"""Streamable HTTP manager: stateless (no Mcp-Session-Id) with plain JSON responses (DD §5.3)."""

from __future__ import annotations

from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from .middleware import MAX_BODY


def build_session_manager(server: Server) -> StreamableHTTPSessionManager:
    # Host/Origin checks live in EdgeGuard, so the SDK's transport security stays off.
    return StreamableHTTPSessionManager(
        app=server, stateless=True, json_response=True, max_request_body_size=MAX_BODY
    )
