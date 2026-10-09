"""Tool specification and outcome types (TD §6.2).

A ToolSpec binds a name, the scopes that may see it, its exact description,
Pydantic input/output models (the source of both JSON Schemas) and a handler.
Handlers take a ServiceContext plus the validated input model and return the
output model; business failures are raised as WatchfloorError and become an
``isError`` result with a structured ``error`` block.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import cached_property
from typing import Any

import mcp_types as types
import orjson
from pydantic import BaseModel
from watchfloor_core.errors import WatchfloorError
from watchfloor_core.models.schema import flat_output_schema, flat_schema
from watchfloor_core.principal import Principal
from watchfloor_core.services.context import ServiceContext

Handler = Callable[[ServiceContext, Any], Awaitable[BaseModel]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    scopes: frozenset[str]  # visible to a principal holding any of these
    description: str  # exact text from DD §6.3
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    annotations: types.ToolAnnotations
    handler: Handler
    ui_resource: str | None = None
    budget_ms: int = 150  # server p95 budget (DD §6.2)

    def visible_to(self, principal: Principal) -> bool:
        return bool(self.scopes & principal.scopes)

    @cached_property
    def descriptor(self) -> types.Tool:
        payload: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": flat_schema(self.input_model),
            "outputSchema": flat_output_schema(self.output_model),
            "annotations": self.annotations.model_dump(by_alias=True, exclude_none=True),
        }
        if self.ui_resource:
            payload["_meta"] = {"ui": {"resourceUri": self.ui_resource}}  # MCP Apps (⚠ S3)
        return types.Tool.model_validate(payload)


@dataclass
class ToolOutcome:
    structured: dict[str, Any]
    is_error: bool = False
    error_code: str | None = None

    @classmethod
    def ok(cls, out: BaseModel) -> ToolOutcome:
        # Unset optionals are omitted, matching the flat output schemas.
        return cls(out.model_dump(mode="json", exclude_none=True))

    @classmethod
    def error(cls, exc: WatchfloorError) -> ToolOutcome:
        return cls(exc.to_payload(), is_error=True, error_code=exc.code.value)

    def to_result(self) -> types.CallToolResult:
        text = orjson.dumps(self.structured).decode()
        return types.CallToolResult.model_validate(
            {
                "content": [{"type": "text", "text": text}],
                "structuredContent": self.structured,
                "isError": self.is_error,
            }
        )


def annotations(**hints: bool) -> types.ToolAnnotations:
    """``annotations(readOnlyHint=True)`` with the wire names used in DD §6.2."""
    return types.ToolAnnotations.model_validate(hints)
