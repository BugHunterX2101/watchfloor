"""Job-scoped agent tokens via client_credentials (DD §12, auth_server.token).

The dispatcher is a confidential client (``client_secret_basic``). Each token
names one workspace and one job, carries only ``watchfloor.agent.read`` and
lives five minutes; the MCP server honours it only while that job is running.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
from watchfloor_core.settings import Settings

from .errors import GiveUp, RetryLater
from .runtime import secret_field

AGENT_SCOPE = "watchfloor.agent.read"
TIMEOUT_S = 10.0

Minter = Callable[[str, str], Awaitable[str]]


async def mint(
    s: Settings,
    workspace_id: str,
    job_id: str,
    *,
    client_secret: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    try:
        async with httpx.AsyncClient(transport=transport, timeout=TIMEOUT_S) as http:
            r = await http.post(
                f"{s.issuer.rstrip('/')}/token",
                data={
                    "grant_type": "client_credentials",
                    "scope": AGENT_SCOPE,
                    "ws": workspace_id,
                    "job": job_id,
                },
                auth=(s.dispatcher_client_id, client_secret),
            )
    except httpx.HTTPError as exc:
        raise RetryLater(f"auth server unreachable: {type(exc).__name__}") from None
    if r.status_code == 429 or r.status_code >= 500:
        raise RetryLater(f"token endpoint {r.status_code}")
    if r.status_code != 200:
        error = r.json().get("error", "") if "json" in r.headers.get("content-type", "") else ""
        raise GiveUp(f"token endpoint refused the dispatcher: {r.status_code} {error}".strip())
    return str(r.json()["access_token"])


def minter_from_settings(s: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> Minter:
    async def minter(workspace_id: str, job_id: str) -> str:
        secret = s.dispatcher_client_secret
        if not secret and s.dispatcher_client_secret_arn:
            secret = secret_field(s.dispatcher_client_secret_arn, "client_secret")
        if not secret:
            raise GiveUp("dispatcher client secret is not configured (WF_DISPATCHER_CLIENT_SECRET[_ARN])")
        return await mint(s, workspace_id, job_id, client_secret=secret, transport=transport)

    return minter
