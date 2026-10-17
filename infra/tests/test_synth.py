"""Synth both environments with stub assets and check the properties the specs pin down."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import app as infra_app
import aws_cdk as cdk
import pytest
import yaml
from aws_cdk.assertions import Match, Template
from wf_infra import common
from wf_infra.config import CONFIG_DIR, PENDING_HOST, EnvConfig

FIXTURES = Path(__file__).resolve().parent / "config"  # the full design; infra/config is the shipped one


@pytest.fixture(scope="module", autouse=True)
def _no_packaged_bundles(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Ignore infra/build: a local `make package` would otherwise swap the stubs for ~190 MB of assets."""
    with pytest.MonkeyPatch.context() as mp:
        empty = tmp_path_factory.mktemp("build")
        mp.setattr(common, "BUILD_DIR", empty)
        yield empty


def _synth(env: str, **context: Any) -> dict[str, Template]:
    application = cdk.App(
        context={
            "env": env,
            "account": "123456789012",
            "wf:stubAssets": "true",
            "wf:configDir": str(FIXTURES),
            **context,
        }
    )
    stacks = infra_app.build(application)
    return {name.split("-", 2)[2]: Template.from_stack(stack) for name, stack in stacks.items()}


@pytest.fixture(scope="module")
def dev() -> dict[str, Template]:
    return _synth("dev", **{"wf:modelsEnabled": "true"})  # the full design; the configs may switch it off


def test_every_stack_synthesizes_for_both_environments(dev: dict[str, Template]) -> None:
    assert set(dev) == {"net", "data", "auth", "mcp", "agents", "async", "harness", "ops"}
    demo = _synth("demo", **{"wf:modelsEnabled": "true"})
    demo["data"].has_resource_properties("AWS::RDS::DBInstance", {"DeletionProtection": True})
    dev["data"].has_resource_properties("AWS::RDS::DBInstance", {"DeletionProtection": False})


def test_database_is_private_encrypted_and_forces_tls(dev: dict[str, Template]) -> None:
    data = dev["data"]
    data.has_resource_properties(
        "AWS::RDS::DBInstance",
        {
            "Engine": "postgres",
            "StorageEncrypted": True,
            "PubliclyAccessible": False,
            "DBInstanceClass": "db.t4g.micro",
        },
    )
    data.has_resource_properties("AWS::RDS::DBParameterGroup", {"Parameters": {"rds.force_ssl": "1"}})
    data.resource_count_is("AWS::SecretsManager::SecretTargetAttachment", 3)  # owner, app, auth


def test_mcp_lambda_alias_and_routes(dev: dict[str, Template]) -> None:
    mcp = dev["mcp"]
    mcp.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "FunctionName": "wf-dev-mcp-server",
            "Architectures": ["arm64"],
            "Handler": "run.sh",
            "MemorySize": 1024,
            "Environment": {
                "Variables": Match.object_like(
                    {
                        "WF_RESOURCE_URI": "https://mcp.dev.watchfloor.example/mcp",
                        "WF_ISSUER": "https://auth.dev.watchfloor.example",
                        "WF_JOBS_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123456789012/wf-dev-jobs",
                        "WF_RATE_LIMIT_USER": "60",
                        "WF_RATE_LIMIT_WS": "600",
                        "AWS_LAMBDA_EXEC_WRAPPER": "/opt/bootstrap",
                    }
                )
            },
        },
    )
    mcp.has_resource_properties(
        "AWS::Lambda::Alias",
        {"Name": "live", "ProvisionedConcurrencyConfig": {"ProvisionedConcurrentExecutions": 1}},
    )
    routes = {r["Properties"]["RouteKey"] for r in mcp.find_resources("AWS::ApiGatewayV2::Route").values()}
    assert {"POST /mcp", "GET /mcp", "DELETE /mcp", "GET /.well-known/{proxy+}", "GET /healthz"} <= routes
    mcp.has_resource_properties(
        "AWS::ApiGatewayV2::Stage",
        {"DefaultRouteSettings": {"ThrottlingBurstLimit": 50, "ThrottlingRateLimit": 25}},
    )


def test_signing_key_is_es256_in_kms(dev: dict[str, Template]) -> None:
    dev["auth"].has_resource_properties(
        "AWS::KMS::Key", {"KeySpec": "ECC_NIST_P256", "KeyUsage": "SIGN_VERIFY"}
    )


