"""Low-level MCP server: tools/list, tools/call and ui:// resources (TD §6.2).

Error layering (DD §5.5):
- unknown or out-of-scope tool, or schema-violating arguments → JSON-RPC -32602
- business outcome (not found, expired token…) → result with ``isError: true``
- database unreachable or timed out → ``isError`` with TEMPORARILY_UNAVAILABLE
- anything else → JSON-RPC -32603 with no internals

Each call runs in one workspace-scoped transaction. A WatchfloorError is caught
inside it so partial bookkeeping (a proposal marked failed) and the audit row
still commit. Outbox rows are handed to the queue only after COMMIT.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import asyncpg
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.shared.exceptions import MCPError
from pydantic import ValidationError
from watchfloor_core import queue
from watchfloor_core.db import DB_UNAVAILABLE, ws_tx
from watchfloor_core.errors import ErrorCode, WatchfloorError
from watchfloor_core.principal import Principal
from watchfloor_core.repos import misc
from watchfloor_core.services.actions import POST_HOLD
from watchfloor_core.services.context import (
    ServiceContext,
    UnknownUser,
    UnknownWorkspace,
    clock,
    make_context,
)
from watchfloor_core.tokens import args_hash

from . import __version__, resources
from .context import current_principal, current_trace_id
from .middleware.telemetry import tool_record
from .tools import REGISTRY, ToolOutcome, ToolSpec, visible

log = logging.getLogger("watchfloor.mcp.server")

INSTRUCTIONS = (
    "Watchfloor reports B2B pipeline risk. Prefer get_risk_briefing for open-ended "
    "questions. Never execute actions without propose_action then confirm_action."
)

RESOURCE_NOT_FOUND = -32002


def _unavailable() -> WatchfloorError:
    return WatchfloorError(
        ErrorCode.TEMPORARILY_UNAVAILABLE,
        "Watchfloor can't reach its data right now.",
        recovery="Try again in a minute.",
    )


def first_error(tool: str, exc: ValidationError) -> str:
    """One field-specific message for -32602, e.g. ``deal_id: String should match pattern …``."""
    err = exc.errors(include_url=False)[0]
    loc = ".".join(str(part) for part in err["loc"])
    msg = str(err["msg"]).removeprefix("Value error, ")
    return f"Invalid arguments for {tool}: {loc + ': ' if loc else ''}{msg}"


async def list_tools(ctx: Any, params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
    principal = current_principal(ctx)
    return types.ListToolsResult(tools=[spec.descriptor for spec in visible(principal)])


async def call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
    principal = current_principal(ctx)
    spec = REGISTRY.get(params.name)
    if spec is None or not spec.visible_to(principal):  # out-of-scope looks unknown (delta T2)
        raise MCPError(code=types.INVALID_PARAMS, message=f"Unknown tool: {params.name}")
    arguments = params.arguments or {}
    try:
        args = spec.input_model.model_validate(arguments)
    except ValidationError as exc:
        raise MCPError(code=types.INVALID_PARAMS, message=first_error(spec.name, exc)) from None
    return await _run(spec, principal, args, arguments, current_trace_id(ctx))


async def _run(
    spec: ToolSpec, principal: Principal, args: Any, arguments: dict[str, Any], trace_id: str | None
) -> types.CallToolResult:
    t0 = time.perf_counter()
    user_id: str | None = principal.user_id or None
    outbox_ids: list[int] = []
    held_ids: list[int] = []
    try:
        async with ws_tx(principal.workspace_id) as conn:
            sctx: ServiceContext | None = None
            try:
                sctx = await make_context(conn, principal, trace_id)
                user_id = sctx.user_id
                outcome = ToolOutcome.ok(await spec.handler(sctx, args))
            except WatchfloorError as exc:
                outcome = ToolOutcome.error(exc)
            if sctx is not None:
                outbox_ids = list(sctx.outbox_ids)
                held_ids = list(sctx.held_outbox_ids)
            await _audit(conn, spec, principal, user_id, arguments, outcome, _ms(t0), trace_id)
    except UnknownWorkspace:
        # A valid signature for a workspace that no longer exists: nothing to read, nothing to audit.
        log.warning("token names unknown workspace %s", principal.workspace_id)
        raise MCPError(code=types.INVALID_REQUEST, message="Unknown workspace") from None
    except UnknownUser:
        # A valid signature for someone this workspace does not have: no data, and no audit row to
        # attribute to a user who is not there.
        log.warning(
            "token names a user %s that workspace %s does not have", principal.user_id, principal.workspace_id
        )
        raise MCPError(code=types.INVALID_REQUEST, message="Unknown user") from None
    except DB_UNAVAILABLE as exc:
        log.warning("database unavailable during %s: %s", spec.name, type(exc).__name__)
        outcome = ToolOutcome.error(_unavailable())
        outbox_ids = held_ids = []
    except Exception:
        log.exception("tool %s failed", spec.name)
        await _audit_best_effort(spec, principal, user_id, arguments, _ms(t0), trace_id)
        _record(spec, principal, user_id, _ms(t0), "internal", "INTERNAL", trace_id)
        raise MCPError(code=types.INTERNAL_ERROR, message="Internal error") from None

    await queue.publish(principal.workspace_id, outbox_ids)
    # Slack posts held for the undo window are delivered when it closes (ADR-021).
    await queue.publish(principal.workspace_id, held_ids, delay_s=int(POST_HOLD.total_seconds()))
    latency = _ms(t0)
    _record(
        spec, principal, user_id, latency, "error" if outcome.is_error else "ok", outcome.error_code, trace_id
    )
    if latency > spec.budget_ms:
        log.info("%s over budget: %d ms > %d ms", spec.name, latency, spec.budget_ms)
    return outcome.to_result()


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


async def _audit(
    conn: asyncpg.Connection,
    spec: ToolSpec,
    principal: Principal,
    user_id: str | None,
    arguments: dict[str, Any],
    outcome: ToolOutcome,
    latency_ms: int,
    trace_id: str | None,
) -> None:
    await misc.record_audit(
        conn,
        workspace_id=principal.workspace_id,
        user_id=user_id,
        tool=spec.name,
        args_hash=args_hash(arguments),
        outcome="error" if outcome.is_error else "ok",
        error_code=outcome.error_code,
        latency_ms=latency_ms,
        trace_id=trace_id,
        at=clock().now(),
    )


async def _audit_best_effort(
    spec: ToolSpec,
    principal: Principal,
    user_id: str | None,
    arguments: dict[str, Any],
    latency_ms: int,
    trace_id: str | None,
) -> None:
    try:
        async with ws_tx(principal.workspace_id) as conn:
            await misc.record_audit(
                conn,
                workspace_id=principal.workspace_id,
                user_id=user_id,
                tool=spec.name,
                args_hash=args_hash(arguments),
                outcome="internal",
                error_code="INTERNAL",
                latency_ms=latency_ms,
                trace_id=trace_id,
                at=clock().now(),
            )
    except Exception:
        log.warning("could not audit failed call to %s", spec.name)


def _record(
    spec: ToolSpec,
    principal: Principal,
    user_id: str | None,
    latency_ms: int,
    outcome: str,
    error_code: str | None,
    trace_id: str | None,
) -> None:
    tool_record(
        tool=spec.name,
        ws=principal.workspace_id,
        user=user_id or principal.subject or None,
        latency_ms=latency_ms,
        outcome=outcome,
        error_code=error_code,
        trace_id=trace_id,
    )


async def list_resources(ctx: Any, params: types.PaginatedRequestParams | None) -> types.ListResourcesResult:
    current_principal(ctx)
    return types.ListResourcesResult(resources=resources.descriptors())


async def read_resource(ctx: Any, params: types.ReadResourceRequestParams) -> types.ReadResourceResult:
    current_principal(ctx)
    uri = str(params.uri)
    html = resources.load_ui(uri)
    if html is None:
        raise MCPError(code=RESOURCE_NOT_FOUND, message="Resource not found", data={"uri": uri})
    return types.ReadResourceResult.model_validate(
        {"contents": [{"uri": uri, "mimeType": resources.MIME_TYPE, "text": html}]}
    )


def build_server() -> Server:
    return Server(
        "watchfloor",
        version=__version__,
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_list_resources=list_resources,
        on_read_resource=read_resource,
    )
