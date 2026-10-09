"""Per-environment settings (infra/config/<env>.yaml), validated before any stack is built.

Domain names are fixed strings rather than API Gateway tokens on purpose: the
issuer, the resource URI (token audience) and the harness redirect URI must be
known to several stacks at once, and fixed values keep the stacks free of
cross-stack cycles.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


class Models(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sonnet: str  # Bedrock inference profile ids (⚠ Verify S6)
    haiku: str


class EnvConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    env: Literal["dev", "demo"]
    account: str = Field(pattern=r"^\d{12}$")
    region: Literal["us-east-1"] = "us-east-1"
    mcp_domain: str
    auth_domain: str
    harness_domain: str
    certificate_arn: str  # ACM certificate in us-east-1 covering the three domains
    # Route 53 zone; without it, the DNS records are created by hand from the DnsTarget outputs.
    hosted_zone_id: str | None = None
    hosted_zone_name: str | None = None
    lwa_layer_arn: str  # Lambda Web Adapter, arm64 (⚠ Verify the current version)
    models: Models
    # false on an AWS Free plan account: AgentCore is Paid-plan only and Claude is billed through
    # Marketplace. No Agents stack then; deep triage is refused, drafts use a template, and the
    # harness uses its offline engine. `-c wf:modelsEnabled=true|false` overrides it for one synth.
    models_enabled: bool = True
    alert_email: str
    # Seeded user the canary signs in as, with its own generated password (wf-<env>/canary/login).
    canary_email: str = "daniel@lumenridge.example"
    # Alexa+ account-linking redirect URIs, known once the skill exists (⚠ S2); the seeder registers them.
    alexa_redirect_uris: list[str] = []
    budgets_usd: list[int] = [25, 60, 90]
    demo_mode: bool = False
    db_instance_class: str = "t4g.micro"
    db_storage_gb: int = 20
    provisioned_concurrency: int = 1
    # MCP calls per minute (DD §18.1). Raise them only for a load-test window: the Locust profile's
    # 20 rps is twice the per-workspace default (tests/load/locustfile.py).
    rate_limit_user: int = Field(default=60, ge=1)
    rate_limit_ws: int = Field(default=600, ge=1)
    agent_image_tag: str = "latest"
    removal_protection: bool = True

    @field_validator("mcp_domain", "auth_domain", "harness_domain")
    @classmethod
    def _hostname(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", v):
            raise ValueError(f"{v!r} is not a bare host name")
        return v

    @property
    def prefix(self) -> str:
        return f"wf-{self.env}"

    @property
    def issuer(self) -> str:
        return f"https://{self.auth_domain}"

    @property
    def resource_uri(self) -> str:
        return f"https://{self.mcp_domain}/mcp"

    @property
    def harness_url(self) -> str:
        return f"https://{self.harness_domain}"

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
