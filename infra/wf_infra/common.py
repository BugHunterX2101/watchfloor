"""Shared building blocks: the uv-packaged Python Lambda and its ECS twin, environment presets and
custom domains."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import jsii
from aws_cdk import CfnOutput, Duration, RemovalPolicy
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from aws_cdk import aws_secretsmanager as sm
from aws_cdk import aws_servicediscovery as servicediscovery
from constructs import Construct

from .config import EnvConfig

if TYPE_CHECKING:
    from .stacks.net import NetStack

BUILD_DIR = Path(__file__).resolve().parents[1] / "build"
STUB_CONTEXT = "wf:stubAssets"
NAMESPACE = "Watchfloor"
STUB_IMAGE = "public.ecr.aws/docker/library/python:3.12-slim-bookworm"  # synth-only runs
HTTP_PORT = 8080
# The header the HTTP API sets to its own domain on the way into a container (ADR-025): through a VPC
# link, Host names the integration, so the MCP server's host check reads this one instead.
GATEWAY_HOST_HEADER = "x-wf-host"


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


GEO_PREFIXES = frozenset({"us", "us-gov", "eu", "apac", "jp", "au", "ca", "global"})


def bedrock_arns(cfg: EnvConfig, *model_ids: str) -> list[str]:
    """What a Bedrock invoke grant names (ADR-022).

    An inference profile (``us.anthropic.…``) routes to its model in several Regions, so the grant
    names the profile and the foundation models behind it. A plain model id (``amazon.nova-lite-v1:0``)
    runs on demand in this Region only, so the grant names that one model.
    """
    arns: list[str] = []
    for model_id in model_ids:
        if model_id.split(".", 1)[0] in GEO_PREFIXES:
            arns += [
                f"arn:aws:bedrock:*:{cfg.account}:inference-profile/{model_id}",
                "arn:aws:bedrock:*::foundation-model/*",
            ]
        else:
            arns.append(f"arn:aws:bedrock:{cfg.region}::foundation-model/{model_id}")
    return list(dict.fromkeys(arns))


def base_env(cfg: EnvConfig, service: str) -> dict[str, str]:
    """What every Watchfloor Lambda reads (TD §7.1). Identifiers are fixed strings from config."""
    env = {
        "WF_ENV": cfg.env,
        "WF_ISSUER": cfg.issuer,
        "WF_RESOURCE_URI": cfg.resource_uri,
        "WF_JWKS_URL": f"{cfg.issuer}/jwks.json",
        "WF_MCP_URL": cfg.resource_uri,
        "WF_DEMO_MODE": "true" if cfg.demo_mode else "false",
        "WF_MODELS_ENABLED": "true" if cfg.models_enabled else "false",
        "WF_AGENTS_ENABLED": "true" if cfg.triage_on else "false",
        "POWERTOOLS_SERVICE_NAME": service,
        "POWERTOOLS_METRICS_NAMESPACE": NAMESPACE,
        "LOG_LEVEL": "INFO",
    }
    if cfg.gemini_enabled:  # the model's name only; the key is read where gemini_access grants it
        env["WF_GEMINI_MODEL_ID"] = cfg.gemini_model
        if cfg.gemini_haiku_model:
            env["WF_GEMINI_HAIKU_MODEL_ID"] = cfg.gemini_haiku_model
    return env


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
            reserved_concurrent_executions=reserved_concurrency if cfg.reserve_concurrency else None,
            log_group=log_group,
        )


def container_image(scope: Construct, bundle: str) -> ecs.ContainerImage:
    """The bundle as an arm64 image (scripts/package_lambdas.py writes its Dockerfile), or a public
    stand-in for synth-only runs. CDK builds and pushes it to the bootstrap repository on deploy."""
    path = BUILD_DIR / bundle
    packaged = (path / ".packaged").is_file()
    if packaged and (path / "Dockerfile").is_file():
        return ecs.ContainerImage.from_asset(str(path), platform=ecr_assets.Platform.LINUX_ARM64)
    if scope.node.try_get_context(STUB_CONTEXT) in (True, "true", "1"):
        return ecs.ContainerImage.from_registry(STUB_IMAGE)
    if packaged:  # packaged before bundles carried a Dockerfile
        raise RuntimeError(f"infra/build/{bundle} has no Dockerfile: run `make package` again")
    raise RuntimeError(
        f"infra/build/{bundle} is missing: run `make package`, or synth with -c {STUB_CONTEXT}=true"
    )


def http_health(path: str) -> ecs.HealthCheck:
    """Healthy below 500, like the Lambda Web Adapter's readiness check: the MCP server answers a
    request without the gateway's host header with 421, which still shows it is serving."""
    probe = (
        "import sys, urllib.error, urllib.request\n"
        "try:\n"
        f"    urllib.request.urlopen('http://127.0.0.1:{HTTP_PORT}{path}', timeout=3)\n"
        "except urllib.error.HTTPError as e:\n"
        "    sys.exit(1 if e.code >= 500 else 0)\n"
        "except Exception:\n"
        "    sys.exit(1)\n"
    )
    return ecs.HealthCheck(
        command=["CMD", "python", "-c", probe],
        interval=Duration.seconds(15),
        timeout=Duration.seconds(5),
        retries=3,
        start_period=Duration.seconds(60),
    )


