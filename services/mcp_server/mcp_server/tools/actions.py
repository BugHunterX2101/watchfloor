"""propose_action and confirm_action: the two-tool confirmation loop (J3, DD §8), with
extend_preview and undo_action on either side of it (ADR-021)."""

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

# More time keeps the same token alive a little longer; like propose, it has no business effect.
EXTEND = ToolSpec(
    name="extend_preview",
    scopes=frozenset({Scope.READ}),
    description=DESCRIPTIONS["extend_preview"],
    input_model=m.ExtendIn,
    output_model=m.ExtendOut,
    annotations=annotations(readOnlyHint=True),
    handler=actions.extend,
    budget_ms=120,
)

UNDO = ToolSpec(
    name="undo_action",
    scopes=frozenset({Scope.WRITE}),
    description=DESCRIPTIONS["undo_action"],
    input_model=m.UndoIn,
    output_model=m.UndoOut,
    annotations=annotations(destructiveHint=True, idempotentHint=True),
    handler=actions.undo,
    budget_ms=300,
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
