"""Per-environment settings (infra/config/<env>.yaml), validated before any stack is built.

Domain names are fixed strings rather than API Gateway tokens on purpose: the
issuer, the resource URI (token audience) and the harness redirect URI must be
known to several stacks at once, and fixed values keep the stacks free of
cross-stack cycles. That holds without custom domains too: the hosts AWS assigns
stay the same for the life of the stacks, so they are written into the config
after the first deploy (scripts/default_hosts.py).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
PENDING_HOST = "pending.invalid"  # a host AWS has yet to assign (RFC 2606 reserves .invalid)


class Models(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Slot names from the design: sonnet = the reasoning tier (Lead, Strategist, Critic), haiku = the
    # fast tier (harness, drafts, Analyst, Context). Either takes a Claude inference-profile id
    # (us.anthropic.…) or an in-Region Amazon Nova id (amazon.nova-…, ADR-022).
    sonnet: str
    haiku: str


class EnvConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    env: Literal["dev", "demo"]
    account: str = Field(pattern=r"^\d{12}$")
    region: Literal["us-east-1"] = "us-east-1"
    # With certificate_arn (an ACM certificate in us-east-1 covering all three), the host names are custom
    # domains. Without one (no domain to buy), they are the hosts AWS assigns, execute-api and
    # cloudfront.net: the first deploy creates them, scripts/default_hosts.py writes them here, and a
    # second deploy points every Lambda at them (`make cdk-deploy` does all three). Until then they
    # read as pending.invalid.
    mcp_domain: str | None = None
    auth_domain: str | None = None
    harness_domain: str | None = None
    certificate_arn: str | None = None
    # Route 53 zone; without it, the DNS records are created by hand from the DnsTarget outputs.
    hosted_zone_id: str | None = None
    hosted_zone_name: str | None = None
    lwa_layer_arn: str  # Lambda Web Adapter, arm64 (⚠ Verify the current version)
    models: Models
    # false: no Bedrock model runs anywhere. Drafts use a template, the harness its offline engine,
    # and there is no Agents stack. `-c wf:modelsEnabled=true|false` overrides it for one synth.
    models_enabled: bool = True
    # false: Bedrock models run (harness, drafts) but AgentCore is not deployed, so there is no Agents
    # stack and deep triage is refused up front (ADR-022). `-c wf:agentsEnabled=…` overrides it.
    agents_enabled: bool = True
    # Google AI Studio's Gemini API, free tier (ADR-023): the model the harness and drafts use when
    # Bedrock can't run, after Bedrock where models are on. Its key is the plain-string secret
    # wf-<env>/models/gemini, stored by hand; until it is there, turns and drafts fall back as before.
    gemini_enabled: bool = False
    gemini_model: str = "gemini-flash-lite-latest"
    # false while AWS Support has not verified the account for CloudFront (a new account's
    # distributions are refused until then): the harness Lambda serves its page on its API's host.
    cdn: bool = True
    # false while the account's Lambda concurrency quota is 10 (a new account's): reserving any would
    # take the unreserved pool below its minimum of 10, so the functions share the 10 instead (ADR-016).
    reserve_concurrency: bool = True
    alert_email: str
    # Seeded user the canary signs in as, with its own generated password (wf-<env>/canary/login).
    canary_email: str = "daniel@lumenridge.example"
    # Alexa+ account-linking redirect URIs, known once the skill exists (⚠ S2); the seeder registers them.
    alexa_redirect_uris: list[str] = []
    budgets_usd: list[int] = [25, 60, 90]
    demo_mode: bool = False
    db_instance_class: str = "t4g.micro"
    db_storage_gb: int = 20
    # Automated backup retention. A Free plan account caps it, so dev.yaml sets 1.
    db_backup_days: int = Field(default=7, ge=1, le=35)
    provisioned_concurrency: int = 1
    # MCP calls per minute (DD §18.1). Raise them only for a load-test window: the Locust profile's
    # 20 rps is twice the per-workspace default (tests/load/locustfile.py).
    rate_limit_user: int = Field(default=60, ge=1)
    rate_limit_ws: int = Field(default=600, ge=1)
    agent_image_tag: str = "latest"
    removal_protection: bool = True

    @field_validator("mcp_domain", "auth_domain", "harness_domain")
    @classmethod
    def _hostname(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", v):
            raise ValueError(f"{v!r} is not a bare host name")
        return v

    @model_validator(mode="after")
    def _domains(self) -> EnvConfig:
        if self.certificate_arn and not (self.mcp_domain and self.auth_domain and self.harness_domain):
            raise ValueError("a certificate_arn needs all three host names (mcp, auth and harness_domain)")
        if (self.hosted_zone_id or self.hosted_zone_name) and not self.certificate_arn:
            raise ValueError("a hosted zone is for custom domains: set certificate_arn too")
        return self

    @property
    def custom_domains(self) -> bool:
        return self.certificate_arn is not None

    @property
    def agents_on(self) -> bool:
        """The Agents stack (AgentCore Runtime and Memory) is deployed."""
        return self.models_enabled and self.agents_enabled

    @property
    def prefix(self) -> str:
        return f"wf-{self.env}"

    @property
    def gemini_secret_name(self) -> str:
        return f"{self.prefix}/models/gemini"

    @property
    def mcp_host(self) -> str:
        return self.mcp_domain or PENDING_HOST

    @property
    def auth_host(self) -> str:
        return self.auth_domain or PENDING_HOST

    @property
    def harness_host(self) -> str:
        return self.harness_domain or PENDING_HOST

    @property
    def issuer(self) -> str:
        return f"https://{self.auth_host}"

    @property
    def resource_uri(self) -> str:
        return f"https://{self.mcp_host}/mcp"

    @property
    def harness_url(self) -> str:
        return f"https://{self.harness_host}"

    def queue_url(self, name: str) -> str:
        """SQS URLs are deterministic for named queues, so the MCP stack needs no Async reference."""
        return f"https://sqs.{self.region}.amazonaws.com/{self.account}/{self.prefix}-{name}"


def load_config(env: str, *, account: str | None = None, directory: Path = CONFIG_DIR) -> EnvConfig:
    path = directory / f"{env}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"no infra config for {env!r} ({path})")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if account:
        data["account"] = account
    return EnvConfig.model_validate(data)