@jsii.implements(iam.IGrantable)
class Container(Construct):
    """One packaged bundle as an arm64 Fargate task: the ECS twin of :class:`PythonLambda` (ADR-025).

    Same base environment and its own log group; grants go to the task role, so ``secret.grant_read``,
    ``add_environment`` and ``add_to_role_policy`` read as they do for a function. ``http`` makes it a
    web service: Uvicorn on 0.0.0.0:8080, trusting the forwarded headers of the API's VPC link (only
    that link and the other services reach the port). :meth:`service` keeps one task running; without
    it the task definition is a one-off job (``scripts/run_task.py``).
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        cfg: EnvConfig,
        name: str,
        bundle: str,
        command: list[str] | None = None,
        cpu: int = 256,
        memory_mib: int = 512,
        environment: dict[str, str] | None = None,
        http: bool = False,
        health_path: str | None = None,
    ) -> None:
        super().__init__(scope, construct_id)
        self.cfg = cfg
        self.name = name
        # Lambda sets the Region for the SDK; say it here too rather than rely on the task's agent.
        env = {**base_env(cfg, name), "AWS_REGION": cfg.region, "AWS_DEFAULT_REGION": cfg.region}
        env.update(environment or {})
        if http:
            env.update(
                {"WF_BIND_HOST": "0.0.0.0", "PORT": str(HTTP_PORT), "FORWARDED_ALLOW_IPS": "*"}  # noqa: S104
            )
        self.task_definition = ecs.FargateTaskDefinition(
            self,
            "Task",
            family=f"{cfg.prefix}-{name}",
            cpu=cpu,
            memory_limit_mib=memory_mib,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )
        self.log_group = logs.LogGroup(
            self, "Logs", retention=logs.RetentionDays.ONE_MONTH, removal_policy=RemovalPolicy.DESTROY
        )
        self.container = self.task_definition.add_container(
            "App",
            container_name=name,
            image=container_image(self, bundle),
            command=command,
            environment=env,
            logging=ecs.LogDrivers.aws_logs(stream_prefix=name, log_group=self.log_group),
            port_mappings=[ecs.PortMapping(container_port=HTTP_PORT)] if http else None,
            health_check=http_health(health_path) if health_path else None,
            stop_timeout=Duration.seconds(120),  # Fargate's maximum: room to finish in-flight work
        )

    @property
    def grant_principal(self) -> iam.IPrincipal:
        return self.task_definition.task_role

    def add_environment(self, key: str, value: str) -> None:
        self.container.add_environment(key, value)

    def add_to_role_policy(self, statement: iam.PolicyStatement) -> None:
        self.task_definition.add_to_task_role_policy(statement)

    def add_secret_env(self, key: str, secret: sm.ISecret) -> None:
        """A secret ECS injects at start (the execution role reads it), never written into the task
        definition, where a function's dynamic reference would be resolved into plain text."""
        self.container.add_secret(key, ecs.Secret.from_secrets_manager(secret))

    def service(
        self, net: NetStack, *, discovery: str | None = None, srv: bool = False, rolling: bool = True
    ) -> ecs.FargateService:
        """Keep one task running in the private subnets, registered in Cloud Map as ``discovery``
        (SRV records for an HTTP API's integration, A records for a service-to-service call).

        ``rolling``: start the new task before stopping the old (no gap). A single consumer of the
        queues and the schedules sets it False, so two never run the watcher at once.
        """
        assert net.cluster is not None and net.namespace is not None  # NetStack builds them on ECS
        cloud_map = None
        if discovery:
            cloud_map = ecs.CloudMapOptions(
                name=discovery,
                cloud_map_namespace=net.namespace,
                dns_record_type=(
                    servicediscovery.DnsRecordType.SRV if srv else servicediscovery.DnsRecordType.A
                ),
                dns_ttl=Duration.seconds(10),
                container=self.container if srv else None,
                container_port=HTTP_PORT if srv else None,
            )
        return ecs.FargateService(
            self,
            "Service",
            cluster=net.cluster,
            task_definition=self.task_definition,
            service_name=f"{self.cfg.prefix}-{self.name}",
            desired_count=1,
            min_healthy_percent=100 if rolling else 0,
            max_healthy_percent=200 if rolling else 100,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            security_groups=[net.lambda_sg],
            assign_public_ip=False,
            circuit_breaker=ecs.DeploymentCircuitBreaker(enable=True, rollback=True),
            cloud_map_options=cloud_map,
            propagate_tags=ecs.PropagatedTagSource.SERVICE,
        )