def test_seeder_reads_generated_secrets_only(dev: dict[str, Template]) -> None:
    auth = dev["auth"]
    auth.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "FunctionName": "wf-dev-seeder",
            "Handler": "auth_server.seeder.handler",
            "VpcConfig": Match.any_value(),
            "Environment": {
                "Variables": Match.object_like(
                    {"WF_HARNESS_REDIRECT_URI": "https://harness.dev.watchfloor.example/oauth/callback"}
                )
            },
        },
    )
    generated = {
        r["Properties"]["Name"]: r["Properties"]["GenerateSecretString"]
        for r in auth.find_resources("AWS::SecretsManager::Secret").values()
        if "GenerateSecretString" in r["Properties"]
    }
    assert generated["wf-dev/auth/alexa-client"]["GenerateStringKey"] == "client_secret"
    assert generated["wf-dev/auth/dispatcher-client"]["GenerateStringKey"] == "client_secret"
    assert generated["wf-dev/auth/demo-login"]["GenerateStringKey"] == "password"
    assert (
        generated["wf-dev/canary/login"]["SecretStringTemplate"] == '{"email": "daniel@lumenridge.example"}'
    )
    # Async and Ops reuse Auth's secrets instead of creating empty ones.
    for stack in ("async", "ops"):
        names = {
            r["Properties"].get("Name")
            for r in dev[stack].find_resources("AWS::SecretsManager::Secret").values()
        }
        assert not names & {"wf-dev/auth/dispatcher-client", "wf-dev/canary/login"}


def test_queue_timeouts_exceed_the_dispatcher(dev: dict[str, Template]) -> None:
    a = dev["async"]
    a.has_resource_properties("AWS::SQS::Queue", {"QueueName": "wf-dev-jobs", "VisibilityTimeout": 180})
    a.has_resource_properties("AWS::SQS::Queue", {"QueueName": "wf-dev-outbox", "VisibilityTimeout": 130})
    a.has_resource_properties("AWS::Lambda::Function", {"FunctionName": "wf-dev-dispatcher", "Timeout": 120})
    a.has_resource_properties(
        "AWS::Lambda::EventSourceMapping",
        {"BatchSize": 1, "FunctionResponseTypes": ["ReportBatchItemFailures"]},
    )


def test_agent_runtime_role_has_no_database_or_vpc(dev: dict[str, Template]) -> None:
    agents = dev["agents"]
    agents.has_resource_properties(
        "AWS::BedrockAgentCore::Runtime",
        {"AgentRuntimeName": "wf_dev_triage", "NetworkConfiguration": {"NetworkMode": "PUBLIC"}},
    )
    agents.has_resource_properties(
        "AWS::BedrockAgentCore::Memory",
        {
            "MemoryStrategies": Match.array_with(
                [
                    Match.object_like(
                        {
                            "SemanticMemoryStrategy": Match.object_like(
                                {"Namespaces": ["/actors/{actorId}/facts"]}
                            )
                        }
                    )
                ]
            )
        },
    )
    policies = str(agents.find_resources("AWS::IAM::Policy"))
    assert "secretsmanager" not in policies and "ec2:" not in policies


def test_first_deploy_can_skip_the_runtime() -> None:
    bare = _synth("dev", **{"wf:agentRuntime": "false", "wf:modelsEnabled": "true"})
    bare["agents"].resource_count_is("AWS::BedrockAgentCore::Runtime", 0)
    assert "WF_AGENT_RUNTIME_ARN" not in str(bare["async"].find_resources("AWS::Lambda::Function"))


def test_harness_serves_api_paths_through_cloudfront(dev: dict[str, Template]) -> None:
    h = dev["harness"]
    dist = next(iter(h.find_resources("AWS::CloudFront::Distribution").values()))["Properties"][
        "DistributionConfig"
    ]
    assert {b["PathPattern"] for b in dist["CacheBehaviors"]} == {
        "/converse",
        "/api/*",
        "/oauth/*",
        "/healthz",
    }
    assert dist["Aliases"] == ["harness.dev.watchfloor.example"]


def test_every_api_has_a_stage_throttle(dev: dict[str, Template]) -> None:
    """A flood is answered 429 by API Gateway, before it costs a container, a connection or a model call."""
    for stack, rate, burst in (("mcp", 25, 50), ("auth", 20, 40), ("harness", 50, 100)):
        dev[stack].has_resource_properties(
            "AWS::ApiGatewayV2::Stage",
            {"DefaultRouteSettings": {"ThrottlingBurstLimit": burst, "ThrottlingRateLimit": rate}},
        )


