"""Trace ids, request timing and the one-line-per-tool-call log record (TD §16.1).

A W3C ``traceparent`` is honoured when present and generated otherwise; the
response echoes it. Tool records carry an embedded-metric-format block so
CloudWatch derives ``ToolLatencyMs{tool}`` without a metrics API call. Never
logged: tokens, headers, argument values or CRM text.
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from contextvars import ContextVar

import orjson
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from watchfloor_core.services.context import clock

from ._http import state

log = logging.getLogger("watchfloor.mcp")

TRACE_ID: ContextVar[str | None] = ContextVar("wf_trace_id", default=None)
_TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
_cold_start = True


def parse_traceparent(value: str | None) -> str | None:
    m = _TRACEPARENT.match((value or "").strip().lower())
    if m is None or m.group(1) == "0" * 32:
        return None
    return m.group(1)


class Telemetry:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        trace_id = parse_traceparent(Headers(scope=scope).get("traceparent")) or secrets.token_hex(16)
        traceparent = f"00-{trace_id}-{secrets.token_hex(8)}-01".encode()
        state(scope)["trace_id"] = trace_id
        token = TRACE_ID.set(trace_id)
        t0 = time.perf_counter()
        status = 0

        async def send_with_trace(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {**message, "headers": [*message.get("headers", []), (b"traceparent", traceparent)]}
            await send(message)

        try:
            await self.app(scope, receive, send_with_trace)
        finally:
            TRACE_ID.reset(token)
            log.debug(
                "http %s %s -> %s in %.1f ms [%s]",
                scope["method"],
                scope["path"],
                status,
                (time.perf_counter() - t0) * 1000,
                trace_id,
            )


def tool_record(
    *,
    tool: str,
    ws: str,
    user: str | None,
    latency_ms: int,
    outcome: str,
    error_code: str | None,
    trace_id: str | None,
) -> None:
    global _cold_start
    now = clock().now()
    record = {
        "level": "INFO",
        "service": "mcp-server",
        "timestamp": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "message": "tool_call",
        "trace_id": trace_id,
        "tool": tool,
        "ws": ws,
        "user": user,
        "latency_ms": latency_ms,
        "outcome": outcome,
        "error_code": error_code,
        "cold_start": _cold_start,
        "ToolLatencyMs": latency_ms,
        "_aws": {
            "Timestamp": int(now.timestamp() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": "Watchfloor",
                    "Dimensions": [["tool"]],
                    "Metrics": [{"Name": "ToolLatencyMs", "Unit": "Milliseconds"}],
                }
            ],
        },
    }
    _cold_start = False
    log.info(orjson.dumps(record).decode())
