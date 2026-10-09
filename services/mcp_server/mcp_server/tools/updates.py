"""log_deal_update: voice CRM updates (J5)."""

from __future__ import annotations

from watchfloor_core.models import tools as m
from watchfloor_core.principal import Scope
from watchfloor_core.services import updates

from ..resources import CONFIRM_SHEET
from .base import ToolSpec, annotations
from .descriptions import DESCRIPTIONS

SPEC = ToolSpec(
    name="log_deal_update",
    scopes=frozenset({Scope.WRITE}),
    description=DESCRIPTIONS["log_deal_update"],
    input_model=m.LogUpdateIn,
    output_model=m.LogUpdateOut,
    annotations=annotations(destructiveHint=False),
    handler=updates.log_update,
    ui_resource=CONFIRM_SHEET,  # shown when a close-date or amount change is pending
    budget_ms=200,
)
