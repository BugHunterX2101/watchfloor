"""MCP-Protocol-Version gate (DD §5.2 #7).

Every message except ``initialize`` must carry a supported version; a present
but unsupported value is rejected even on ``initialize``. Stricter than the
spec's "assume 2025-03-26" fallback on purpose (CF-05, ⚠ S1).
"""

from __future__ import annotations

from collections.abc import Iterable

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from ._http import is_mcp_post, respond_jsonrpc_error, state


class ProtocolVersion:
    def __init__(self, app: ASGIApp, *, allowed: Iterable[str]) -> None:
        self.app = app
        self.allowed = frozenset(allowed)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if is_mcp_post(scope):
            version = Headers(scope=scope).get("mcp-protocol-version")
            method = state(scope).get("jsonrpc_method")
            if (version is None and method != "initialize") or (
                version is not None and version not in self.allowed
            ):
                await respond_jsonrpc_error(send, 400, -32600, "Unsupported or missing MCP-Protocol-Version")
                return
        await self.app(scope, receive, send)
