"""Triage Lead: Sonnet, temp 0, four specialist tools, 8 steps (DD §10.3–10.5).

The Lead's final turn is a structured-output call into ``LeadDraft``; the gate
turns that into a ``TriageOutput``.
"""

from __future__ import annotations

from typing import Any

from strands import Agent
from watchfloor_core.settings import Settings

from .data_tools import JobData, make_data_tools
from .envelope import wrap_untrusted
from .gate import Rejected
from .models import sonnet
from .prompts import load_prompt
from .schemas import LeadDraft
from .specialists import make_specialist_tools

LEAD_TURNS = 8


def build_lead(job: JobData, settings: Settings, recall: Any) -> Agent:
    return Agent(
        model=sonnet(settings=settings),
        system_prompt=load_prompt("lead"),
        tools=make_specialist_tools(make_data_tools(job), recall=recall, settings=settings),
        callback_handler=None,
        name="triage_lead",
        retry_strategy=None,  # fail at once on a throttle (ADR-023)
    )


def lead_prompt(job: JobData) -> str:
    deal = (job.explain or {}).get("deal") or {}
    label = f"{deal.get('account', '')} · {deal.get('name', '')}".strip(" ·") or job.deal_id
    return (
        f"Triage the deal under review ({job.deal_id}):\n"
        f"{wrap_untrusted(label, source='deal', ref_id=job.deal_id)}\n"
        "Follow the procedure, then return the final triage."
    )


async def run_lead(job: JobData, settings: Settings, recall: Any) -> LeadDraft:
    lead = build_lead(job, settings, recall)
    result = await lead.invoke_async(
        lead_prompt(job), structured_output_model=LeadDraft, limits={"turns": LEAD_TURNS}
    )
    draft = result.structured_output
    if not isinstance(draft, LeadDraft):
        raise Rejected("the lead returned no structured triage")
    return draft
