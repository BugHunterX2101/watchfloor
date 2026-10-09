"""Reach a running MCP server as a real user: shared by ``conformance_remote.py`` and ``latency_probe.py``.

Token, in order:

1. ``--token`` or ``WF_MCP_TOKEN``.
2. Otherwise sign in as the canary user through the harness client (``workers.canary.sign_in``:
   authorize → login → consent → token, PKCE S256). The issuer comes from the server's RFC 9728
   document. Credentials come from ``WF_CANARY_EMAIL`` / ``WF_CANARY_PASSWORD`` (read from ``.env``
   for a local URL only), or,
   with ``--env dev|demo``, from the generated secret ``wf-<env>/canary/login``. The refresh token is
   revoked on exit, as the canary does.

The harness redirect URI must be one the auth server registered: ``WF_HARNESS_REDIRECT_URI`` when set;
otherwise ``https://harness.<domain>/oauth/callback`` for ``https://mcp.<domain>/mcp``, or the local
default for a local URL. Without ``--url``, ``--env dev|demo`` reads the MCP domain from
``infra/config/<env>.yaml``.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from demo import ROOT, config_value, load_env_file  # noqa: E402

PROTOCOL_VERSION = "2025-11-25"
LOCAL_URL = "http://localhost:8080/mcp"  # the canonical local resource URI, so PRM `resource` matches
LOCAL_REDIRECT = "http://localhost:8082/oauth/callback"
TIMEOUT_S = 15.0


def default_url(env: str | None) -> str:
    """The MCP endpoint for an environment: local, or ``https://<mcp_domain>/mcp`` from its config."""
    if env in (None, "local"):
        return LOCAL_URL
    domain = config_value(env, "mcp_domain")
    if not domain:
        raise SystemExit(
            f"no mcp_domain in infra/config/{env}.yaml (without custom domains, `make default-hosts "
            f"ENV={env}` writes it after the first deploy); or pass --url"
        )
    return f"https://{domain}/mcp"


def prm_url(url: str) -> str:
    """RFC 9728 path-suffixed metadata URL for a resource URL."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/.well-known/oauth-protected-resource{parts.path}"


def is_local(url: str) -> bool:
    return (urlsplit(url).hostname or "") in {"localhost", "127.0.0.1", "::1"}


def harness_redirect(url: str, env: str | None = None) -> str:
    """The harness client's registered redirect URI: set, from the config's harness_domain (the only way
    to know it on hosts AWS assigned), from a custom mcp.<domain>, or the local one."""
    if os.environ.get("WF_HARNESS_REDIRECT_URI"):
        return os.environ["WF_HARNESS_REDIRECT_URI"]
    harness = config_value(env, "harness_domain") if env in {"dev", "demo"} else None
    if harness:
        return f"https://{harness}/oauth/callback"
    host = urlsplit(url).hostname or ""
    if host.startswith("mcp."):
        return f"https://harness.{host.removeprefix('mcp.')}/oauth/callback"
    return LOCAL_REDIRECT


@dataclass
class Remote:
    """One signed-in caller talking JSON-RPC to the MCP endpoint at ``url``."""

    http: httpx.AsyncClient
    url: str
    token: str | None
    # rpc() spaces calls this far apart (the server allows 60 per user per minute) and waits out 429s.
    min_interval: float = 0.0
    _ids: count = field(default_factory=lambda: count(1))
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _next: float = 0.0

    async def _pace(self) -> None:
        async with self._lock:
            await asyncio.sleep(max(0.0, self._next - time.monotonic()))
            self._next = time.monotonic() + self.min_interval

    def headers(self, **extra: str) -> dict[str, str]:
        h = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        h.update(extra)
        return h

    async def post(self, payload: Any, **headers: str) -> httpx.Response:
        return await self.http.post(self.url, json=payload, headers=self.headers(**headers))

    async def rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": next(self._ids), "method": method}
        if params is not None:
            msg["params"] = params
        while True:
            await self._pace()
            r = await self.post(msg)
            if r.status_code != 429 or not self.min_interval:
                break
            await asyncio.sleep(float(r.headers.get("retry-after", "5")))
        if r.status_code != 200:
            raise AssertionError(f"{method}: HTTP {r.status_code} {r.text[:200]}")
        return r.json()

    async def tools(self) -> list[dict[str, Any]]:
        return (await self.rpc("tools/list"))["result"]["tools"]

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        env = await self.rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if "result" not in env:
            raise AssertionError(f"{name}: JSON-RPC error {env.get('error')}")
        return env["result"]


async def _settings(http: httpx.AsyncClient, url: str, env: str | None) -> Any:
    """Canary sign-in settings: issuer and resource from the server's RFC 9728 document."""
    from watchfloor_core.settings import Settings

    r = await http.get(prm_url(url))
    if r.status_code != 200:
        raise SystemExit(f"{prm_url(url)}: HTTP {r.status_code}; is the MCP server reachable?")
    prm = r.json()
    creds: dict[str, Any] = {
        "canary_email": os.environ.get("WF_CANARY_EMAIL") or None,
        "canary_password": os.environ.get("WF_CANARY_PASSWORD") or None,
    }
    if env in {"dev", "demo"} and not creds["canary_password"]:
        creds["canary_secret_arn"] = f"wf-{env}/canary/login"  # a name works as a SecretId too
    return Settings(
        issuer=prm["authorization_servers"][0],
        resource_uri=prm["resource"],
        mcp_url=url,
        harness_redirect_uri=harness_redirect(url, env),
        **creds,
    )