def _harness_api_headers() -> dict[str, str]:
    """CSP and Permissions-Policy as services/harness/api/harness_api/headers.py defines them (read, not imported:
    this project does not install the harness's dependencies)."""
    import ast

    source = (
        Path(__file__).resolve().parents[2] / "services" / "harness" / "api" / "harness_api" / "headers.py"
    )
    found: dict[str, str] = {}
    for node in ast.parse(source.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name == "CSP" and isinstance(node.value, ast.Call):
                found[name] = "; ".join(ast.literal_eval(node.value.args[0]))
            elif name == "PERMISSIONS":
                found[name] = ast.literal_eval(node.value)
    return found


def test_cloudfront_sends_the_headers_the_harness_api_sends(dev: dict[str, Template]) -> None:
    from wf_infra.stacks import harness

    api = _harness_api_headers()
    assert api["CSP"] == harness.CSP and api["PERMISSIONS"] == harness.PERMISSIONS_POLICY
    dev["harness"].has_resource_properties(
        "AWS::CloudFront::ResponseHeadersPolicy",
        {
            "ResponseHeadersPolicyConfig": Match.object_like(
                {
                    "SecurityHeadersConfig": Match.object_like(
                        {
                            "ContentSecurityPolicy": {"ContentSecurityPolicy": api["CSP"], "Override": True},
                            "FrameOptions": {"FrameOption": "DENY", "Override": True},
                            "ContentTypeOptions": {"Override": True},
                        }
                    ),
                    "CustomHeadersConfig": {
                        "Items": Match.array_with(
                            [{"Header": "Permissions-Policy", "Value": api["PERMISSIONS"], "Override": True}]
                        )
                    },
                }
            )
        },
    )


def test_alarms_cover_the_td_list(dev: dict[str, Template]) -> None:
    names = {
        r["Properties"]["AlarmName"] for r in dev["ops"].find_resources("AWS::CloudWatch::Alarm").values()
    }
    expected = {"5xx", "spillover", "canary", "jobs-dlq", "outbox-dlq", "outbox-dead", "triage-fail"}
    assert {f"wf-dev-{n}" for n in expected} <= names
    assert any(n.startswith("wf-dev-hot-p95") for n in names)
    dev["ops"].resource_count_is("AWS::Budgets::Budget", 1)
    dev["ops"].has_resource_properties(  # usage before credits, or a credits-funded account reads $0
        "AWS::Budgets::Budget",
        {"Budget": Match.object_like({"CostTypes": {"IncludeCredit": False, "IncludeRefund": False}})},
    )
    assert "AWS::Budgets::Budget" not in str(_synth("demo")["ops"].to_json())


def test_missing_assets_fail_loudly() -> None:
    application = cdk.App(context={"env": "dev", "account": "123456789012"})
    with pytest.raises(RuntimeError, match="make package"):
        infra_app.build(application)


def test_without_models_nothing_needs_agentcore_or_claude() -> None:
    """A Free plan account (models_enabled: false): no Agents stack, no Bedrock grants, every Lambda told."""
    off = _synth("dev", **{"wf:modelsEnabled": "false"})
    assert set(off) == {"net", "data", "auth", "mcp", "async", "harness", "ops"}
    for name in ("async", "harness"):
        text = json.dumps(off[name].to_json())
        assert (
            "bedrock" not in text and "WF_HAIKU_MODEL_ID" not in text and "WF_AGENT_RUNTIME_ARN" not in text
        )
    off["mcp"].has_resource_properties(
        "AWS::Lambda::Function",
        {"Environment": {"Variables": Match.object_like({"WF_MODELS_ENABLED": "false"})}},
    )
    assert "AWS/Bedrock" not in json.dumps(off["ops"].to_json())


def test_models_without_agents_run_nova_and_skip_agentcore() -> None:
    """A Free plan account on Nova (ADR-022): Bedrock grants for one in-Region model, no Agents stack."""
    on = _synth(
        "dev",
        **{
            "wf:modelsEnabled": "true",
            "wf:agentsEnabled": "false",
            "wf:configDir": str(CONFIG_DIR),
            "wf:compute": "lambda",
        },
    )
    assert "agents" not in on
    nova = "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0"
    for name in ("async", "harness"):
        text = json.dumps(on[name].to_json())
        assert nova in text and "foundation-model/*" not in text and "inference-profile" not in text
        assert "WF_MEMORY_ID" not in text and "WF_AGENT_RUNTIME_ARN" not in text
    on["async"].has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Environment": {
                "Variables": Match.object_like(
                    {
                        "WF_HAIKU_MODEL_ID": "amazon.nova-lite-v1:0",
                        "WF_MODELS_ENABLED": "true",
                        "WF_AGENTS_ENABLED": "false",
                    }
                )
            }
        },
    )
    on["ops"].has_resource_properties(
        "AWS::CloudWatch::Alarm",
        {
            "AlarmName": "wf-dev-bedrock-throttles-fast",
            "Namespace": "AWS/Bedrock",
            "MetricName": "InvocationThrottles",
            "Dimensions": [{"Name": "ModelId", "Value": "amazon.nova-lite-v1:0"}],
        },
    )
    assert "bedrock-throttles-reasoning" not in json.dumps(on["ops"].to_json())