def service_integration(service: ecs.FargateService, net: NetStack) -> apigwv2.HttpRouteIntegration:
    """An HTTP API's way into a container (ADR-025): its VPC link to the service's Cloud Map SRV
    records. The API's own domain goes along in ``x-wf-host``, overwriting whatever a client sent."""
    assert service.cloud_map_service is not None and net.vpc_link is not None
    return integrations.HttpServiceDiscoveryIntegration(
        "Integ",
        service.cloud_map_service,
        vpc_link=net.vpc_link,
        parameter_mapping=apigwv2.ParameterMapping().overwrite_header(
            GATEWAY_HOST_HEADER, apigwv2.MappingValue.context_variable("domainName")
        ),
    )


def certificate(scope: Construct, cfg: EnvConfig) -> acm.ICertificate:
    assert cfg.certificate_arn is not None  # callers check cfg.custom_domains
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


def domain_mapping(scope: Construct, cfg: EnvConfig, host: str | None) -> apigwv2.DomainMappingOptions | None:
    """The custom domain of an HTTP API, or None (no certificate_arn): it answers on its execute-api host."""
    if not cfg.custom_domains:
        return None
    assert host is not None  # EnvConfig requires all three host names with a certificate
    return apigwv2.DomainMappingOptions(domain_name=api_domain(scope, cfg, host))


def throttle(api: apigwv2.HttpApi, *, rate: int, burst: int) -> None:
    """A stage-wide ceiling in requests per second (DD §18.1), so a flood is answered 429 by API Gateway
    before it costs a container, a database connection or a model call."""
    stage = api.default_stage.node.default_child  # escape hatch: the L2 construct has no throttling option
    assert isinstance(stage, apigwv2.CfnStage)
    stage.default_route_settings = apigwv2.CfnStage.RouteSettingsProperty(
        throttling_burst_limit=burst, throttling_rate_limit=rate
    )


def default_host_output(scope: Construct, cfg: EnvConfig, api: apigwv2.HttpApi) -> None:
    """The host AWS assigned (scripts/default_hosts.py reads it into the config when there is no domain)."""
    CfnOutput(scope, "DefaultHost", value=f"{api.api_id}.execute-api.{cfg.region}.amazonaws.com")


def gemini_access(scope: Construct, cfg: EnvConfig, fn: lambda_.Function | Container) -> None:
    """Gemini for one function (ADR-023): the key's secret name and read access to it.

    The secret is stored by hand, so it is referenced by name rather than created here (once per
    stack). The model id is in every function's base environment.
    """
    secret = cast(sm.ISecret | None, scope.node.try_find_child("GeminiKey")) or sm.Secret.from_secret_name_v2(
        scope, "GeminiKey", cfg.gemini_secret_name
    )
    fn.add_environment("WF_GEMINI_SECRET", cfg.gemini_secret_name)
    secret.grant_read(fn)
