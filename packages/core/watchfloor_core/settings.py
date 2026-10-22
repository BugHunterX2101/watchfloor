"""Typed environment settings shared by every Watchfloor service.

All values come from ``WF_*`` environment variables (TD §7.1). Settings are read
once and cached; tests call :func:`reset_settings` after changing the environment.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field


def _csv(name: str, default: str = "") -> frozenset[str]:
    raw = os.environ.get(name, default)
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings(BaseModel):
    env: Literal["local", "test", "dev", "demo"] = "local"
    database_url: str | None = None
    db_secret_arn: str | None = None
    resource_uri: str = "http://localhost:8080/mcp"
    issuer: str = "http://localhost:8081"
    # Connection addresses (not identifiers) use 127.0.0.1: on Windows "localhost" tries ::1 first,
    # where nothing listens, and costs about 2 s per new connection.
    jwks_url: str = "http://127.0.0.1:8081/jwks.json"
    allowed_hosts: frozenset[str] = Field(default_factory=lambda: frozenset({"localhost", "127.0.0.1"}))
    # The header the host check reads. On ECS (ADR-025) the HTTP API reaches the container through a
    # VPC link, so the public host arrives in a header the API itself sets on every request
    # (x-wf-host = $context.domainName; a client's own value is overwritten), not in Host.
    host_header: str = "host"
    allowed_origins: frozenset[str] = frozenset()
    # 2025-11-25 is what Alexa+ speaks; 2025-06-18 is what Kiro and other MCP clients still offer (ADR-034).
    protocol_versions: frozenset[str] = frozenset({"2025-06-18", "2025-11-25"})
    jobs_queue_url: str | None = None
    outbox_queue_url: str | None = None
    demo_mode: bool = False
    rate_limit_user: int = 60
    rate_limit_ws: int = 600
    dev_signing_key_path: str = ".secrets/dev_es256.pem"
    access_ttl_s: int = 3600
    refresh_ttl_days: int = 30
    mcp_url: str = "http://127.0.0.1:8080/mcp"
    slack_webhook_url: str | None = None
    sonnet_model_id: str | None = None
    haiku_model_id: str | None = None
    # false where no Bedrock model may run: follow-up drafts use a template and the harness its
    # offline engine. Deep triage needs agents_enabled, and Bedrock or a Gemini model.
    models_enabled: bool = True
    # false where no agents are deployed (ADR-022): deep triage is refused up front.
    agents_enabled: bool = True
    # Google AI Studio's Gemini API (ADR-023): the model wherever Bedrock can't run. The key comes from
    # WF_GEMINI_API_KEY (local) or the plain-string secret named by WF_GEMINI_SECRET (deployed).
    gemini_model_id: str | None = None
    # The fast tier's Gemini model (harness turns, drafts, Analyst, Context); unset, it is gemini_model_id.
    gemini_haiku_model_id: str | None = None
    gemini_secret: str | None = None
    gemini_api_key: str | None = None
    agent_runtime_arn: str | None = None
    # The agents as a plain Lambda function where AgentCore is refused (ADR-023).
    agent_function: str | None = None
    # Local alternative to AgentCore: the agents container's /invocations endpoint.
    agent_url: str | None = None
    memory_id: str | None = None
    slack_secret_arn: str | None = None
    dispatcher_client_id: str = "wf-dispatcher"
    dispatcher_client_secret: str | None = None
    dispatcher_client_secret_arn: str | None = None
    # Canary: signs in as a dedicated user through the harness client (login → token → briefing).
    canary_email: str | None = None
    canary_password: str | None = None
    canary_secret_arn: str | None = None
    harness_client_id: str = "wf-harness"
    harness_redirect_uri: str = "http://localhost:8082/oauth/callback"
    # Authorization server: its own role (wf_auth), signing key and form-ticket secret.
    auth_database_url: str | None = None
    auth_db_secret_arn: str | None = None
    kms_key_id: str | None = None
    session_secret: str | None = None


def _from_env() -> Settings:
    e = os.environ.get
    return Settings(
        env=e("WF_ENV", "local"),  # type: ignore[arg-type]
        database_url=e("WF_DATABASE_URL"),
        db_secret_arn=e("WF_DB_SECRET_ARN"),
        resource_uri=e("WF_RESOURCE_URI", "http://localhost:8080/mcp"),
        issuer=e("WF_ISSUER", "http://localhost:8081"),
        jwks_url=e("WF_JWKS_URL", "http://127.0.0.1:8081/jwks.json"),
        allowed_hosts=_csv("WF_ALLOWED_HOSTS", "localhost,127.0.0.1"),
        host_header=(e("WF_HOST_HEADER") or "host").strip().lower(),
        allowed_origins=_csv("WF_ALLOWED_ORIGINS"),
        protocol_versions=_csv("WF_PROTOCOL_VERSIONS", "2025-06-18,2025-11-25"),
        jobs_queue_url=e("WF_JOBS_QUEUE_URL"),
        outbox_queue_url=e("WF_OUTBOX_QUEUE_URL"),
        demo_mode=_bool("WF_DEMO_MODE"),
        rate_limit_user=int(e("WF_RATE_LIMIT_USER", "60")),
        rate_limit_ws=int(e("WF_RATE_LIMIT_WS", "600")),
        dev_signing_key_path=e("WF_DEV_SIGNING_KEY_PATH", ".secrets/dev_es256.pem"),
        access_ttl_s=int(e("WF_ACCESS_TTL_S", "3600")),
        refresh_ttl_days=int(e("WF_REFRESH_TTL_DAYS", "30")),
        mcp_url=e("WF_MCP_URL", "http://127.0.0.1:8080/mcp"),
        slack_webhook_url=e("WF_SLACK_WEBHOOK_URL") or None,
        sonnet_model_id=e("WF_SONNET_MODEL_ID"),
        haiku_model_id=e("WF_HAIKU_MODEL_ID"),
        models_enabled=_bool("WF_MODELS_ENABLED", True),
        agents_enabled=_bool("WF_AGENTS_ENABLED", True),
        gemini_model_id=e("WF_GEMINI_MODEL_ID") or None,
        gemini_haiku_model_id=e("WF_GEMINI_HAIKU_MODEL_ID") or None,
        gemini_secret=e("WF_GEMINI_SECRET") or None,
        gemini_api_key=e("WF_GEMINI_API_KEY") or None,
        agent_runtime_arn=e("WF_AGENT_RUNTIME_ARN") or None,
        agent_function=e("WF_AGENT_FUNCTION") or None,
        agent_url=e("WF_AGENT_URL") or None,
        memory_id=e("WF_MEMORY_ID") or None,
        slack_secret_arn=e("WF_SLACK_SECRET_ARN") or None,
        dispatcher_client_id=e("WF_DISPATCHER_CLIENT_ID", "wf-dispatcher"),
        dispatcher_client_secret=e("WF_DISPATCHER_CLIENT_SECRET") or None,
        dispatcher_client_secret_arn=e("WF_DISPATCHER_CLIENT_SECRET_ARN") or None,
        canary_email=e("WF_CANARY_EMAIL") or None,
        canary_password=e("WF_CANARY_PASSWORD") or None,
        canary_secret_arn=e("WF_CANARY_SECRET_ARN") or None,
        harness_client_id=e("WF_HARNESS_CLIENT_ID", "wf-harness"),
        harness_redirect_uri=e("WF_HARNESS_REDIRECT_URI", "http://localhost:8082/oauth/callback"),
        auth_database_url=e("WF_AUTH_DATABASE_URL"),
        auth_db_secret_arn=e("WF_AUTH_DB_SECRET_ARN"),
        kms_key_id=e("WF_KMS_KEY_ID") or None,
        session_secret=e("WF_SESSION_SECRET") or None,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return _from_env()


def reset_settings() -> None:
    get_settings.cache_clear()
