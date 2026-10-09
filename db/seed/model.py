"""In-memory seed records, plus the snapshot builder that mirrors the SQL in repos/deals.py.

Building the RIGS snapshot in Python lets the generator rejection-sample deals
with the real engine before anything is written; the seeder then asserts that the
database path produces the same scores.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ulid import ULID
from watchfloor_core.models.domain import DealSnapshot, Engagement, FieldChange


def det_id(prefix: str, rng: random.Random, at: datetime) -> str:
    ms = int(at.timestamp() * 1000)
    return f"{prefix}_{ULID.from_bytes(ms.to_bytes(6, 'big') + rng.randbytes(10))}"


@dataclass
class Activity:
    id: str
    type: str  # email | meeting | call | doc_view | note
    direction: str  # inbound | outbound | internal
    occurred_at: datetime
    summary: str
    body: str | None = None
    contact_id: str | None = None

    @property
    def two_way(self) -> bool:
        return (self.type in ("email", "call") and self.direction == "inbound") or self.type == "meeting"


@dataclass
class History:
    id: str
    field: str
    old_value: str
    new_value: str
    changed_at: datetime
    changed_by: str | None = None


@dataclass
class Contact:
    id: str
    name: str
    title: str
    is_economic_buyer: bool = False


@dataclass
class Account:
    id: str
    name: str
    segment: str
    owner_id: str
    contacts: list[Contact] = field(default_factory=list)


@dataclass
class Deal:
    id: str
    account: Account
    name: str
    amount_cents: int
    stage: str
    close_date: date
    owner_id: str
    next_step: str | None
    next_step_date: date | None
    economic_buyer_id: str | None
    decision_process: str | None
    created_at: datetime
    activities: list[Activity] = field(default_factory=list)
    history: list[History] = field(default_factory=list)


def snapshot(
    deal: Deal, now: datetime, today: date, window_days: int = 14, history_days: int = 90
) -> DealSnapshot:
    past = [a for a in deal.activities if a.occurred_at <= now]
    two_way = sorted((a for a in past if a.two_way), key=lambda a: a.occurred_at, reverse=True)
    window = [a for a in past if a.occurred_at > now - timedelta(days=window_days)]
    window.sort(key=lambda a: a.occurred_at, reverse=True)
    views = [a for a in window if a.type == "doc_view"]
    eng = Engagement(
        replies=sum(1 for a in window if a.type == "email" and a.direction == "inbound"),
        meetings=sum(1 for a in window if a.type == "meeting"),
        inbound_calls=sum(1 for a in window if a.type == "call" and a.direction == "inbound"),
        views=len(views),
        activity_ids=tuple(a.id for a in window if a.two_way),
        view_ids=tuple(a.id for a in views),
    )
    changes: list[FieldChange] = []
    for h in sorted(deal.history, key=lambda h: (h.changed_at, h.id)):
        if h.field not in ("close_date", "stage", "amount_cents"):
            continue
        if not (now - timedelta(days=history_days) < h.changed_at <= now):
            continue
        if h.field == "close_date":
            changes.append(
                FieldChange(
                    h.id,
                    "close_date",
                    date.fromisoformat(h.old_value),
                    date.fromisoformat(h.new_value),
                    h.changed_at,
                )
            )
        elif h.field == "amount_cents":
            changes.append(
                FieldChange(h.id, "amount_cents", int(h.old_value), int(h.new_value), h.changed_at)
            )
        else:
            changes.append(FieldChange(h.id, "stage", h.old_value, h.new_value, h.changed_at))
    return DealSnapshot(
        deal_id=deal.id,
        stage=deal.stage,
        amount_cents=deal.amount_cents,
        economic_buyer_id=deal.economic_buyer_id,
        next_step=deal.next_step,
        next_step_date=deal.next_step_date,
        decision_process=deal.decision_process,
        last_two_way_at=two_way[0].occurred_at if two_way else None,
        last_two_way_id=two_way[0].id if two_way else None,
        engagement_14d=eng,
        history_90d=tuple(changes),
        now=now,
        today=today,
    )
