"""The five agent-only read tools (DD §6.5). Job-scoped tokens only; never visible to Alexa+."""

from __future__ import annotations

from pydantic import BaseModel
from watchfloor_core.models import tools as m
from watchfloor_core.principal import Scope
from watchfloor_core.services import agent_read

from .base import Handler, ToolSpec, annotations
from .descriptions import DESCRIPTIONS

_AGENT = frozenset({Scope.AGENT_READ})


def _spec(
    name: str,
    input_model: type[BaseModel],
    output_model: type[BaseModel],
    handler: Handler,
    budget_ms: int = 150,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        scopes=_AGENT,
        description=DESCRIPTIONS[name],
        input_model=input_model,
        output_model=output_model,
        annotations=annotations(readOnlyHint=True, openWorldHint=False),
        handler=handler,
        budget_ms=budget_ms,
    )


SPECS: tuple[ToolSpec, ...] = (
    _spec("get_deal_timeline", m.TimelineIn, m.TimelineOut, agent_read.timeline),
    _spec("get_deal_history", m.HistoryIn, m.HistoryOut, agent_read.history),
    _spec("get_account_context", m.AccountContextIn, m.AccountContextOut, agent_read.account_context),
    _spec("get_prior_triage", m.PriorTriageIn, m.PriorTriageOut, agent_read.prior_triage),
    _spec("get_playbook", m.PlaybookIn, m.PlaybookOut, agent_read.playbook, budget_ms=50),
)
