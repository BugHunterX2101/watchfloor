"""Auth (DD §18.1, ADR-006): KMS ES256 signing key, the authorization server Lambda, its HTTP API,
the generated OAuth client and login secrets, and the seeder Lambda that registers them."""

from __future__ import annotations

import json
from typing import Any

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_kms as kms
from aws_cdk import aws_secretsmanager as sm
from constructs import Construct

from ..common import PythonLambda, api_domain
from ..config import EnvConfig
from .data import DataStack
from .net import NetStack


class AuthStack(Stack):
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
        self.signing_key = kms.Key(
            self,
            "SigningKey",
            key_spec=kms.KeySpec.ECC_NIST_P256,
            key_usage=kms.KeyUsage.SIGN_VERIFY,
            alias=f"alias/{cfg.prefix}-jwt-signing",
            description="Watchfloor access-token signing (ES256); the private key never leaves KMS",
            removal_policy=RemovalPolicy.RETAIN,
        )
        # Signs the login → consent form ticket; required outside local (auth_server.context).
        session = sm.Secret(
            self,
            "SessionSecret",
            secret_name=f"{cfg.prefix}/auth/session",
            generate_secret_string=sm.SecretStringGenerator(exclude_punctuation=True, password_length=48),
        )
        fn = PythonLambda(
            self,
            "Fn",
            cfg=cfg,
            name="auth-server",
            bundle="auth_server",
            web_adapter=True,
            memory_size=512,
            timeout=Duration.seconds(10),
            vpc=net.vpc,
            security_groups=[net.lambda_sg],
            environment={
                "WF_AUTH_DB_SECRET_ARN": data.auth_secret.secret_arn,
                "WF_KMS_KEY_ID": self.signing_key.key_arn,
                # A CloudFormation dynamic reference: resolved at deploy time, never written into the template.
                "WF_SESSION_SECRET": session.secret_value.unsafe_unwrap(),
                "WF_HARNESS_REDIRECT_URI": f"{cfg.harness_url}/oauth/callback",
                "WF_ALLOWED_HOSTS": cfg.auth_domain,
            },
        ).function
        data.auth_secret.grant_read(fn)
        self.signing_key.grant(fn, "kms:Sign", "kms:GetPublicKey", "kms:DescribeKey")

        # OAuth client secrets and logins, generated here and registered by the seeder (README, Deploy).
        # The Alexa+ secret is read from Secrets Manager when the skill's account linking is set up.
        self.alexa_secret = self._generated("AlexaClient", f"{cfg.prefix}/auth/alexa-client", "client_secret")
        self.dispatcher_secret = self._generated(
            "DispatcherClient", f"{cfg.prefix}/auth/dispatcher-client", "client_secret"
        )
        self.demo_login_secret = self._generated("DemoLogin", f"{cfg.prefix}/auth/demo-login", "password")
        self.canary_secret = self._generated(
            "CanaryLogin", f"{cfg.prefix}/canary/login", "password", {"email": cfg.canary_email}
        )
        login_secrets = {
            "WF_ALEXA_CLIENT_SECRET_ARN": self.alexa_secret,
            "WF_DISPATCHER_CLIENT_SECRET_ARN": self.dispatcher_secret,
            "WF_DEMO_LOGIN_SECRET_ARN": self.demo_login_secret,
            "WF_CANARY_SECRET_ARN": self.canary_secret,
        }
        # One-shot, like the migrator: `make seed-remote ENV=…` (auth_server.seeder).
        self.seeder = PythonLambda(
            self,
            "Seeder",
            cfg=cfg,
            name="seeder",
            bundle="auth_server",
            handler="auth_server.seeder.handler",
            memory_size=1024,
            timeout=Duration.minutes(5),
            vpc=net.vpc,
            security_groups=[net.lambda_sg],
            environment={
                "WF_OWNER_DB_SECRET_ARN": data.owner_secret.secret_arn,
                "WF_HARNESS_REDIRECT_URI": f"{cfg.harness_url}/oauth/callback",
                "WF_ALEXA_REDIRECT_URIS": ",".join(cfg.alexa_redirect_uris),
                **{env: secret.secret_arn for env, secret in login_secrets.items()},
            },
        ).function
        for secret in (data.owner_secret, *login_secrets.values()):
            secret.grant_read(self.seeder)
        CfnOutput(self, "SeederName", value=self.seeder.function_name)
        CfnOutput(self, "AlexaClientSecretName", value=self.alexa_secret.secret_name)
        CfnOutput(self, "DemoLoginSecretName", value=self.demo_login_secret.secret_name)

        integ = integrations.HttpLambdaIntegration("Integ", fn)
        self.api = apigwv2.HttpApi(
            self,
            "Api",
            api_name=f"{cfg.prefix}-auth",
            default_domain_mapping=apigwv2.DomainMappingOptions(
                domain_name=api_domain(self, cfg, cfg.auth_domain)
            ),
        )
        self.api.add_routes(path="/{proxy+}", methods=[apigwv2.HttpMethod.ANY], integration=integ)
        self.function = fn
        CfnOutput(self, "Issuer", value=cfg.issuer)

    def _generated(
        self, construct_id: str, name: str, key: str, template: dict[str, str] | None = None
    ) -> sm.Secret:
        """A JSON secret whose ``key`` field Secrets Manager generates (letters and digits, 40 long)."""
        return sm.Secret(
            self,
            f"{construct_id}Secret",
            secret_name=name,
            generate_secret_string=sm.SecretStringGenerator(
                secret_string_template=json.dumps(template or {}),
                generate_string_key=key,
                exclude_punctuation=True,
                password_length=40,
            ),
        )