def test_gemini_runs_where_bedrock_cannot() -> None:
    """ADR-023, the shipped dev config on Lambda: models off, Gemini on. Both callers read the key, no
    Bedrock grant. (On ECS, test_ecs_*.)"""
    shipped = _synth("dev", **{"wf:configDir": str(CONFIG_DIR), "wf:compute": "lambda"})
    gemini_env = {
        "WF_GEMINI_MODEL_ID": "gemini-flash-lite-latest",
        "WF_GEMINI_SECRET": "wf-dev/models/gemini",
    }
    for name in ("async", "harness"):
        text = json.dumps(shipped[name].to_json())
        assert "bedrock" not in text and "secret:wf-dev/models/gemini-??????" in text
        shipped[name].has_resource_properties(
            "AWS::Lambda::Function", {"Environment": {"Variables": Match.object_like(gemini_env)}}
        )
    assert "GEMINI" not in json.dumps(_synth("dev")["harness"].to_json())  # off unless the config says so


@pytest.mark.parametrize("env", ["dev", "demo"])
def test_the_shipped_configs_synthesize(env: str) -> None:
    stacks = _synth(env, **{"wf:configDir": str(CONFIG_DIR)})
    assert {"net", "data", "auth", "mcp", "async", "harness", "ops"} <= set(stacks)


def test_without_custom_domains_the_services_answer_on_the_hosts_aws_assigns() -> None:
    """No certificate_arn: no custom domains or DNS records, a DefaultHost output per entry point, and
    pending.invalid in every URL until scripts/default_hosts.py writes the assigned hosts in."""
    free = _synth("dev", **{"wf:configDir": str(FIXTURES / "free")})
    for name in ("auth", "mcp", "harness"):
        free[name].resource_count_is("AWS::ApiGatewayV2::DomainName", 0)
        free[name].resource_count_is("AWS::Route53::RecordSet", 0)
        assert "DefaultHost" in free[name].to_json()["Outputs"]
    dist = next(iter(free["harness"].find_resources("AWS::CloudFront::Distribution").values()))
    assert "Aliases" not in dist["Properties"]["DistributionConfig"]
    free["mcp"].has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Environment": {
                "Variables": Match.object_like(
                    {
                        "WF_ISSUER": f"https://{PENDING_HOST}",
                        "WF_RESOURCE_URI": f"https://{PENDING_HOST}/mcp",
                        "WF_ALLOWED_HOSTS": PENDING_HOST,
                    }
                )
            }
        },
    )
    alias = next(iter(free["mcp"].find_resources("AWS::Lambda::Alias").values()))
    assert "ProvisionedConcurrencyConfig" not in alias["Properties"]


def test_without_cloudfront_the_harness_lambda_serves_its_page(tmp_path: Path) -> None:
    """cdn: false (ADR-015): no bucket or distribution; the Lambda serves the packaged page on its API host."""
    text = (FIXTURES / "free" / "dev.yaml").read_text(encoding="utf-8")
    (tmp_path / "dev.yaml").write_text(text + "\ncdn: false\n", encoding="utf-8")
    h = _synth("dev", **{"wf:configDir": str(tmp_path)})["harness"]
    h.resource_count_is("AWS::CloudFront::Distribution", 0)
    h.resource_count_is("AWS::S3::Bucket", 0)
    h.has_resource_properties(
        "AWS::Lambda::Function",
        {"Environment": {"Variables": Match.object_like({"WF_HARNESS_WEB_DIST": "/var/task/harness_web"})}},
    )
    h.has_resource_properties("AWS::ApiGatewayV2::Route", {"RouteKey": "GET /"})
    assert "DefaultHost" in h.to_json()["Outputs"]


