"""Shared building blocks: the uv-packaged Python Lambda, environment presets and custom domains."""

from __future__ import annotations

from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from constructs import Construct

from .config import EnvConfig

BUILD_DIR = Path(__file__).resolve().parents[1] / "build"
STUB_CONTEXT = "wf:stubAssets"
NAMESPACE = "Watchfloor"


def lambda_code(scope: Construct, bundle: str) -> lambda_.Code:
    """The zip built by scripts/package_lambdas.py (TD §10.1), or an inline stub for synth-only runs."""
    path = BUILD_DIR / bundle
    if (path / ".packaged").is_file():
        return lambda_.Code.from_asset(str(path))
    if scope.node.try_get_context(STUB_CONTEXT) in (True, "true", "1"):
        return lambda_.Code.from_inline(
            "def handler(event, context):\n    raise RuntimeError('stub asset')\n"
        )
    raise RuntimeError(
        f"infra/build/{bundle} is missing: run `make package`, or synth with -c {STUB_CONTEXT}=true"
    )


def base_env(cfg: EnvConfig, service: str) -> dict[str, str]:
    """What every Watchfloor Lambda reads (TD §7.1). Identifiers are fixed strings from config."""
    return {
        "WF_ENV": cfg.env,
        "WF_ISSUER": cfg.issuer,
        "WF_RESOURCE_URI": cfg.resource_uri,
        "WF_JWKS_URL": f"{cfg.issuer}/jwks.json",
        "WF_MCP_URL": cfg.resource_uri,
        "WF_DEMO_MODE": "true" if cfg.demo_mode else "false",
        "POWERTOOLS_SERVICE_NAME": service,
        "POWERTOOLS_METRICS_NAMESPACE": NAMESPACE,
        "LOG_LEVEL": "INFO",
    }


def lwa_env() -> dict[str, str]:
    """Lambda Web Adapter (TD §10.2): Uvicorn behind /opt/bootstrap, readiness on /healthz."""
    return {
        "AWS_LAMBDA_EXEC_WRAPPER": "/opt/bootstrap",
        "AWS_LWA_PORT": "8080",
        "AWS_LWA_READINESS_CHECK_PATH": "/healthz",
        "PORT": "8080",
    }


class PythonLambda(Construct):
    """arm64 Python 3.12 Lambda from a uv-built zip, with its own log group and X-Ray tracing."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        cfg: EnvConfig,
        name: str,
        bundle: str,
        handler: str = "run.sh",
        web_adapter: bool = False,
        memory_size: int = 512,
        timeout: Duration | None = None,
        environment: dict[str, str] | None = None,
        vpc: ec2.IVpc | None = None,
        security_groups: list[ec2.ISecurityGroup] | None = None,
        reserved_concurrency: int | None = None,
    ) -> None:
        super().__init__(scope, construct_id)
        env = {**base_env(cfg, name), **(environment or {})}
        layers: list[lambda_.ILayerVersion] = []
        if web_adapter:
            env.update(lwa_env())
            layers.append(lambda_.LayerVersion.from_layer_version_arn(self, "Lwa", cfg.lwa_layer_arn))
            handler = "run.sh"
        log_group = logs.LogGroup(
            self, "Logs", retention=logs.RetentionDays.ONE_MONTH, removal_policy=RemovalPolicy.DESTROY
        )
        self.function = lambda_.Function(
            self,
            "Fn",
            function_name=f"{cfg.prefix}-{name}",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler=handler,
            code=lambda_code(self, bundle),
            memory_size=memory_size,
            timeout=timeout or Duration.seconds(30),
            environment=env,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS) if vpc else None,
            security_groups=security_groups,
            layers=layers,
            tracing=lambda_.Tracing.ACTIVE,
            reserved_concurrent_executions=reserved_concurrency,
            log_group=log_group,
        )


def certificate(scope: Construct, cfg: EnvConfig) -> acm.ICertificate:
    return acm.Certificate.from_certificate_arn(scope, "Certificate", cfg.certificate_arn)


def hosted_zone(scope: Construct, cfg: EnvConfig) -> route53.IHostedZone | None:
    if not (cfg.hosted_zone_id and cfg.hosted_zone_name):
        return None
    return route53.HostedZone.from_hosted_zone_attributes(
        scope, "Zone", hosted_zone_id=cfg.hosted_zone_id, zone_name=cfg.hosted_zone_name
    )


def api_domain(scope: Construct, cfg: EnvConfig, host: str) -> apigwv2.DomainName:
    """A regional custom domain for an HTTP API, plus its DNS alias (or an output to create it by hand)."""
    domain = apigwv2.DomainName(scope, "Domain", domain_name=host, certificate=certificate(scope, cfg))
    zone = hosted_zone(scope, cfg)
    if zone is not None:
        route53.ARecord(
            scope,
            "DomainAlias",
            zone=zone,
            record_name=host,
            target=route53.RecordTarget.from_alias(
                targets.ApiGatewayv2DomainProperties(
                    domain.regional_domain_name, domain.regional_hosted_zone_id
                )
            ),
        )
    CfnOutput(
        scope, "DnsTarget", value=domain.regional_domain_name, description=f"CNAME or alias target for {host}"
    )
    return domain
