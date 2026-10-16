"""Net (TD §11.3): VPC over 2 AZs, one fck-nat instance for egress (ADR-009), S3 gateway endpoint.

With ``compute: ecs`` (ADR-025) also what the containers share: the ECS cluster, a private DNS
namespace (``<prefix>.internal``, Cloud Map) and the VPC link the three HTTP APIs reach them through.
The services keep the Lambdas' security group, which Postgres already admits; port 8080 opens to the
VPC link and between the services (the workers call the triage agents).
"""

from __future__ import annotations

from typing import Any

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_servicediscovery as servicediscovery
from cdk_fck_nat import FckNatInstanceProvider
from constructs import Construct

from ..common import HTTP_PORT
from ..config import EnvConfig


class NetStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, cfg: EnvConfig, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)
        # t4g.micro, not nano: a Free plan account launches only free-tier-eligible types (ADR-014).
        nat = FckNatInstanceProvider(instance_type=ec2.InstanceType("t4g.micro"))
        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            ip_addresses=ec2.IpAddresses.cidr("10.20.0.0/16"),
            max_azs=2,
            nat_gateway_provider=nat,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24),
                ec2.SubnetConfiguration(
                    name="private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, cidr_mask=22
                ),
            ],
            gateway_endpoints={
                "S3": ec2.GatewayVpcEndpointOptions(service=ec2.GatewayVpcEndpointAwsService.S3)
            },
        )
        nat.security_group.add_ingress_rule(ec2.Peer.ipv4(self.vpc.vpc_cidr_block), ec2.Port.all_traffic())
        self.lambda_sg = ec2.SecurityGroup(
            self, "LambdaSg", vpc=self.vpc, allow_all_outbound=True, description="Watchfloor Lambdas"
        )
        self.db_sg = ec2.SecurityGroup(
            self, "DbSg", vpc=self.vpc, allow_all_outbound=False, description="Watchfloor Postgres"
        )
        self.db_sg.add_ingress_rule(self.lambda_sg, ec2.Port.tcp(5432), "Lambdas to Postgres")

        self.cluster: ecs.Cluster | None = None
        self.namespace: servicediscovery.PrivateDnsNamespace | None = None
        self.vpc_link: apigwv2.VpcLink | None = None
        if cfg.ecs:
            self._containers(cfg)

    def _containers(self, cfg: EnvConfig) -> None:
        private = ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)
        self.cluster = ecs.Cluster(self, "Cluster", vpc=self.vpc, cluster_name=cfg.prefix)
        self.namespace = servicediscovery.PrivateDnsNamespace(
            self, "Namespace", name=f"{cfg.prefix}.internal", vpc=self.vpc
        )
        link_sg = ec2.SecurityGroup(
            self, "VpcLinkSg", vpc=self.vpc, allow_all_outbound=False, description="Watchfloor API VPC link"
        )
        port = ec2.Port.tcp(HTTP_PORT)
        link_sg.add_egress_rule(self.lambda_sg, port, "VPC link to the services")
        self.lambda_sg.add_ingress_rule(link_sg, port, "HTTP APIs to the services")
        self.lambda_sg.add_ingress_rule(self.lambda_sg, port, "Service to service (workers to triage)")
        self.vpc_link = apigwv2.VpcLink(
            self, "VpcLink", vpc=self.vpc, subnets=private, security_groups=[link_sg]
        )
        # What scripts/run_task.py needs to start a one-off task (the migrator, the seeder).
        CfnOutput(self, "ClusterName", value=self.cluster.cluster_name)
        CfnOutput(
            self,
            "TaskSubnets",
            value=",".join(
                s.subnet_id for s in self.vpc.select_subnets(subnet_type=private.subnet_type).subnets
            ),
        )
        CfnOutput(self, "TaskSecurityGroup", value=self.lambda_sg.security_group_id)