def test_without_reserved_concurrency_no_function_reserves_any(tmp_path: Path) -> None:
    """reserve_concurrency: false (ADR-016): a 10-execution account quota leaves nothing to reserve."""
    text = (FIXTURES / "free" / "dev.yaml").read_text(encoding="utf-8")
    (tmp_path / "dev.yaml").write_text(text + "\nreserve_concurrency: false\n", encoding="utf-8")
    stacks = _synth("dev", **{"wf:configDir": str(tmp_path)})
    assert "ReservedConcurrentExecutions" not in json.dumps([t.to_json() for t in stacks.values()])


def test_by_default_the_mcp_server_and_dispatcher_reserve_concurrency(dev: dict[str, Template]) -> None:
    dev["mcp"].has_resource_properties("AWS::Lambda::Function", {"ReservedConcurrentExecutions": 20})
    dev["async"].has_resource_properties("AWS::Lambda::Function", {"ReservedConcurrentExecutions": 3})


def test_deep_triage_runs_on_lambda_where_agentcore_cannot() -> None:
    """ADR-023, the shipped dev config on Lambda: the agents are a function outside the VPC that only
    the dispatcher may invoke, on Gemini. There is no Agents stack, and deep triage is offered."""
    shipped = _synth("dev", **{"wf:configDir": str(CONFIG_DIR), "wf:compute": "lambda"})
    assert "agents" not in shipped
    triage = shipped["async"].find_resources(
        "AWS::Lambda::Function", {"Properties": {"FunctionName": "wf-dev-triage"}}
    )
    (props,) = (r["Properties"] for r in triage.values())
    assert (
        props["Handler"] == "agents.handler.handler" and props["Timeout"] == 95 and "VpcConfig" not in props
    )
    assert props["Environment"]["Variables"]["WF_GEMINI_SECRET"] == "wf-dev/models/gemini"
    shipped["async"].has_resource_properties(
        "AWS::Lambda::Function",
        {
            "FunctionName": "wf-dev-dispatcher",
            "Environment": {"Variables": Match.object_like({"WF_AGENT_FUNCTION": Match.any_value()})},
        },
    )
    assert "lambda:InvokeFunction" in json.dumps(shipped["async"].to_json())
    shipped["mcp"].has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Environment": {
                "Variables": Match.object_like(
                    {"WF_AGENTS_ENABLED": "true", "WF_GEMINI_MODEL_ID": "gemini-flash-lite-latest"}
                )
            }
        },
    )


# ---------------------------------------------------------------- compute: ecs (ADR-025)

ON_LAMBDA = ("AWS::Lambda::", "Custom::", "AWS::CloudFormation::CustomResource")


@pytest.fixture(scope="module")
def ecs() -> dict[str, Template]:
    """The shipped dev config: Lambda refused account-wide, every service on Fargate."""
    return _synth("dev", **{"wf:configDir": str(CONFIG_DIR)})


def _containers(t: Template) -> list[dict[str, Any]]:
    return [
        c
        for td in t.find_resources("AWS::ECS::TaskDefinition").values()
        for c in td["Properties"]["ContainerDefinitions"]
    ]


def _web(t: Template) -> dict[str, Any]:
    (container,) = (c for c in _containers(t) if c.get("PortMappings"))
    return container


def _env(container: dict[str, Any]) -> dict[str, Any]:
    return {e["Name"]: e["Value"] for e in container.get("Environment", [])}


def test_ecs_deploys_nothing_that_runs_on_lambda(ecs: dict[str, Template]) -> None:
    """No function, permission, event source mapping, layer or CDK custom resource: on an account
    that refuses Lambda any of them fails, and fails its stack with it."""
    for name, t in ecs.items():
        types = {r["Type"] for r in t.to_json()["Resources"].values()}
        assert not {x for x in types if x.startswith(ON_LAMBDA)}, name
    ecs["net"].has_resource_properties("AWS::ECS::Cluster", {"ClusterName": "wf-dev"})
    ecs["net"].has_resource_properties(
        "AWS::ServiceDiscovery::PrivateDnsNamespace", {"Name": "wf-dev.internal"}
    )
    ecs["net"].resource_count_is("AWS::ApiGatewayV2::VpcLink", 1)
    assert {"ClusterName", "TaskSubnets", "TaskSecurityGroup"} <= set(ecs["net"].to_json()["Outputs"])


