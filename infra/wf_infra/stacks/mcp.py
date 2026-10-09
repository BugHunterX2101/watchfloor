"""Mcp (TD §11.4): the MCP server Lambda, alias ``live`` with provisioned concurrency, HTTP API.

Queue URLs and ARNs are derived from fixed queue names (``EnvConfig.queue_url``), so
this stack sends to the Async stack's queues without depending on it.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from constructs import Construct

from ..common import PythonLambda, default_host_output, domain_mapping
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
        fn = PythonLambda(
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
            environment={
                "WF_DB_SECRET_ARN": data.app_secret.secret_arn,
                "WF_ALLOWED_HOSTS": cfg.mcp_host,
                "WF_ALLOWED_ORIGINS": cfg.harness_url,
                "WF_PROTOCOL_VERSIONS": "2025-11-25",
                "WF_RATE_LIMIT_USER": str(cfg.rate_limit_user),
                "WF_RATE_LIMIT_WS": str(cfg.rate_limit_ws),
                "WF_JOBS_QUEUE_URL": cfg.queue_url("jobs"),
                "WF_OUTBOX_QUEUE_URL": cfg.queue_url("outbox"),
            },
        ).function
        data.app_secret.grant_read(fn)
        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["sqs:SendMessage"],
                resources=[f"arn:aws:sqs:{cfg.region}:{cfg.account}:{cfg.prefix}-{q}" for q in QUEUES],
            )
        )
        self.function = fn
        self.alias = lambda_.Alias(
            self,
            "Live",
            alias_name="live",
            version=fn.current_version,
            provisioned_concurrent_executions=cfg.provisioned_concurrency or None,
        )

        integ = integrations.HttpLambdaIntegration("Integ", self.alias)
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
        stage = self.api.default_stage.node.default_child  # escape hatch: stage throttling (DD §18.1)
        assert isinstance(stage, apigwv2.CfnStage)
        stage.default_route_settings = apigwv2.CfnStage.RouteSettingsProperty(
            throttling_burst_limit=50, throttling_rate_limit=25
        )
        CfnOutput(self, "ResourceUri", value=cfg.resource_uri)
        default_host_output(self, cfg, self.api)
