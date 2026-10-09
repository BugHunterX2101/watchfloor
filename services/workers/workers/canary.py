"""Synthetic canary (DD §17, TD §16.4): every 5 minutes, as a dedicated canary user.

It walks the real path a judge or Alexa+ takes: OAuth sign-in through the
harness client (authorize → login → consent → token, PKCE S256), then MCP
``tools/list`` and ``tools/call get_risk_briefing``. It emits ``CanarySuccess``
(1 or 0) and ``CanaryLatencyMs``; two failures in a row page (``wf-canary``).
The refresh token is revoked at the end, so canary runs leave nothing behind.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import html
import logging
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from watchfloor_core.settings import Settings, get_settings

from . import runtime

log = logging.getLogger("watchfloor.canary")
PROTOCOL_VERSION = "2025-11-25"
USER_SCOPE = "watchfloor.read watchfloor.write"
TIMEOUT_S = 10.0
_HIDDEN = re.compile(r'type="hidden" name="(\w+)" value="([^"]*)"')


class CanaryFailure(Exception):
    pass


@dataclass(frozen=True)
class Credentials:
    email: str
    password: str


def credentials_from_settings(s: Settings) -> Credentials:
    if s.canary_email and s.canary_password:
        return Credentials(s.canary_email, s.canary_password)
    if s.canary_secret_arn:
        return Credentials(
            runtime.secret_field(s.canary_secret_arn, "email"),
            runtime.secret_field(s.canary_secret_arn, "password"),
        )
    raise CanaryFailure("canary credentials are not configured (WF_CANARY_EMAIL/PASSWORD or _SECRET_ARN)")


def _hidden(page: str) -> dict[str, str]:
    return {k: html.unescape(v) for k, v in _HIDDEN.findall(page)}


def _expect(r: httpx.Response, status: int, step: str) -> None:
    if r.status_code != status:
        raise CanaryFailure(f"{step}: HTTP {r.status_code}, expected {status}")


async def sign_in(
    s: Settings, creds: Credentials, *, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[str, str | None]:
    """The account-linking flow, as a browser would do it. Returns (access, refresh) tokens."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(12)
    async with httpx.AsyncClient(base_url=s.issuer, transport=transport, timeout=TIMEOUT_S) as auth:
        r = await auth.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": s.harness_client_id,
                "redirect_uri": s.harness_redirect_uri,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
                "resource": s.resource_uri,
                "scope": USER_SCOPE,
            },
        )
        _expect(r, 200, "authorize")
        r = await auth.post(
            "/login", data={**_hidden(r.text), "email": creds.email, "password": creds.password}
        )
        _expect(r, 200, "login")
        r = await auth.post("/consent", data={**_hidden(r.text), "decision": "allow"})
        _expect(r, 303, "consent")
        query = parse_qs(urlparse(r.headers["location"]).query)
        if query.get("state") != [state] or query.get("iss") != [s.issuer]:
            raise CanaryFailure("consent: redirect state or issuer mismatch")
        if "code" not in query:
            raise CanaryFailure(f"consent: no code ({query.get('error', ['?'])[0]})")
        r = await auth.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": query["code"][0],
                "redirect_uri": s.harness_redirect_uri,
                "code_verifier": verifier,
                "client_id": s.harness_client_id,
                "resource": s.resource_uri,
            },
        )
        _expect(r, 200, "token")
        body = r.json()
    return body["access_token"], body.get("refresh_token")


async def revoke(
    s: Settings, refresh_token: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> None:
    try:
        async with httpx.AsyncClient(base_url=s.issuer, transport=transport, timeout=TIMEOUT_S) as auth:
            r = await auth.post(
                "/revoke",
                data={
                    "token": refresh_token,
                    "token_type_hint": "refresh_token",
                    "client_id": s.harness_client_id,
                },
            )
        if r.status_code != 200:
            log.warning("canary refresh token revocation returned %s", r.status_code)
    except httpx.HTTPError:
        log.warning("canary refresh token revocation failed", exc_info=True)


async def call_mcp(
    http: httpx.AsyncClient, url: str, token: str, method: str, params: dict[str, Any], rid: int
) -> dict[str, Any]:
    r = await http.post(
        url,
        json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params},
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        },
    )
    _expect(r, 200, method)
    body = r.json()
    if "error" in body:
        raise CanaryFailure(f"{method}: JSON-RPC {body['error'].get('code')} {body['error'].get('message')}")
    return body["result"]


async def run(
    *,
    settings: Settings | None = None,
    auth_transport: httpx.AsyncBaseTransport | None = None,
    mcp_transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    s = settings or get_settings()
    t0 = time.perf_counter()
    stage, ok, detail, refresh = "sign-in", False, "", None
    try:
        access, refresh = await sign_in(s, credentials_from_settings(s), transport=auth_transport)
        async with httpx.AsyncClient(transport=mcp_transport, timeout=TIMEOUT_S) as http:
            stage = "tools/list"
            listed = await call_mcp(http, s.mcp_url, access, "tools/list", {}, 1)
            if "get_risk_briefing" not in {t["name"] for t in listed.get("tools", [])}:
                raise CanaryFailure("tools/list: get_risk_briefing is missing")
            stage = "briefing"
            result = await call_mcp(
                http, s.mcp_url, access, "tools/call", {"name": "get_risk_briefing", "arguments": {}}, 2
            )
        structured = result.get("structuredContent") or {}
        if result.get("isError"):
            raise CanaryFailure(f"briefing: {structured.get('error', {}).get('code', 'error')}")
        if not structured.get("summary"):
            raise CanaryFailure("briefing: no summary")
        stage, ok = "done", True
    except (CanaryFailure, httpx.HTTPError, KeyError, ValueError) as exc:
        detail = f"{type(exc).__name__}: {exc}"
        log.error("canary failed at %s: %s", stage, detail)
    finally:
        if refresh:
            await revoke(s, refresh, transport=auth_transport)
    latency = runtime.elapsed_ms(t0)
    runtime.emit_metrics("canary", {"CanarySuccess": int(ok), "CanaryLatencyMs": latency})
    return {"ok": ok, "stage": stage, "latency_ms": latency, "detail": detail}


def handler(_event: object, _ctx: object) -> dict[str, Any]:
    """EventBridge Scheduler target, rate(5 minutes). Needs no database access."""
    return runtime.run_in_lambda(run())


def main(argv: list[str] | None = None) -> int:
    runtime.configure_logging()
    desc = "Sign in as the canary user, list tools and fetch a briefing."
    argparse.ArgumentParser(description=desc).parse_args(argv)
    result = asyncio.run(run())
    print(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