async def _sign_in(http: httpx.AsyncClient, url: str, env: str | None) -> tuple[str, str | None, Any]:
    from workers import canary

    settings = await _settings(http, url, env)
    try:
        access, refresh = await canary.sign_in(settings, canary.credentials_from_settings(settings))
    except canary.CanaryFailure as exc:
        raise SystemExit(f"canary sign-in failed: {exc}") from None
    return access, refresh, settings


@asynccontextmanager
async def connect(
    url: str, *, token: str | None = None, env: str | None = None, min_interval: float = 0.0
) -> AsyncIterator[Remote]:
    """A signed-in :class:`Remote`; with no token available, signs in as the canary user."""
    if is_local(url):  # .env holds the local stack's values; a deployed one uses its own secrets
        load_env_file(ROOT / ".env")
    token = token or os.environ.get("WF_MCP_TOKEN") or None
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as http:
        refresh, settings = None, None
        if not token:
            token, refresh, settings = await _sign_in(http, url, env)
        try:
            yield Remote(http, url, token, min_interval=min_interval)
        finally:
            if refresh and settings is not None:
                from workers import canary

                await canary.revoke(settings, refresh)


async def _token_command(command: str, url: str, env: str | None) -> dict[str, Any]:
    from workers import canary

    if is_local(url):
        load_env_file(ROOT / ".env")
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as http:
        if command == "sign-in":
            access, refresh, _ = await _sign_in(http, url, env)
            return {"access": access, "refresh": refresh}
        await canary.revoke(await _settings(http, url, env), sys.stdin.read().strip())
        return {}


if __name__ == "__main__":
    # For callers that can't run asyncio themselves (Locust runs under gevent, where it hangs):
    #   remote_mcp.py sign-in URL [ENV]   → {"access": ..., "refresh": ...} on stdout, for a pipe
    #   remote_mcp.py revoke URL [ENV]    ← the refresh token on stdin
    import json

    cmd, target, *rest = sys.argv[1:]
    if cmd not in {"sign-in", "revoke"}:
        raise SystemExit("usage: remote_mcp.py sign-in|revoke URL [ENV]")
    if cmd == "sign-in" and sys.stdout.isatty():
        raise SystemExit("sign-in writes tokens: pipe its output, don't print it")
    print(json.dumps(asyncio.run(_token_command(cmd, target, rest[0] if rest else None))))
