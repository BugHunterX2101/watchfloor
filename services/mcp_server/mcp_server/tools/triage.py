"""start_deep_triage and get_triage_result (J2, the warm path)."""

from __future__ import annotations

from watchfloor_core.models import tools as m
from watchfloor_core.principal import Scope
from watchfloor_core.services import triage

from ..resources import DEAL_DETAIL
from .base import ToolSpec, annotations
from .descriptions import DESCRIPTIONS

START = ToolSpec(
    name="start_deep_triage",
    scopes=frozenset({Scope.READ}),
    description=DESCRIPTIONS["start_deep_triage"],
    input_model=m.StartTriageIn,
    output_model=m.StartTriageOut,
    annotations=annotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True),
    handler=triage.start,
    budget_ms=100,
)

RESULT = ToolSpec(
    name="get_triage_result",
    scopes=frozenset({Scope.READ}),
    description=DESCRIPTIONS["get_triage_result"],
    input_model=m.GetTriageIn,
    output_model=m.TriageResultOut,
    annotations=annotations(readOnlyHint=True),
    handler=triage.get_result,
    ui_resource=DEAL_DETAIL,
    budget_ms=100,
)
