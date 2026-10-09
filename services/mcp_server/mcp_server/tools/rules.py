"""set_standing_order, list_standing_orders and remove_standing_order (J4)."""

from __future__ import annotations

from watchfloor_core.models import tools as m
from watchfloor_core.principal import Scope
from watchfloor_core.services import rules as rule_service
from watchfloor_core.services.context import ServiceContext

from .base import ToolSpec, annotations
from .descriptions import DESCRIPTIONS


async def _list(ctx: ServiceContext, args: m.ListRulesIn) -> m.ListRulesOut:
    return await rule_service.list_rules(ctx)


SET = ToolSpec(
    name="set_standing_order",
    scopes=frozenset({Scope.WRITE}),
    description=DESCRIPTIONS["set_standing_order"],
    input_model=m.SetRuleIn,
    output_model=m.SetRuleOut,
    annotations=annotations(destructiveHint=False),
    handler=rule_service.create_rule,
    budget_ms=150,
)

LIST = ToolSpec(
    name="list_standing_orders",
    scopes=frozenset({Scope.READ}),
    description=DESCRIPTIONS["list_standing_orders"],
    input_model=m.ListRulesIn,
    output_model=m.ListRulesOut,
    annotations=annotations(readOnlyHint=True),
    handler=_list,
    budget_ms=100,
)

REMOVE = ToolSpec(
    name="remove_standing_order",
    scopes=frozenset({Scope.WRITE}),
    description=DESCRIPTIONS["remove_standing_order"],
    input_model=m.RemoveRuleIn,
    output_model=m.RemoveRuleOut,
    annotations=annotations(destructiveHint=True),
    handler=rule_service.remove_rule,
    budget_ms=150,
)
