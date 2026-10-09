"""explain_deal_risk: visible to users and to deep-triage agents (DD §6.1)."""

from __future__ import annotations

from watchfloor_core.models import tools as m
from watchfloor_core.principal import Scope
from watchfloor_core.services import agent_read, read
from watchfloor_core.services.context import ServiceContext

from ..resources import DEAL_DETAIL
from .base import ToolSpec, annotations
from .descriptions import DESCRIPTIONS


async def handle(ctx: ServiceContext, args: m.ExplainIn) -> m.ExplainOut:
    await agent_read.ensure_job_scope(ctx, deal_id=args.deal_id)  # no-op for user tokens
    return await read.explain_deal(ctx, args)


SPEC = ToolSpec(
    name="explain_deal_risk",
    scopes=frozenset({Scope.READ, Scope.AGENT_READ}),
    description=DESCRIPTIONS["explain_deal_risk"],
    input_model=m.ExplainIn,
    output_model=m.ExplainOut,
    annotations=annotations(readOnlyHint=True),
    handler=handle,
    ui_resource=DEAL_DETAIL,
    budget_ms=150,
)
