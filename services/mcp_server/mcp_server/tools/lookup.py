"""lookup_deal: name → deal_id candidates."""

from __future__ import annotations

from watchfloor_core.models import tools as m
from watchfloor_core.principal import Scope
from watchfloor_core.services import read

from .base import ToolSpec, annotations
from .descriptions import DESCRIPTIONS

SPEC = ToolSpec(
    name="lookup_deal",
    scopes=frozenset({Scope.READ}),
    description=DESCRIPTIONS["lookup_deal"],
    input_model=m.LookupIn,
    output_model=m.LookupOut,
    annotations=annotations(readOnlyHint=True),
    handler=read.lookup_deal,
    budget_ms=100,
)
