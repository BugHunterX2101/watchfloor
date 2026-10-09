"""Snapshot builders shared by the RIGS unit tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from watchfloor_core.models.domain import DealSnapshot, Engagement, FieldChange

NOW = datetime(2026, 10, 8, 16, 0, tzinfo=UTC)
TODAY = NOW.date()


def snap(**overrides: object) -> DealSnapshot:
    base = DealSnapshot(
        deal_id="dl_test",
        stage="proposal",
        amount_cents=5_000_000,
        economic_buyer_id="ct_eb",
        next_step="Review proposal",
        next_step_date=TODAY + timedelta(days=3),
        decision_process="CFO signs after security review",
        last_two_way_at=NOW - timedelta(days=2),
        engagement_14d=Engagement(replies=3, meetings=1),
        history_90d=(),
        now=NOW,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def push(n: int, old: date, new: date, days_ago: int = 5) -> FieldChange:
    return FieldChange(f"hist_{n}", "close_date", old, new, NOW - timedelta(days=days_ago))


def kestrel() -> DealSnapshot:
    """DD §7.5 worked example."""
    d0 = TODAY + timedelta(days=9)
    d1 = d0 + timedelta(days=17)
    d2 = d1 + timedelta(days=17)
    return snap(
        stage="proposal",
        amount_cents=8_400_000,
        economic_buyer_id=None,
        decision_process=None,
        next_step="Send revised proposal",
        next_step_date=TODAY + timedelta(days=4),
        last_two_way_at=NOW - timedelta(days=16, hours=2),
        last_two_way_id="act_last_reply",
        engagement_14d=Engagement(views=6, view_ids=tuple(f"act_v{i}" for i in range(6))),
        history_90d=(push(441, d0, d1, 20), push(462, d1, d2, 8)),
    )


def harbor_and_pine() -> DealSnapshot:
    d0 = TODAY + timedelta(days=12)
    return snap(
        stage="negotiation",
        amount_cents=5_200_000,
        next_step=None,
        next_step_date=None,
        last_two_way_at=NOW - timedelta(days=11, hours=3),
        engagement_14d=Engagement(replies=1, activity_ids=("act_reply",)),
        history_90d=(push(501, d0, d0 + timedelta(days=10), 15),),
    )


def orbital_dental() -> DealSnapshot:
    return snap(
        stage="demo",
        amount_cents=3_100_000,
        next_step=None,
        next_step_date=None,
        last_two_way_at=NOW - timedelta(days=22, hours=1),
        engagement_14d=Engagement(views=4, view_ids=("a", "b", "c", "d")),
        history_90d=(),
    )


def northgate() -> DealSnapshot:
    d0 = TODAY + timedelta(days=10)
    return snap(
        stage="closing",
        amount_cents=12_000_000,
        last_two_way_at=NOW - timedelta(days=2),
        engagement_14d=Engagement(meetings=1, views=4),
        history_90d=(push(601, d0, d0 + timedelta(days=7), 40),),
    )
