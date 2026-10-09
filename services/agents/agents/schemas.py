"""Output schemas (DD §10.7).

``TriageOutput`` is the strict contract the dispatcher enforces. The Lead fills
the looser ``LeadDraft`` instead, so a slightly-off answer (a long claim, an
unknown evidence id) reaches our gate and is repaired or dropped item by item,
rather than failing the whole structured-output call.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from watchfloor_core.models.triage import TriageOutput

__all__ = ["DraftAction", "DraftDriver", "LeadDraft", "TriageOutput"]


class DraftDriver(BaseModel):
    factor: Literal["R", "I", "G", "S"] = Field(
        description="R recency, I interaction, G gaps in qualification, S slippage."
    )
    claim: str = Field(description="One short factual sentence, at most 200 characters.")
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="1 to 5 activity_id or history_id values that support the claim, copied exactly.",
    )


class DraftAction(BaseModel):
    action_type: Literal["create_task", "reassign_owner", "notify_channel", "draft_followup"]
    params: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "create_task: assignee_name, title (and optional due_date YYYY-MM-DD). "
            "reassign_owner: new_owner_name. notify_channel: message. draft_followup: intent."
        ),
    )
    rationale: str = Field(description="Why this play fits, at most 240 characters.")
    play_id: str = Field(description="A playbook id, P01 to P12.")
    confidence: float = Field(default=0.5, description="0 to 1.")


class LeadDraft(BaseModel):
    voice_summary: str = Field(
        description="What Alexa says: plain speech, at most 45 words, no ids, no links."
    )
    risk_drivers: list[DraftDriver] = Field(description="1 to 5 drivers, most important first.")
    recommended_actions: list[DraftAction] = Field(
        default_factory=list, description="At most 3 actions, best first."
    )
    confidence: float = Field(description="Overall confidence, 0 to 1.")