def test_ecs_apis_reach_their_services_through_the_vpc_link(ecs: dict[str, Template]) -> None:
    for name in ("auth", "mcp", "harness"):
        t = ecs[name]
        t.has_resource_properties(
            "AWS::ApiGatewayV2::Integration",
            {
                "IntegrationType": "HTTP_PROXY",
                "ConnectionType": "VPC_LINK",
                "RequestParameters": {"overwrite:header.x-wf-host": "$context.domainName"},
            },
        )
        t.has_resource_properties(
            "AWS::ServiceDiscovery::Service",
            {"Name": name, "DnsConfig": Match.object_like({"DnsRecords": [{"TTL": 10, "Type": "SRV"}]})},
        )
        t.has_resource_properties(
            "AWS::ECS::Service",
            {
                "LaunchType": "FARGATE",
                "DesiredCount": 1,
                "DeploymentConfiguration": Match.object_like(
                    {
                        "MinimumHealthyPercent": 100,
                        "DeploymentCircuitBreaker": {"Enable": True, "Rollback": True},
                    }
                ),
            },
        )
        web = _web(t)
        env = _env(web)
        bind = ("0.0.0.0", "8080", "*")  # noqa: S104 - the container's own interface; only the VPC link reaches it
        assert (env["WF_BIND_HOST"], env["PORT"], env["FORWARDED_ALLOW_IPS"]) == bind
        assert web["HealthCheck"]["Command"][:2] == ["CMD", "python"]
        assert "{{resolve:secretsmanager" not in json.dumps(t.to_json()), "secrets are injected at start"
    mcp = _env(_web(ecs["mcp"]))
    assert mcp["WF_HOST_HEADER"] == "x-wf-host" and mcp["WF_JOBS_QUEUE_URL"].endswith("/wf-dev-jobs-svc")
    ecs["mcp"].has_resource_properties(
        "AWS::Logs::MetricFilter",
        {
            "MetricTransformations": [
                Match.object_like(
                    {
                        "MetricName": "ToolLatencyMs",
                        "MetricNamespace": "Watchfloor",
                        "Dimensions": [{"Key": "tool", "Value": "$.tool"}],
                    }
                )
            ]
        },
    )
    harness = _web(ecs["harness"])
    assert _env(harness)["WF_HARNESS_WEB_DIST"] == "/var/task/harness_web"
    assert [s["Name"] for s in harness["Secrets"]] == ["WF_HARNESS_SESSION_SECRET"]
    ecs["harness"].has_resource_properties("AWS::ApiGatewayV2::Route", {"RouteKey": "GET /"})


def test_ecs_workers_do_the_four_functions_jobs(ecs: dict[str, Template]) -> None:
    """Dispatcher, watcher, sweeper and canary in one service; the agents in another, by private DNS."""
    a = ecs["async"]
    for queue in ("jobs", "outbox", "jobs-dlq", "outbox-dlq"):  # new queues: no orphaned mapping reaches them
        a.has_resource_properties("AWS::SQS::Queue", {"QueueName": f"wf-dev-{queue}-svc"})
    containers = {c["Name"]: c for c in _containers(a)}
    workers, triage = containers["workers"], containers["triage"]
    assert workers["Command"] == ["python", "-m", "workers.service"]
    env = _env(workers)
    assert env["WF_AGENT_URL"] == "http://triage.wf-dev.internal:8080/invocations"
    assert env["WF_METRICS_API"] == "true" and {"WF_CANARY_SECRET_ARN", "WF_SLACK_SECRET_ARN"} <= set(env)
    assert triage["Command"] == ["python", "-m", "agents.main"] and _env(triage)["DOCKER_CONTAINER"] == "1"
    a.has_resource_properties(
        "AWS::ServiceDiscovery::Service",
        {"Name": "triage", "DnsConfig": Match.object_like({"DnsRecords": [{"TTL": 10, "Type": "A"}]})},
    )
    # One consumer and one scheduler: never two workers tasks at once.
    a.has_resource_properties(
        "AWS::ECS::Service",
        {"DeploymentConfiguration": Match.object_like({"MinimumHealthyPercent": 0, "MaximumPercent": 100})},
    )
    text = json.dumps(a.to_json())
    assert "sqs:ReceiveMessage" in text and "cloudwatch:PutMetricData" in text
    assert '"cloudwatch:namespace": "Watchfloor"' in text
    for name in ("async", "auth", "data", "harness", "mcp"):
        for td in ecs[name].find_resources("AWS::ECS::TaskDefinition").values():
            assert td["Properties"]["RuntimePlatform"] == {
                "CpuArchitecture": "ARM64",
                "OperatingSystemFamily": "LINUX",
            }


