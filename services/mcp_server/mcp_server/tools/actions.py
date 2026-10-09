"""propose_action and confirm_action: the two-tool confirmation loop (J3, DD §8)."""

from __future__ import annotations

from watchfloor_core.models import tools as m
from watchfloor_core.principal import Scope
from watchfloor_core.services import actions

from ..resources import CONFIRM_SHEET
from .base import ToolSpec, annotations
from .descriptions import DESCRIPTIONS

# A pending proposal has no business effect, so propose is read-only to the customer (DD §6.2).
PROPOSE = ToolSpec(
    name="propose_action",
    scopes=frozenset({Scope.READ}),
    description=DESCRIPTIONS["propose_action"],
    input_model=m.ProposeIn,
    output_model=m.ProposalOut,
    annotations=annotations(readOnlyHint=True),
    handler=actions.propose,
    ui_resource=CONFIRM_SHEET,
    budget_ms=120,
)

CONFIRM = ToolSpec(
    name="confirm_action",
    scopes=frozenset({Scope.WRITE}),
    description=DESCRIPTIONS["confirm_action"],
    input_model=m.ConfirmIn,
    output_model=m.ConfirmOut,
    annotations=annotations(destructiveHint=True, idempotentHint=True),
    handler=actions.confirm,
    budget_ms=300,
)
