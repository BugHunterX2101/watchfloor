"""Deep-triage output contract (DD §10.7).

The dispatcher validates every agent result against ``TriageOutput`` before
anything is stored; a failure marks the job failed, never partially saved.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ActionType = Literal["create_task", "reassign_owner", "notify_channel", "draft_followup"]
Engine = Literal["strands", "offline"]
SpecialistStatus = Literal["ok", "issues", "failed", "timed_out"]
UNCHECKED_CONFIDENCE = 0.5  # the most a review may claim when its critic didn't approve it
_URL = re.compile(r"(https?://|www\.)", re.IGNORECASE)
_PLAY = re.compile(r"^P(0[1-9]|1[0-2])$")


def _no_urls(text: str) -> str:
    if _URL.search(text):
        raise ValueError("URLs are not allowed in triage output")
    return text


class Driver(BaseModel):
    model_config = ConfigDict(extra="forbid")

    factor: Literal["R", "I", "G", "S"]
    claim: Annotated[str, Field(min_length=3, max_length=200)]
    evidence_ids: Annotated[list[str], Field(min_length=1, max_length=5)]

    _claim = field_validator("claim")(_no_urls)


class RecommendedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: ActionType
    params: dict[str, Annotated[str, Field(max_length=300)]]
    rationale: Annotated[str, Field(min_length=3, max_length=240)]
    play_id: str
    confidence: Annotated[float, Field(ge=0, le=1)]

    _rationale = field_validator("rationale")(_no_urls)

    @field_validator("play_id")
    @classmethod
    def _known_play(cls, v: str) -> str:
        if not _PLAY.match(v):
            raise ValueError("play_id must be P01..P12")
        return v

    @field_validator("params")
    @classmethod
    def _param_values(cls, v: dict[str, str]) -> dict[str, str]:
        for value in v.values():
            _no_urls(value)
        return v


class TriageOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_summary: Annotated[str, Field(min_length=10, max_length=320)]
    risk_drivers: Annotated[list[Driver], Field(min_length=1, max_length=5)]
    recommended_actions: Annotated[list[RecommendedAction], Field(max_length=3)]
    confidence: Annotated[float, Field(ge=0, le=1)]
    # Set by the agents' runner, never by a model (the Lead fills LeadDraft). Results stored before
    # they were recorded have none of them.
    # Who answered: the multi-agent review ("strands"), or the rule-based one ("offline", ADR-023).
    engine: Engine | None = None
    # Whether the critic approved the final draft; None when no critic runs (the offline engine).
    checked: bool | None = None
    # How each specialist the Lead called last finished; "issues" is a critic that found problems.
    specialists: dict[Annotated[str, Field(max_length=40)], SpecialistStatus] = Field(
        default_factory=dict, max_length=8
    )

    @field_validator("voice_summary")
    @classmethod
    def _speakable(cls, v: str) -> str:
        if len(v.split()) > 45:
            raise ValueError("voice_summary must be 45 words or fewer")
        return _no_urls(v)

    def evidence_ids(self) -> set[str]:
        return {e for d in self.risk_drivers for e in d.evidence_ids}
