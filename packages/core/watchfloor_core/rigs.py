"""RIGS: an explainable, deterministic deal-risk score (DD §7).

Pure functions only: no I/O, no clock, no randomness. The same snapshot always
yields the same score and the same reason order, so the hot path, the watcher,
the seeder and the agents all agree.

    R  Recency    clamp((d - n) / 2n, 0, 1)        d = days since two-way touch
    I  Intent     clamp(1 - points_14d / b, 0, 1)
    G  Gaps       0.25 per missing qualification item
    S  Slippage   pushes, stage regression, amount cut, magnitude kicker; cap 1

    score = round(100 * (0.30 R + 0.25 I + 0.25 G + 0.20 S))
"""

from __future__ import annotations

from .models.domain import (
    Band,
    DealSnapshot,
    FieldChange,
    Reason,
    RigsResult,
    stage_rank,
)
from .rigs_params import BandThresholds, RigsParams, StageParams, Weights
from .speech import plural, stage_label, times_word

NEVER_DAYS = 10_000


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def score(snapshot: DealSnapshot, params: RigsParams) -> RigsResult:
    stage = params.stage(snapshot.stage)
    return score_with(snapshot, stage, params.weights, params)


def score_with(s: DealSnapshot, p: StageParams, w: Weights, params: RigsParams) -> RigsResult:
    reasons: list[Reason] = []

    # R: recency of two-way contact.
    d = max(0, (s.now - s.last_two_way_at).days) if s.last_two_way_at is not None else NEVER_DAYS
    r = clamp((d - p.norm_days) / (2 * p.norm_days))
    if r > 0:
        if d >= NEVER_DAYS:
            text = "No two-way contact on record."
        else:
            text = f"No two-way contact for {d} days; typical for {stage_label(p.name)} is {p.norm_days}."
        reasons.append(
            Reason(
                "SILENT_DAYS",
                "R",
                w.r * r,
                {"days": d if d < NEVER_DAYS else None, "norm": p.norm_days},
                text,
                (s.last_two_way_id,) if s.last_two_way_id else (),
            )
        )

    # I: buyer intent over the trailing window.
    e = s.engagement_14d
    pts = engagement_points(s, params)
    i = clamp(1 - pts / p.intent_baseline)
    if i > 0:
        if e.views and not e.two_way:
            doc = "Proposal" if s.stage in ("proposal", "negotiation", "closing") else "Shared material"
            code = "VIEWS_WITHOUT_REPLY"
            text = f"{doc} viewed {plural(e.views, 'time')} in {params.engagement_window_days} days with no reply."
            evidence = e.view_ids[:5]
        else:
            code = "LOW_ENGAGEMENT"
            text = (
                f"Buyer engagement is low: {pts:.1f} engagement points in "
                f"{params.engagement_window_days} days against a typical {p.intent_baseline:.1f}."
            )
            evidence = e.activity_ids[:5]
        reasons.append(
            Reason(
                code,
                "I",
                w.i * i,
                {
                    "points": round(pts, 2),
                    "baseline": p.intent_baseline,
                    "views": e.views,
                    "two_way": e.two_way,
                },
                text,
                tuple(evidence),
            )
        )

    # G: missing qualification.
    gap_items: list[tuple[str, str, dict[str, object]]] = []
    if s.economic_buyer_id is None:
        gap_items.append(("NO_ECONOMIC_BUYER", "No economic buyer identified.", {}))
    if not s.next_step:
        gap_items.append(("NO_NEXT_STEP", "No next step recorded.", {}))
    if s.next_step_date is None:
        gap_items.append(
            ("NEXT_STEP_DATE_MISSING_OR_OVERDUE", "Next step has no date.", {"overdue_days": None})
        )
    else:
        overdue = (s.local_today - s.next_step_date).days
        if overdue > params.gaps.overdue_grace_days:
            gap_items.append(
                (
                    "NEXT_STEP_DATE_MISSING_OR_OVERDUE",
                    f"Next step is {plural(overdue, 'day')} overdue.",
                    {"overdue_days": overdue},
                )
            )
    if not s.decision_process:
        gap_items.append(("NO_DECISION_PROCESS", "Decision process not captured.", {}))
    g = clamp(params.gaps.per_gap * len(gap_items))
    for code, text, extra in gap_items:
        reasons.append(Reason(code, "G", w.g * params.gaps.per_gap, extra, text))

    # S: slippage, with each component explained separately.
    sv, slip_reasons = _slippage(s.history_90d, params)
    reasons.extend(Reason(c, "S", w.s * part, extra, text, ev) for c, part, extra, text, ev in slip_reasons)

    total = w.r * r + w.i * i + w.g * g + w.s * sv
    result_score = int(round(100 * total))
    reasons.sort(key=lambda x: (-round(x.contribution, 9), _FACTOR_ORDER[x.factor], x.code))
    return RigsResult(
        score=max(0, min(100, result_score)),
        r=r,
        i=i,
        g=g,
        s=sv,
        reasons=tuple(reasons),
        model_version=params.model_version,
    )


