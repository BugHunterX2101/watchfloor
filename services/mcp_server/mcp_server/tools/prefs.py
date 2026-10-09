"""update_briefing_preferences."""

from __future__ import annotations

from watchfloor_core.models import tools as m
from watchfloor_core.principal import Scope
from watchfloor_core.services import updates

from .base import ToolSpec, annotations
from .descriptions import DESCRIPTIONS

SPEC = ToolSpec(
    name="update_briefing_preferences",
    scopes=frozenset({Scope.WRITE}),
    description=DESCRIPTIONS["update_briefing_preferences"],
    input_model=m.PrefsIn,
    output_model=m.PrefsOut,
    annotations=annotations(destructiveHint=False, idempotentHint=True),
    handler=updates.update_preferences,
    budget_ms=120,
)
