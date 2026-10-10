"""Tool registry in catalogue order (DD §6.2, §6.5); tools/list filters it by token scope."""

from __future__ import annotations

from watchfloor_core.principal import Principal

from . import actions, agent_read, briefing, explain, lookup, prefs, rules, triage, updates
from .base import ToolOutcome, ToolSpec

ORDERED: tuple[ToolSpec, ...] = (
    briefing.SPEC,
    explain.SPEC,
    lookup.SPEC,
    triage.START,
    triage.RESULT,
    actions.PROPOSE,
    actions.EXTEND,
    rules.LIST,
    actions.CONFIRM,
    actions.UNDO,
    rules.SET,
    rules.REMOVE,
    updates.SPEC,
    prefs.SPEC,
    *agent_read.SPECS,
)

REGISTRY: dict[str, ToolSpec] = {spec.name: spec for spec in ORDERED}


def visible(principal: Principal) -> list[ToolSpec]:
    return [spec for spec in ORDERED if spec.visible_to(principal)]


__all__ = ["ORDERED", "REGISTRY", "ToolOutcome", "ToolSpec", "visible"]
