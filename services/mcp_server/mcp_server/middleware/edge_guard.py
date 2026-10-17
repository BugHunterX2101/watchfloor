"""Host, Origin, Accept and body checks before anything else runs (DD §5.2, TD §6.2).

Order: Host → 421, Origin → 403 (a MUST in 2025-11-25, against DNS rebinding),
then for ``POST /mcp``: Accept → 406, body > 64 KiB → 413, not JSON → 400 with
a JSON-RPC parse error. The parsed method is stashed for the version check, and
the buffered body is replayed downstream.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

import orjson
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from ._http import is_mcp_post, read_body, replay, respond, respond_jsonrpc_error, state

MAX_BODY = 65_536

# uri-host [":" port] and nothing else: no userinfo, no list, no spaces. A bracketed IPv6 literal or a DNS name.
_HOST = re.compile(r"(?P<name>\[[0-9a-f:.]+\]|[a-z0-9._-]+)(?::(?P<port>[0-9]{1,5}))?")


def host_name(host_header: str) -> str | None:
    """The lower-case host of a Host header, or None when it is not a well-formed ``host[:port]``.

    Splitting on the first colon would read ``mcp.example:80@evil.example`` as ``mcp.example``.
    """
    m = _HOST.fullmatch(host_header.strip().lower())
    if m is None or (m["port"] is not None and int(m["port"]) > 65_535):
        return None
    name = m["name"]
    return name[1:-1] if name.startswith("[") else name


def _origin_key(origin: str) -> str:
    """Origins compare by their lower-case serialisation, without a trailing slash a config typo may add."""
    return origin.strip().lower().rstrip("/")


class EdgeGuard:
    def __init__(
        self,
        app: ASGIApp,
        *,
        hosts: Iterable[str],
        origins: Iterable[str],
        max_body: int = MAX_BODY,
        host_header: str = "host",
    ) -> None:
        self.app = app
        self.hosts = frozenset(h.lower() for h in hosts)
        self.origins = frozenset(k for k in map(_origin_key, origins) if k)  # "" must never match "Origin:"
        self.max_body = max_body
        # "host", or on ECS the header the HTTP API sets to its own domain (Settings.host_header)
        self.host_header = host_header

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        h = Headers(scope=scope)
        # Exactly one value of each: with two, the app and a proxy could read different ones.
        hosts = h.getlist(self.host_header)
        name = host_name(hosts[0]) if len(hosts) == 1 else None
        if name is None or name not in self.hosts:
            await respond(send, 421)
            return
        origins = h.getlist("origin")
        if origins and (len(origins) > 1 or _origin_key(origins[0]) not in self.origins):
            await respond_jsonrpc_error(send, 403, -32600, "Origin not allowed")
            return
        if is_mcp_post(scope):
            accept = h.get("accept", "")
            if "application/json" not in accept or "text/event-stream" not in accept:
                await respond(send, 406)
                return
            declared = h.get("content-length")
            if declared is not None and declared.isdigit() and int(declared) > self.max_body:
                await respond(send, 413)
                return
            body = await read_body(receive, self.max_body)
            if body is None:
                await respond(send, 413)
                return
            try:
                msg = orjson.loads(body)
            except orjson.JSONDecodeError:
                await respond_jsonrpc_error(send, 400, -32700, "Parse error")
                return
            state(scope)["jsonrpc_method"] = msg.get("method") if isinstance(msg, dict) else None
            receive = replay(body, receive)
        await self.app(scope, receive, send)
