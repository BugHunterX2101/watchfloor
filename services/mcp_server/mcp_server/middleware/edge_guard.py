"""Host, Origin, Accept and body checks before anything else runs (DD §5.2, TD §6.2).

Order: Host → 421, Origin → 403 (a MUST in 2025-11-25, against DNS rebinding),
then for ``POST /mcp``: Accept → 406, body > 64 KiB → 413, not JSON → 400 with
a JSON-RPC parse error. The parsed method is stashed for the version check, and
the buffered body is replayed downstream.
"""

from __future__ import annotations

from collections.abc import Iterable

import orjson
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from ._http import is_mcp_post, read_body, replay, respond, respond_jsonrpc_error, state

MAX_BODY = 65_536


def host_name(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("["):  # IPv6 literal, e.g. [::1]:8080
        return host[1 : host.find("]")] if "]" in host else host
    return host.split(":", 1)[0]


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
        self.origins = frozenset(origins)
        self.max_body = max_body
        # "host", or on ECS the header the HTTP API sets to its own domain (Settings.host_header)
        self.host_header = host_header

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        h = Headers(scope=scope)
        if host_name(h.get(self.host_header, "")) not in self.hosts:
            await respond(send, 421)
            return
        origin = h.get("origin")
        if origin is not None and origin not in self.origins:
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
