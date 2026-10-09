"""Request-scoped principal and trace id for MCP handlers (TD §6.2).

``BearerAuth`` stores the principal in ``scope["state"]`` and in a ContextVar.
Handlers read the HTTP request the SDK hands them first and fall back to the
ContextVar; CF-04 (agent sees exactly 6 tools) would catch a propagation gap.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_REQUEST
from watchfloor_core.principal import Principal

PRINCIPAL: ContextVar[Principal | None] = ContextVar("wf_principal", default=None)


def _scope_state(ctx: Any) -> dict[str, Any]:
    request = getattr(ctx, "request", None)
    scope = getattr(request, "scope", None)
    return scope.get("state", {}) if isinstance(scope, dict) else {}


def current_principal(ctx: Any = None) -> Principal:
    principal = _scope_state(ctx).get("principal") or PRINCIPAL.get()
    if principal is None:
        raise MCPError(code=INVALID_REQUEST, message="Unauthenticated")
    return principal


def current_trace_id(ctx: Any = None) -> str | None:
    from .middleware.telemetry import TRACE_ID

    return _scope_state(ctx).get("trace_id") or TRACE_ID.get()
