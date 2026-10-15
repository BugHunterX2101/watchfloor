"""Bearer JWT → Principal (DD §5.2 #6, §12.4).

ES256 only, verified against the authorization server's JWKS (cached 10 min,
refetched on an unknown ``kid`` at most every 30 s). ``iss`` and ``aud`` (the
canonical resource URI) are enforced with 60 s leeway. A 401 never carries
``WWW-Authenticate``: Alexa+ does not support it yet (constraint C3).

Scopes are narrowed by token kind as defence in depth: a job token (``job``
claim) can only hold ``watchfloor.agent.read``; a user token never can.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

import httpx
import jwt
import orjson
from jwt import PyJWK, PyJWKSet
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send
from watchfloor_core.principal import ALL_SCOPES, Principal
from watchfloor_core.principal import Scope as WfScope

from ..context import PRINCIPAL
from ._http import is_mcp_post, respond, respond_jsonrpc_error, state

log = logging.getLogger("watchfloor.mcp.auth")


# What an Alexa+ service token (mcp:service) may call: discovery and the static ui:// views.
SERVICE_METHODS = frozenset(
    {
        "initialize",
        "notifications/initialized",
        "ping",
        "tools/list",
        "resources/list",
        "resources/templates/list",
        "resources/read",
    }
)


class InvalidToken(Exception):
    pass


class JwksUnavailable(Exception):
    pass


class JwksCache:
    """Public keys by ``kid``. ``static`` (tests) or a ``file:`` URL (local dev) skip the network."""

    def __init__(
        self,
        url: str | None = None,
        *,
        static: dict[str, Any] | None = None,
        ttl_s: float = 600,
        min_refetch_s: float = 30,
    ) -> None:
        if url is None and static is None:
            raise ValueError("JwksCache needs a url or static keys")
        self.url = url
        self.static = static
        self.ttl_s = ttl_s
        self.min_refetch_s = min_refetch_s
        self._keys: dict[str, PyJWK] | None = None
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def key(self, kid: str | None) -> PyJWK | None:
        if not kid:
            return None
        if self._keys is None or time.monotonic() - self._fetched_at > self.ttl_s:
            await self._refresh(self._fetched_at)
        found = (self._keys or {}).get(kid)
        if found is None and time.monotonic() - self._fetched_at > self.min_refetch_s:
            await self._refresh(self._fetched_at)  # key rotation: an unknown kid may be brand new
            found = (self._keys or {}).get(kid)
        return found

    async def _refresh(self, seen: float) -> None:
        async with self._lock:
            if self._fetched_at != seen:
                return  # another request refreshed while this one waited: one fetch serves both
            try:
                doc = await self._load()
                keys = {k.key_id: k for k in PyJWKSet.from_dict(doc).keys if k.key_id}
            except Exception as exc:
                if self._keys is None:
                    raise JwksUnavailable(str(exc)) from exc
                log.warning("JWKS refresh failed; keeping cached keys: %s", exc)
                self._fetched_at = time.monotonic()
                return
            self._keys = keys
            self._fetched_at = time.monotonic()

    async def _load(self) -> dict[str, Any]:
        if self.static is not None:
            return self.static
        assert self.url is not None
        if self.url.startswith("file:"):
            path = Path(url2pathname(urlparse(self.url).path))
            return orjson.loads(await asyncio.to_thread(path.read_bytes))
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(self.url)
            r.raise_for_status()
            return r.json()


def principal_from_claims(claims: dict[str, Any]) -> Principal:
    ws = claims.get("ws")
    sub = claims.get("sub")
    scopes = frozenset(str(claims.get("scope", "")).split()) & ALL_SCOPES
    if WfScope.SERVICE in scopes:
        # Alexa+ Tier 1: a service token names no user, workspace or job, and holds nothing else.
        if scopes != {WfScope.SERVICE} or ws or claims.get("job") or not isinstance(sub, str) or not sub:
            raise InvalidToken("service token with user or job claims")
        return Principal(user_id="", workspace_id="", scopes=scopes, subject=sub)
    if not isinstance(ws, str) or not ws or not isinstance(sub, str) or not sub:
        raise InvalidToken("missing ws or sub")
    job = claims.get("job")
    if job:
        scopes &= {WfScope.AGENT_READ}
        if not scopes:
            raise InvalidToken("job token without agent scope")
        return Principal(user_id="", workspace_id=ws, scopes=scopes, job_id=str(job), subject=f"job:{job}")
    scopes -= {WfScope.AGENT_READ}
    if not scopes:
        raise InvalidToken("no usable scope")
    return Principal(user_id=sub, workspace_id=ws, scopes=scopes, subject=sub)


class BearerAuth:
    def __init__(
        self, app: ASGIApp, *, issuer: str, audience: str, jwks: JwksCache, leeway: int = 60
    ) -> None:
        self.app = app
        self.issuer = issuer
        self.audience = audience
        self.jwks = jwks
        self.leeway = leeway

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not is_mcp_post(scope):  # well-known, healthz and 405s are public
            await self.app(scope, receive, send)
            return
        raw = Headers(scope=scope).get("authorization", "")
        kind, _, token = raw.partition(" ")
        if kind.lower() != "bearer" or not token.strip():
            await respond(send, 401)
            return
        try:
            principal = await self.verify(token.strip())
        except JwksUnavailable:
            log.error("JWKS unavailable; cannot verify tokens")
            await respond_jsonrpc_error(
                send, 503, -32603, "Authorization temporarily unavailable", headers=[(b"retry-after", b"5")]
            )
            return
        except (jwt.PyJWTError, InvalidToken) as exc:
            log.info("rejected bearer token: %s", type(exc).__name__)
            await respond(send, 401)
            return
        if principal.is_service() and state(scope).get("jsonrpc_method") not in SERVICE_METHODS:
            await respond(send, 403)  # user data needs a linked account: Alexa+ starts linking on 403
            return
        state(scope)["principal"] = principal
        reset = PRINCIPAL.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            PRINCIPAL.reset(reset)

    async def verify(self, token: str) -> Principal:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "ES256":
            raise InvalidToken("alg")
        key = await self.jwks.key(header.get("kid"))
        if key is None:
            raise InvalidToken("unknown kid")
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["ES256"],
            audience=self.audience,
            issuer=self.issuer,
            leeway=self.leeway,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
        return principal_from_claims(claims)
