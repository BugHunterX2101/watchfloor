"""Mcp (TD §11.4): the MCP server Lambda, alias ``live`` with provisioned concurrency, HTTP API.

With ``compute: ecs`` (ADR-025) the server is a Fargate service behind the same HTTP API instead,
with no alias. Its host check then reads ``x-wf-host``, which the API sets to its own domain, and
``ToolLatencyMs{tool}`` comes from a metric filter on its log group: the tool-call record is the
same JSON line, but container stdout is not read as embedded metric format.

Queue URLs and ARNs are derived from fixed queue names (``EnvConfig.queue_url``), so
this stack sends to the Async stack's queues without depending on it.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

from ..common import (
    GATEWAY_HOST_HEADER,
    NAMESPACE,
    Container,
    PythonLambda,
    default_host_output,
    domain_mapping,
    service_integration,
    throttle,
)
from ..config import EnvConfig
from .data import DataStack
from .net import NetStack

QUEUES = ("jobs", "outbox")


class McpStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        cfg: EnvConfig,
        net: NetStack,
        data: DataStack,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        env = {
            "WF_DB_SECRET_ARN": data.app_secret.secret_arn,
            "WF_ALLOWED_HOSTS": cfg.mcp_host,
            "WF_ALLOWED_ORIGINS": cfg.harness_url,
            "WF_PROTOCOL_VERSIONS": "2025-11-25",
            "WF_RATE_LIMIT_USER": str(cfg.rate_limit_user),
            "WF_RATE_LIMIT_WS": str(cfg.rate_limit_ws),
            "WF_JOBS_QUEUE_URL": cfg.queue_url("jobs"),
            "WF_OUTBOX_QUEUE_URL": cfg.queue_url("outbox"),
        }
        server: lambda_.Function | Container
        integ: apigwv2.HttpRouteIntegration
        self.alias: lambda_.Alias | None = None
        if cfg.ecs:
            server = Container(
                self,
                "Server",
                cfg=cfg,
                name="mcp-server",
                bundle="mcp_server",
                memory_mib=1024,
                environment={**env, "WF_HOST_HEADER": GATEWAY_HOST_HEADER},
                http=True,
                health_path="/healthz",
            )
            integ = service_integration(server.service(net, discovery="mcp", srv=True), net)
            # An Ops stack deployed on Lambda imports the function's name (its spillover alarm). This
            # stack deploys first, and an imported export can't disappear, so it stays, as a string.
            CfnOutput(
                self,
                "LegacyFnName",
                value=f"{cfg.prefix}-mcp-server",
                export_name=f"{self.stack_name}:ExportsOutputRefFn733158FE11753353",
            )
            logs.MetricFilter(
                self,
                "ToolLatency",
                log_group=server.log_group,
                filter_pattern=logs.FilterPattern.string_value("$.message", "=", "tool_call"),
                metric_namespace=NAMESPACE,
                metric_name="ToolLatencyMs",
                metric_value="$.latency_ms",
                dimensions={"tool": "$.tool"},
                unit=cw.Unit.MILLISECONDS,
            )
        else:
            server = PythonLambda(
                self,
                "Fn",
                cfg=cfg,
                name="mcp-server",
                bundle="mcp_server",
                web_adapter=True,
                memory_size=1024,
                timeout=Duration.seconds(10),
                vpc=net.vpc,
                security_groups=[net.lambda_sg],
                reserved_concurrency=20,
                environment=env,
            ).function
            self.alias = lambda_.Alias(
                self,
                "Live",
                alias_name="live",
                version=server.current_version,
                provisioned_concurrent_executions=cfg.provisioned_concurrency or None,
            )
            integ = integrations.HttpLambdaIntegration("Integ", self.alias)
        data.app_secret.grant_read(server)
        server.add_to_role_policy(
            iam.PolicyStatement(
                actions=["sqs:SendMessage"],
                resources=[f"arn:aws:sqs:{cfg.region}:{cfg.account}:{cfg.queue_name(q)}" for q in QUEUES],
            )
        )
        self.function = server

        self.api = apigwv2.HttpApi(
            self,
            "Api",
            api_name=f"{cfg.prefix}-mcp",
            default_domain_mapping=domain_mapping(self, cfg, cfg.mcp_domain),
        )
        self.api.add_routes(
            path="/mcp",
            methods=[apigwv2.HttpMethod.POST, apigwv2.HttpMethod.GET, apigwv2.HttpMethod.DELETE],
            integration=integ,
        )
        self.api.add_routes(path="/.well-known/{proxy+}", methods=[apigwv2.HttpMethod.GET], integration=integ)
        self.api.add_routes(path="/healthz", methods=[apigwv2.HttpMethod.GET], integration=integ)
        throttle(self.api, rate=25, burst=50)
        CfnOutput(self, "ResourceUri", value=cfg.resource_uri)
        default_host_output(self, cfg, self.api)
