"""JSON Schema generation for tool contracts.

Pydantic models are the single source of truth (TD §20.2). MCP clients resolve
``$ref`` only within the schema document, and LLM tool callers fill flat schemas
more reliably, so we inline every ``$defs`` reference, drop auto-generated titles
and ``default: null`` noise, and collapse ``X | None`` into plain ``X``.
"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel


def flat_schema(model: type[BaseModel]) -> dict[str, Any]:
    raw = model.model_json_schema(mode="validation")
    defs = raw.pop("$defs", {})
    return _clean(_inline(raw, defs))


def flat_output_schema(model: type[BaseModel]) -> dict[str, Any]:
    raw = model.model_json_schema(mode="serialization")
    defs = raw.pop("$defs", {})
    return _clean(_inline(raw, defs))


def _inline(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            target = copy.deepcopy(defs[name])
            extra = {k: v for k, v in node.items() if k != "$ref"}
            return _inline({**target, **extra}, defs)
        return {k: _inline(v, defs) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline(v, defs) for v in node]
    return node


def _clean(node: Any) -> Any:
    if isinstance(node, dict):
        node = dict(node)
        node.pop("title", None)
        if "default" in node and node["default"] is None:
            node.pop("default")
        any_of = node.get("anyOf")
        if isinstance(any_of, list):
            non_null = [s for s in any_of if s != {"type": "null"}]
            if len(non_null) == 1 and len(non_null) != len(any_of):
                merged = {**{k: v for k, v in node.items() if k != "anyOf"}, **non_null[0]}
                return _clean(merged)
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k == "properties" and isinstance(v, dict):
                out[k] = {name: _clean(sub) for name, sub in v.items()}
            else:
                out[k] = _clean(v)
        return out
    if isinstance(node, list):
        return [_clean(v) for v in node]
    return node
