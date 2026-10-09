"""get_risk_briefing (J1, the hot path)."""

from __future__ import annotations

from watchfloor_core.models import tools as m
from watchfloor_core.principal import Scope
from watchfloor_core.services import read

from ..resources import TRIAGE_BOARD
from .base import ToolSpec, annotations
from .descriptions import DESCRIPTIONS

# Touches users.last_briefing_at, which is session bookkeeping, so it stays read-only (DD §6.2).
SPEC = ToolSpec(
    name="get_risk_briefing",
    scopes=frozenset({Scope.READ}),
    description=DESCRIPTIONS["get_risk_briefing"],
    input_model=m.BriefingIn,
    output_model=m.BriefingOut,
    annotations=annotations(readOnlyHint=True, openWorldHint=False),
    handler=read.build_briefing,
    ui_resource=TRIAGE_BOARD,
    budget_ms=150,
)
