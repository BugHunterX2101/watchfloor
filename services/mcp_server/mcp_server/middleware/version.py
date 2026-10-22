"""MCP-Protocol-Version gate and initialize counter-offer (DD §5.2 #7, ADR-034).

Every message except ``initialize`` must carry a supported version; a present
but unsupported value is rejected even on ``initialize``. Stricter than the
spec's "assume 2025-03-26" fallback on purpose (CF-05, ⚠ S1).

The SDK answers ``initialize`` with the client's ``protocolVersion`` whenever
it knows that revision, even one this gate refuses on the very next request.
So an ``initialize`` offering a version outside ``allowed`` is rewritten to
offer the newest allowed one: the server counter-offers it, as the lifecycle
spec requires, and the client adopts it or disconnects.
"""

from __future__ import annotations

from collections.abc import Iterable

import orjson
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from ._http import is_mcp_post, replay, respond_jsonrpc_error, state


class ProtocolVersion:
    def __init__(self, app: ASGIApp, *, allowed: Iterable[str]) -> None:
        self.app = app
        self.allowed = frozenset(allowed)
        reachable = [v for v in HANDSHAKE_PROTOCOL_VERSIONS if v in self.allowed]  # oldest to newest
        self.counter_offer = reachable[-1] if reachable else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if is_mcp_post(scope):
            version = Headers(scope=scope).get("mcp-protocol-version")
            method = state(scope).get("jsonrpc_method")
            if (version is None and method != "initialize") or (
                version is not None and version not in self.allowed
            ):
                await respond_jsonrpc_error(send, 400, -32600, "Unsupported or missing MCP-Protocol-Version")
                return
            if method == "initialize":
                receive = await self._counter_offer(scope, receive)
        await self.app(scope, receive, send)

    async def _counter_offer(self, scope: Scope, receive: Receive) -> Receive:
        msg = state(scope).get("jsonrpc_message")
        params = msg.get("params") if isinstance(msg, dict) else None
        offered = params.get("protocolVersion") if isinstance(params, dict) else None
        if self.counter_offer is None or not isinstance(offered, str) or offered in self.allowed:
            return receive
        params["protocolVersion"] = self.counter_offer
        body = orjson.dumps(msg)
        await receive()  # drop the original body EdgeGuard buffered for replay; its stream is done
        headers = [(k, v) for k, v in scope["headers"] if k != b"content-length"]
        scope["headers"] = [*headers, (b"content-length", str(len(body)).encode())]
        return replay(body, receive)