def test_ecs_one_off_jobs_are_task_definitions(ecs: dict[str, Template]) -> None:
    ecs["data"].has_resource_properties(
        "AWS::ECS::TaskDefinition",
        {
            "Family": "wf-dev-migrator",
            "ContainerDefinitions": [
                Match.object_like({"Name": "migrator", "Command": ["python", "-m", "workers.migrator"]})
            ],
        },
    )
    ecs["auth"].has_resource_properties(
        "AWS::ECS::TaskDefinition",
        {
            "Family": "wf-dev-seeder",
            "Cpu": "512",
            "Memory": "1024",
            "ContainerDefinitions": [
                Match.object_like({"Name": "seeder", "Command": ["python", "-m", "auth_server.seeder"]})
            ],
        },
    )
    alarms = [
        r["Properties"]["AlarmName"] for r in ecs["ops"].find_resources("AWS::CloudWatch::Alarm").values()
    ]
    assert "wf-dev-canary" in alarms and "wf-dev-spillover" not in alarms  # no alias on ECS


def test_ecs_needs_the_page_served_without_cloudfront() -> None:
    """The page upload and the bucket cleanup are custom resources, which run on Lambda."""
    data = yaml.safe_load((FIXTURES / "free" / "dev.yaml").read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="cdn: false"):
        EnvConfig.model_validate({**data, "compute": "ecs"})
    cfg = EnvConfig.model_validate({**data, "compute": "ecs", "cdn": False})
    assert cfg.queue_name("jobs") == "wf-dev-jobs-svc" and cfg.queue_url("jobs").endswith("/wf-dev-jobs-svc")
    assert EnvConfig.model_validate(data).queue_name("jobs") == "wf-dev-jobs"


def _exports(stacks: dict[str, Template]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for t in stacks.values():
        for out in t.to_json().get("Outputs", {}).values():
            if "Export" in out:
                name = out["Export"]["Name"]
                assert name not in found, f"export {name} twice"
                found[name] = out["Value"]
    return found


def test_the_move_to_ecs_keeps_every_export_the_deployed_ops_stack_imports(ecs: dict[str, Template]) -> None:
    """The exports the Lambda-era Ops stack imports (listed from dev, 2026-10-17). Async and Mcp deploy
    before Ops, and CloudFormation refuses to change or drop an export another stack still imports, so
    each must survive with its value until Ops has been updated."""
    imported_by_ops = {
        "wf-dev-async:ExportsOutputFnGetAttJobsDlqB53173A1QueueName299FCEF1": "wf-dev-jobs-dlq",
        "wf-dev-async:ExportsOutputFnGetAttOutboxDlqFF3CB47FQueueName3435D3EA": "wf-dev-outbox-dlq",
        "wf-dev-mcp:ExportsOutputRefFn733158FE11753353": "wf-dev-mcp-server",
    }
    exports = _exports(ecs)
    for name, value in imported_by_ops.items():
        assert exports.get(name) == value, name
    for name in (
        "wf-dev-mcp:ExportsOutputRefApiF70053CD5653BA4D",
        "wf-dev-auth:ExportsOutputRefCanaryLoginSecretEA929801C2CE77D5",
    ):
        assert name in exports, name  # values are tokens of resources that stay
    ops = json.dumps(ecs["ops"].to_json())
    assert "wf-dev-async:" not in ops, "Ops names the dead-letter queues itself"
    lambda_mode = _synth("dev", **{"wf:configDir": str(CONFIG_DIR), "wf:compute": "lambda"})
    assert set(imported_by_ops) <= set(_exports(lambda_mode))  # and no export twice, on either compute
