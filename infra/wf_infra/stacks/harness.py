"""Harness (DD §17, §18.1): the web page on S3 + CloudFront and the harness API Lambda.

CloudFront serves both from one domain: ``/converse``, ``/api/*``, ``/oauth/*`` and
``/healthz`` go to the API, everything else to the bucket. The session cookie
therefore stays first-party, as it is locally.
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
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from aws_cdk import aws_secretsmanager as sm
from constructs import Construct

from ..common import PythonLambda, certificate, hosted_zone
from ..config import EnvConfig

WEB_DIST = Path(__file__).resolve().parents[3] / "services" / "harness" / "web" / "dist"
API_PATHS = ("/converse", "/api/*", "/oauth/*", "/healthz")


class HarnessStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, cfg: EnvConfig, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)
        session = sm.Secret(
            self,
            "SessionSecret",
            secret_name=f"{cfg.prefix}/harness/session",
            generate_secret_string=sm.SecretStringGenerator(exclude_punctuation=True, password_length=48),
        )
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
                "WF_HAIKU_MODEL_ID": cfg.models.haiku,
                "WF_HARNESS_SESSION_SECRET": session.secret_value.unsafe_unwrap(),  # dynamic reference
                "WF_HARNESS_REDIRECT_URI": f"{cfg.harness_url}/oauth/callback",
                "WF_HARNESS_WEB_URL": "/",
            },
        ).function
        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:Converse",
                    "bedrock:ConverseStream",
                ],
                resources=[
                    f"arn:aws:bedrock:*:{cfg.account}:inference-profile/{cfg.models.haiku}",
                    "arn:aws:bedrock:*::foundation-model/*",
                ],
            )
        )
        self.api = apigwv2.HttpApi(self, "Api", api_name=f"{cfg.prefix}-harness")
        self.api.add_routes(
            path="/{proxy+}",
            methods=[apigwv2.HttpMethod.ANY],
            integration=integrations.HttpLambdaIntegration("Integ", fn),
        )

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
        self.distribution = cloudfront.Distribution(
            self,
            "Cdn",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            ),
            additional_behaviors={path: api_behavior for path in API_PATHS},
            default_root_object="index.html",
            domain_names=[cfg.harness_domain],
            certificate=certificate(self, cfg),
            minimum_protocol_version=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
        )
        if (WEB_DIST / "index.html").is_file():
            s3deploy.BucketDeployment(
                self,
                "Deploy",
                sources=[s3deploy.Source.asset(str(WEB_DIST))],
                destination_bucket=bucket,
                distribution=self.distribution,
                distribution_paths=["/index.html"],
            )
        zone = hosted_zone(self, cfg)
        if zone is not None:
            route53.ARecord(
                self,
                "Alias",
                zone=zone,
                record_name=cfg.harness_domain,
                target=route53.RecordTarget.from_alias(targets.CloudFrontTarget(self.distribution)),
            )
        CfnOutput(self, "Url", value=cfg.harness_url)
        CfnOutput(self, "DnsTarget", value=self.distribution.distribution_domain_name)
