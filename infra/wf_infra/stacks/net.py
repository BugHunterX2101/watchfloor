"""Net (TD §11.3): VPC over 2 AZs, one fck-nat instance for egress (ADR-009), S3 gateway endpoint."""

from __future__ import annotations

from typing import Any

from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from cdk_fck_nat import FckNatInstanceProvider
from constructs import Construct

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