_FACTOR_ORDER = {"R": 0, "I": 1, "G": 2, "S": 3}


def engagement_points(s: DealSnapshot, params: RigsParams) -> float:
    e = s.engagement_14d
    pp = params.points
    return (
        pp.email_inbound * e.replies
        + pp.meeting * e.meetings
        + pp.call_inbound * e.inbound_calls
        + pp.doc_view * min(e.views, pp.doc_view_cap)
    )


def _slippage(
    history: tuple[FieldChange, ...], params: RigsParams
) -> tuple[float, list[tuple[str, float, dict[str, object], str, tuple[str, ...]]]]:
    sp = params.slippage
    pushes = [c for c in history if c.field == "close_date" and c.new > c.old]
    total_push = sum((c.new - c.old).days for c in pushes)
    regressions = [c for c in history if c.field == "stage" and stage_rank(c.new) < stage_rank(c.old)]
    cuts = [
        c
        for c in history
        if c.field == "amount_cents" and c.old and c.new < (1 - sp.amount_cut_threshold) * c.old
    ]

    parts: list[tuple[str, float, dict[str, object], str, tuple[str, ...]]] = []
    if pushes:
        raw = sp.push * len(pushes) + (sp.magnitude_kicker if total_push > sp.magnitude_days else 0.0)
        parts.append(
            (
                "CLOSE_DATE_PUSHED",
                raw,
                {"count": len(pushes), "days": total_push},
                f"Close date pushed {times_word(len(pushes))}, {plural(total_push, 'day')} in total.",
                tuple(c.id for c in pushes),
            )
        )
    if regressions:
        last = regressions[-1]
        parts.append(
            (
                "STAGE_REGRESSED",
                sp.stage_regression,
                {"from": last.old, "to": last.new},
                f"Deal moved back from {stage_label(last.old)} to {stage_label(last.new)}.",
                tuple(c.id for c in regressions),
            )
        )
    if cuts:
        worst = min(cuts, key=lambda c: c.new / c.old)
        pct = round(100 * (1 - worst.new / worst.old))
        parts.append(
            (
                "AMOUNT_CUT",
                sp.amount_cut,
                {"percent": pct},
                f"Deal amount cut by {pct}%.",
                tuple(c.id for c in cuts),
            )
        )

    raw_total = sum(p[1] for p in parts)
    if raw_total <= 0:
        return 0.0, []
    sv = min(raw_total, 1.0)
    scale = sv / raw_total
    return sv, [(c, part * scale, extra, text, ev) for c, part, extra, text, ev in parts]


def band_for(score_value: int, previous: Band | None, t: BandThresholds) -> Band:
    """Band with hysteresis (DD delta D5): enter At Risk at >= 65, leave below 60;
    enter Watch at >= 40, leave below 36."""
    if previous == "at_risk" and score_value >= t.at_risk_exit_below:
        return "at_risk"
    if score_value >= t.at_risk_enter:
        return "at_risk"
    if previous in ("at_risk", "watch") and score_value >= t.watch_exit_below:
        return "watch"
    if score_value >= t.watch_enter:
        return "watch"
    return "healthy"


def factor_sums(result: RigsResult) -> dict[str, float]:
    out = {"R": 0.0, "I": 0.0, "G": 0.0, "S": 0.0}
    for reason in result.reasons:
        out[reason.factor] += reason.contribution
    return out
