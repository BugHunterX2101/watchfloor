"""Harness (DD §17, §18.1): the web page on S3 + CloudFront and the harness API Lambda.

CloudFront serves both from one domain: ``/converse``, ``/api/*``, ``/oauth/*`` and
``/healthz`` go to the API, everything else to the bucket. The session cookie
therefore stays first-party, as it is locally.

With ``cdn: false`` (an account AWS has not verified for CloudFront yet, ADR-015) there is no
bucket or distribution: the Lambda serves the page it was packaged with, as it does locally, on
the API's own host. With ``compute: ecs`` (ADR-025, which implies ``cdn: false``) a Fargate
service does the same behind the same HTTP API.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aws_cdk import CfnOutput, Duration, Fn, RemovalPolicy, Stack
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from aws_cdk import aws_secretsmanager as sm
from constructs import Construct

from ..common import (
    Container,
    PythonLambda,
    bedrock_arns,
    certificate,
    default_host_output,
    domain_mapping,
    gemini_access,
    hosted_zone,
    service_integration,
    throttle,
)
from ..config import EnvConfig
from .net import NetStack

WEB_DIST = Path(__file__).resolve().parents[3] / "services" / "harness" / "web" / "dist"
API_PATHS = ("/converse", "/api/*", "/oauth/*", "/healthz")
PACKAGED_WEB_DIST = "/var/task/harness_web"  # scripts/package_lambdas.py copies the page there

# The harness API sends these on everything it serves (harness_api/headers.py). Behind CloudFront the page
# and its assets come from S3 instead, so the distribution adds the same ones; a test keeps the copies equal.
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; manifest-src 'self'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'; object-src 'none'"
)
PERMISSIONS_POLICY = "microphone=(self), camera=(), geolocation=(), payment=(), usb=(), interest-cohort=()"


class HarnessStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        cfg: EnvConfig,
        net: NetStack | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        session = sm.Secret(
            self,
            "SessionSecret",
            secret_name=f"{cfg.prefix}/harness/session",
            generate_secret_string=sm.SecretStringGenerator(exclude_punctuation=True, password_length=48),
        )
        env = {
            "WF_HARNESS_REDIRECT_URI": f"{cfg.harness_url}/oauth/callback",
            "WF_HARNESS_WEB_URL": "/",
        }
        fn: lambda_.Function | Container
        if cfg.ecs:  # ADR-025: the container serves its packaged page, as the Lambda does without a CDN
            assert net is not None, "compute: ecs runs the harness in the VPC (pass net)"
            fn = Container(
                self,
                "Server",
                cfg=cfg,
                name="harness-api",
                bundle="harness_api",
                environment=env,  # WF_HARNESS_WEB_DIST below: compute: ecs implies cdn: false
                http=True,
                health_path="/healthz",
            )
            fn.add_secret_env("WF_HARNESS_SESSION_SECRET", session)
        else:
            fn = PythonLambda(
                self,
                "Fn",
                cfg=cfg,
                name="harness-api",
                bundle="harness_api",
                web_adapter=True,
                memory_size=512,
                timeout=Duration.seconds(60),
                environment={
                    "WF_HARNESS_SESSION_SECRET": session.secret_value.unsafe_unwrap(),  # dynamic reference
                    **env,
                },
            ).function
        if cfg.models_enabled:  # otherwise the harness runs its offline engine (WF_MODELS_ENABLED=false)
            fn.add_environment("WF_HAIKU_MODEL_ID", cfg.models.haiku)
            fn.add_to_role_policy(
                iam.PolicyStatement(
                    actions=[
                        "bedrock:InvokeModel",
                        "bedrock:InvokeModelWithResponseStream",
                        "bedrock:Converse",
                        "bedrock:ConverseStream",
                    ],
                    resources=bedrock_arns(cfg, cfg.models.haiku),
                )
            )
        if cfg.gemini_enabled:  # after Bedrock, or alone where models are off (ADR-023)
            gemini_access(self, cfg, fn)
        integration: apigwv2.HttpRouteIntegration
        if isinstance(fn, Container):
            assert net is not None
            integration = service_integration(fn.service(net, discovery="harness", srv=True), net)
        else:
            integration = integrations.HttpLambdaIntegration("Integ", fn)
        self.api = apigwv2.HttpApi(
            self,
            "Api",
            api_name=f"{cfg.prefix}-harness",
            default_domain_mapping=None if cfg.cdn else domain_mapping(self, cfg, cfg.harness_host),
        )
        self.api.add_routes(path="/{proxy+}", methods=[apigwv2.HttpMethod.ANY], integration=integration)
        throttle(self.api, rate=50, burst=100)  # a page load is about a dozen requests
        self.distribution: cloudfront.Distribution | None = None
        if cfg.cdn:
            self.distribution = self._cdn(cfg)
        else:
            fn.add_environment("WF_HARNESS_WEB_DIST", PACKAGED_WEB_DIST)
            self.api.add_routes(path="/", methods=[apigwv2.HttpMethod.GET], integration=integration)
            if not cfg.custom_domains:
                default_host_output(self, cfg, self.api)  # scripts/default_hosts.py
        CfnOutput(self, "Url", value=cfg.harness_url)

    def _cdn(self, cfg: EnvConfig) -> cloudfront.Distribution:
        bucket = s3.Bucket(
            self,
            "Site",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )
        api_origin = origins.HttpOrigin(Fn.select(2, Fn.split("/", self.api.api_endpoint)))
        api_behavior = cloudfront.BehaviorOptions(
            origin=api_origin,
            allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
            cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
            origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        )
        page_headers = cloudfront.ResponseHeadersPolicy(
            self,
            "PageHeaders",
            security_headers_behavior=cloudfront.ResponseSecurityHeadersBehavior(
                content_security_policy=cloudfront.ResponseHeadersContentSecurityPolicy(
                    content_security_policy=CSP, override=True
                ),
                content_type_options=cloudfront.ResponseHeadersContentTypeOptions(override=True),
                frame_options=cloudfront.ResponseHeadersFrameOptions(
                    frame_option=cloudfront.HeadersFrameOption.DENY, override=True
                ),
                referrer_policy=cloudfront.ResponseHeadersReferrerPolicy(
                    referrer_policy=cloudfront.HeadersReferrerPolicy.NO_REFERRER, override=True
                ),
                strict_transport_security=cloudfront.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=Duration.days(365), override=True
                ),
            ),
            custom_headers_behavior=cloudfront.ResponseCustomHeadersBehavior(
                custom_headers=[
                    cloudfront.ResponseCustomHeader(
                        header="Permissions-Policy", value=PERMISSIONS_POLICY, override=True
                    ),
                    cloudfront.ResponseCustomHeader(
                        header="Cross-Origin-Opener-Policy", value="same-origin", override=True
                    ),
                ]
            ),
        )
        distribution = cloudfront.Distribution(
            self,
            "Cdn",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                response_headers_policy=page_headers,
            ),
            additional_behaviors={path: api_behavior for path in API_PATHS},
            default_root_object="index.html",
            # Without custom domains it answers on its cloudfront.net host, under CloudFront's certificate.
            domain_names=[cfg.harness_host] if cfg.custom_domains else None,
            certificate=certificate(self, cfg) if cfg.custom_domains else None,
            minimum_protocol_version=(
                cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021 if cfg.custom_domains else None
            ),
        )
        if (WEB_DIST / "index.html").is_file():
            s3deploy.BucketDeployment(
                self,
                "Deploy",
                sources=[s3deploy.Source.asset(str(WEB_DIST))],
                destination_bucket=bucket,
                distribution=distribution,
                distribution_paths=["/index.html"],
            )
        zone = hosted_zone(self, cfg)
        if zone is not None:
            route53.ARecord(
                self,
                "Alias",
                zone=zone,
                record_name=cfg.harness_host,
                target=route53.RecordTarget.from_alias(targets.CloudFrontTarget(distribution)),
            )
        CfnOutput(self, "DnsTarget", value=distribution.distribution_domain_name)
        CfnOutput(
            self, "DefaultHost", value=distribution.distribution_domain_name
        )  # scripts/default_hosts.py
        return distribution
