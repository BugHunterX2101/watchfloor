"""Agents (DD §18.1, TD §10.3): ECR repository, AgentCore Memory and the AgentCore Runtime.

The runtime needs an image in the repository before it can be created, so the first
deploy of a new environment runs with ``-c wf:agentRuntime=false``, then
``make agents-image`` and a push, then a normal deploy (README, Deploy).

Memory namespaces are set to ``/actors/{actorId}/...`` so the agents' prefix search
(``WF_MEMORY_NAMESPACE_PATH``, default ``/actors/{actorId}/``) covers both strategies.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from constructs import Construct

from ..common import bedrock_arns
from ..config import EnvConfig

RUNTIME_CONTEXT = "wf:agentRuntime"


class AgentsStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, cfg: EnvConfig, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)
        name = f"wf_{cfg.env}"  # AgentCore names: letters, digits and underscores
        self.repository = ecr.Repository(
            self,
            "Repo",
            repository_name=f"{cfg.prefix}-agents",
            image_scan_on_push=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[ecr.LifecycleRule(max_image_count=10)],
        )
        self.memory = agentcore.CfnMemory(
            self,
            "Memory",
            name=f"{name}_mem",
            description="Watchfloor: outcomes of earlier reviews and learned user preferences (DD §11)",
            event_expiry_duration=90,
            memory_strategies=[
                agentcore.CfnMemory.MemoryStrategyProperty(
                    semantic_memory_strategy=agentcore.CfnMemory.SemanticMemoryStrategyProperty(
                        name="facts", namespaces=["/actors/{actorId}/facts"]
                    )
                ),
                agentcore.CfnMemory.MemoryStrategyProperty(
                    user_preference_memory_strategy=agentcore.CfnMemory.UserPreferenceMemoryStrategyProperty(
                        name="preferences", namespaces=["/actors/{actorId}/preferences"]
                    )
                ),
            ],
        )
        self.memory_id = self.memory.attr_memory_id
        self.memory_arn = self.memory.attr_memory_arn

        # The runtime role: the two models, memory reads, image pull, logs. No database, no VPC.
        role = iam.Role(
            self, "RuntimeRole", assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com")
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:Converse",
                    "bedrock:ConverseStream",
                ],
                resources=bedrock_arns(cfg, cfg.models.sonnet, cfg.models.haiku),
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock-agentcore:RetrieveMemoryRecords", "bedrock-agentcore:ListMemoryRecords"],
                resources=[self.memory_arn],
            )
        )
        self.repository.grant_pull(role)
        role.add_to_policy(iam.PolicyStatement(actions=["ecr:GetAuthorizationToken"], resources=["*"]))
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[f"arn:aws:logs:{cfg.region}:{cfg.account}:log-group:/aws/bedrock-agentcore/*"],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["xray:PutTraceSegments", "xray:PutTelemetryRecords", "cloudwatch:PutMetricData"],
                resources=["*"],
            )
        )

        self.runtime_arn: str | None = None
        if self.node.try_get_context(RUNTIME_CONTEXT) not in (False, "false", "0"):
            runtime = agentcore.CfnRuntime(
                self,
                "Runtime",
                agent_runtime_name=f"{name}_triage",
                description="Watchfloor deep triage: a Triage Lead and four specialists (ADR-004)",
                role_arn=role.role_arn,
                agent_runtime_artifact=agentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                    container_configuration=agentcore.CfnRuntime.ContainerConfigurationProperty(
                        container_uri=f"{self.repository.repository_uri}:{cfg.agent_image_tag}"
                    )
                ),
                network_configuration=agentcore.CfnRuntime.NetworkConfigurationProperty(
                    network_mode="PUBLIC"
                ),
                protocol_configuration="HTTP",
                environment_variables={
                    "WF_ENV": cfg.env,
                    "WF_MCP_URL": cfg.resource_uri,
                    "WF_SONNET_MODEL_ID": cfg.models.sonnet,
                    "WF_HAIKU_MODEL_ID": cfg.models.haiku,
                    "WF_MEMORY_ID": self.memory_id,
                    "WF_MEMORY_NAMESPACE_PATH": "/actors/{actorId}/",
                },
            )
            runtime.node.add_dependency(role)
            self.runtime_arn = runtime.attr_agent_runtime_arn
            CfnOutput(self, "RuntimeArn", value=self.runtime_arn)
        CfnOutput(self, "RepositoryUri", value=self.repository.repository_uri)
        CfnOutput(self, "MemoryId", value=self.memory_id)
