"""Data (DD §18.1): RDS Postgres 16, three login secrets, and the migrator Lambda.

- ``wf_owner`` is the master user (RDS-managed secret): migrations and seeding.
- ``wf_app`` and ``wf_auth`` get generated secrets attached to the instance. Migration
  0004 creates them NOLOGIN; the migrator enables each login with its secret's password.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds
from aws_cdk import aws_secretsmanager as sm
from constructs import Construct

from ..common import PythonLambda
from ..config import EnvConfig
from .net import NetStack


class DataStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str, *, cfg: EnvConfig, net: NetStack, **kwargs: Any
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        engine = rds.DatabaseInstanceEngine.postgres(version=rds.PostgresEngineVersion.VER_16)
        params = rds.ParameterGroup(self, "Params", engine=engine, parameters={"rds.force_ssl": "1"})
        protect = cfg.removal_protection
        self.db = rds.DatabaseInstance(
            self,
            "Db",
            engine=engine,
            instance_type=ec2.InstanceType(cfg.db_instance_class),
            vpc=net.vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            security_groups=[net.db_sg],
            credentials=rds.Credentials.from_generated_secret(
                "wf_owner", secret_name=f"{cfg.prefix}/db/owner"
            ),
            database_name="watchfloor",
            allocated_storage=cfg.db_storage_gb,
            storage_type=rds.StorageType.GP3,
            storage_encrypted=True,
            backup_retention=Duration.days(7),
            multi_az=False,
            publicly_accessible=False,
            parameter_group=params,
            deletion_protection=protect,
            removal_policy=RemovalPolicy.SNAPSHOT if protect else RemovalPolicy.DESTROY,
        )
        assert self.db.secret is not None
        self.owner_secret: sm.ISecret = self.db.secret
        self.app_secret = self._login("App", "wf_app", cfg)
        self.auth_secret = self._login("Auth", "wf_auth", cfg)

        self.migrator = PythonLambda(
            self,
            "Migrator",
            cfg=cfg,
            name="migrator",
            bundle="workers",
            handler="workers.migrator.handler",
            timeout=Duration.minutes(2),
            vpc=net.vpc,
            security_groups=[net.lambda_sg],
            environment={
                "WF_OWNER_DB_SECRET_ARN": self.owner_secret.secret_arn,
                "WF_APP_DB_SECRET_ARN": self.app_secret.secret_arn,
                "WF_AUTH_DB_SECRET_ARN": self.auth_secret.secret_arn,
            },
        ).function
        for secret in (self.owner_secret, self.app_secret, self.auth_secret):
            secret.grant_read(self.migrator)
        CfnOutput(self, "MigratorName", value=self.migrator.function_name)

    def _login(self, construct_id: str, username: str, cfg: EnvConfig) -> sm.ISecret:
        secret = rds.DatabaseSecret(
            self,
            f"{construct_id}Secret",
            username=username,
            secret_name=f"{cfg.prefix}/db/{username.removeprefix('wf_')}",
            exclude_characters=" %+~`#$&*()|[]{}:;<>?!'/@\"\\",
        )
        return secret.attach(self.db)
