"""Hand-computed RIGS cases (DD §7.5, §13.5, §22.4)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from watchfloor_core import rigs
from watchfloor_core.models.domain import Engagement, FieldChange
from watchfloor_core.rigs_params import load_params

from tests.unit.rigs_helpers import TODAY, harbor_and_pine, kestrel, northgate, orbital_dental, push, snap

P = load_params()


def test_kestrel_scores_71_with_documented_components() -> None:
    res = rigs.score(kestrel(), P)
    assert res.score == 71
    assert round(res.r, 2) == 0.83
    assert (round(res.i, 2), res.g, round(res.s, 2)) == (0.70, 0.50, 0.80)
    assert [r.code for r in res.reasons[:3]] == ["SILENT_DAYS", "VIEWS_WITHOUT_REPLY", "CLOSE_DATE_PUSHED"]
    assert [round(r.contribution, 3) for r in res.reasons[:3]] == [0.250, 0.175, 0.160]
    assert res.reasons[0].text == "No two-way contact for 16 days; typical for Proposal is 6."
    assert res.reasons[1].text == "Proposal viewed 6 times in 14 days with no reply."
    assert res.reasons[2].text == "Close date pushed twice, 34 days in total."
    assert res.reasons[2].evidence_ids == ("hist_441", "hist_462")
    assert rigs.band_for(res.score, None, P.bands) == "at_risk"


def test_harbor_and_pine_scores_66_at_risk() -> None:
    res = rigs.score(harbor_and_pine(), P)
    assert res.score == 66
    assert rigs.band_for(res.score, None, P.bands) == "at_risk"
    assert {r.code for r in res.reasons} >= {
        "NO_NEXT_STEP",
        "NEXT_STEP_DATE_MISSING_OR_OVERDUE",
        "CLOSE_DATE_PUSHED",
    }


def test_harbor_and_pine_after_voice_update_drops_to_watch() -> None:
    """J5: next step and date logged -> 53; hysteresis moves At Risk to Watch."""
    updated = harbor_and_pine()
    updated = type(updated)(**{**updated.__dict__, "next_step": "Security review", "next_step_date": TODAY})
    res = rigs.score(updated, P)
    assert res.score == 53
    assert rigs.band_for(res.score, "at_risk", P.bands) == "watch"


def test_orbital_dental_scores_58_watch() -> None:
    res = rigs.score(orbital_dental(), P)
    assert res.score == 58
    assert rigs.band_for(res.score, None, P.bands) == "watch"


def test_northgate_scores_18_healthy() -> None:
    res = rigs.score(northgate(), P)
    assert res.score == 18
    assert rigs.band_for(res.score, None, P.bands) == "healthy"
    assert all(r.code != "VIEWS_WITHOUT_REPLY" for r in res.reasons)


def test_healthy_control_scores_zero() -> None:
    res = rigs.score(snap(), P)
    assert res.score == 0
    assert res.reasons == ()


def test_recency_is_zero_at_norm_and_saturates_at_three_times_norm() -> None:
    now = snap().now
    at_norm = rigs.score(snap(last_two_way_at=now - timedelta(days=6)), P)
    at_3n = rigs.score(snap(last_two_way_at=now - timedelta(days=18)), P)
    assert at_norm.r == 0.0
    assert at_3n.r == 1.0


def test_never_contacted_is_full_recency_risk() -> None:
    res = rigs.score(snap(last_two_way_at=None), P)
    assert res.r == 1.0
    assert res.reasons[0].text == "No two-way contact on record."


def test_slippage_kicker_only_when_total_push_exceeds_30_days() -> None:
    d0 = TODAY + timedelta(days=5)
    small = rigs.score(
        snap(history_90d=(push(1, d0, d0 + timedelta(3)), push(2, d0 + timedelta(3), d0 + timedelta(6)))), P
    )
    large = rigs.score(
        snap(history_90d=(push(1, d0, d0 + timedelta(17)), push(2, d0 + timedelta(17), d0 + timedelta(34)))),
        P,
    )
    assert round(small.s, 2) == 0.70
    assert round(large.s, 2) == 0.80


def test_stage_regression_alone_emits_a_reason() -> None:
    now = snap().now
    regress = FieldChange("hist_9", "stage", "negotiation", "proposal", now - timedelta(days=3))
    res = rigs.score(snap(history_90d=(regress,)), P)
    assert round(res.s, 2) == 0.40
    assert [r.code for r in res.reasons if r.factor == "S"] == ["STAGE_REGRESSED"]


def test_slippage_cap_scales_component_reasons_to_sum_to_factor() -> None:
    now = snap().now
    d0 = TODAY + timedelta(days=5)
    history = (
        push(1, d0, d0 + timedelta(20)),
        push(2, d0 + timedelta(20), d0 + timedelta(40)),
        FieldChange("hist_s", "stage", "closing", "proposal", now - timedelta(days=2)),
        FieldChange("hist_a", "amount_cents", 10_000_000, 5_000_000, now - timedelta(days=2)),
    )
    res = rigs.score(snap(history_90d=history), P)
    assert res.s == 1.0
    assert rigs.factor_sums(res)["S"] == pytest.approx(P.weights.s * 1.0, abs=1e-9)


def test_overdue_next_step_beyond_grace_is_a_gap() -> None:
    within = rigs.score(snap(next_step_date=TODAY - timedelta(days=2)), P)
    beyond = rigs.score(snap(next_step_date=TODAY - timedelta(days=3)), P)
    assert within.g == 0.0
    assert beyond.g == 0.25
    assert any(r.text == "Next step is 3 days overdue." for r in beyond.reasons)


def test_doc_views_are_capped_at_ten() -> None:
    ten = rigs.score(snap(engagement_14d=Engagement(views=10)), P)
    forty = rigs.score(snap(engagement_14d=Engagement(views=40)), P)
    assert ten.i == forty.i


@pytest.mark.parametrize(
    ("score", "previous", "expected"),
    [
        (66, None, "at_risk"),
        (62, "at_risk", "at_risk"),  # stays: above exit threshold
        (59, "at_risk", "watch"),  # DD §22.4
        (64, "watch", "watch"),
        (65, "watch", "at_risk"),
        (38, "watch", "watch"),
        (38, "healthy", "healthy"),
        (35, "watch", "healthy"),
        (40, None, "watch"),
        (39, None, "healthy"),
    ],
)
def test_band_hysteresis(score: int, previous: str | None, expected: str) -> None:
    assert rigs.band_for(score, previous, P.bands) == expected  # type: ignore[arg-type]
