"""Fixed one-minute windows in Postgres: 60/min per subject, 600/min per workspace (DD §5.2 #8).

Fails open: if the counter table is unreachable the request proceeds, because
the tool call itself will then report TEMPORARILY_UNAVAILABLE honestly.
"""

from __future__ import annotations

import logging
import time

from starlette.types import ASGIApp, Receive, Scope, Send
from watchfloor_core.db import system_tx
from watchfloor_core.repos import misc

from ._http import is_mcp_post, respond_jsonrpc_error, state

log = logging.getLogger("watchfloor.mcp.ratelimit")


class RateLimit:
    def __init__(self, app: ASGIApp, *, per_user: int, per_ws: int) -> None:
        self.app = app
        self.per_user = per_user
        self.per_ws = per_ws

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        principal = state(scope).get("principal") if is_mcp_post(scope) else None
        if principal is None:
            await self.app(scope, receive, send)
            return
        try:
            async with system_tx() as conn:
                n_subject = await misc.rate_hit(conn, f"sub:{principal.rate_subject}")
                n_ws = await misc.rate_hit(conn, f"ws:{principal.workspace_id}")
        except Exception as exc:
            log.warning("rate limiter unavailable, failing open: %s", type(exc).__name__)
            await self.app(scope, receive, send)
            return
        if n_subject > self.per_user or n_ws > self.per_ws:
            retry_after = max(1, 60 - int(time.time()) % 60)
            await respond_jsonrpc_error(
                send,
                429,
                -32000,
                "Rate limit exceeded",
                headers=[(b"retry-after", str(retry_after).encode())],
            )
            return
        await self.app(scope, receive, send)
