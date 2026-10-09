"""Domain value objects consumed and produced by the RIGS engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

Stage = Literal["discovery", "demo", "proposal", "negotiation", "closing", "won", "lost"]
Band = Literal["healthy", "watch", "at_risk"]
Factor = Literal["R", "I", "G", "S"]

OPEN_STAGES: tuple[str, ...] = ("discovery", "demo", "proposal", "negotiation", "closing")
CLOSED_STAGES: tuple[str, ...] = ("won", "lost")


def stage_rank(stage: str) -> int:
    """Position in the sales process; used to detect regressions."""
    return OPEN_STAGES.index(stage) if stage in OPEN_STAGES else len(OPEN_STAGES)


@dataclass(frozen=True)
class Engagement:
    """Buyer engagement over the trailing window, with the activity ids behind each count."""

    replies: int = 0  # inbound emails
    meetings: int = 0  # meetings held
    inbound_calls: int = 0
    views: int = 0  # document views
    activity_ids: tuple[str, ...] = ()
    view_ids: tuple[str, ...] = ()

    @property
    def two_way(self) -> int:
        return self.replies + self.meetings + self.inbound_calls


@dataclass(frozen=True)
class FieldChange:
    id: str
    field: Literal["close_date", "stage", "amount_cents"]
    old: Any
    new: Any
    changed_at: datetime


@dataclass(frozen=True)
class DealSnapshot:
    deal_id: str
    stage: str
    amount_cents: int
    economic_buyer_id: str | None
    next_step: str | None
    next_step_date: date | None
    decision_process: str | None
    last_two_way_at: datetime | None
    engagement_14d: Engagement
    history_90d: tuple[FieldChange, ...]
    now: datetime
    last_two_way_id: str | None = None
    today: date | None = None  # workspace-local date; defaults to now.date()

    @property
    def local_today(self) -> date:
        return self.today or self.now.date()


@dataclass(frozen=True)
class Reason:
    code: str
    factor: Factor
    contribution: float  # weighted contribution to the 0..1 total
    params: dict[str, Any] = field(default_factory=dict)
    text: str = ""
    evidence_ids: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "factor": self.factor,
            "contribution": round(self.contribution, 4),
            "text": self.text,
            "evidence_ids": list(self.evidence_ids),
            "params": self.params,
        }


@dataclass(frozen=True)
class RigsResult:
    score: int
    r: float
    i: float
    g: float
    s: float
    reasons: tuple[Reason, ...]
    model_version: str

    @property
    def components(self) -> dict[str, float]:
        return {"r": round(self.r, 2), "i": round(self.i, 2), "g": round(self.g, 2), "s": round(self.s, 2)}
