"""Properties RIGS guarantees for every input (DD §7.7)."""

from __future__ import annotations

from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st
from watchfloor_core import rigs
from watchfloor_core.models.domain import OPEN_STAGES, Engagement
from watchfloor_core.rigs_params import load_params

from tests.unit.rigs_helpers import NOW, TODAY, push, snap

P = load_params()

engagements = st.builds(
    Engagement,
    replies=st.integers(0, 8),
    meetings=st.integers(0, 4),
    inbound_calls=st.integers(0, 4),
    views=st.integers(0, 30),
)
stages = st.sampled_from(OPEN_STAGES)


@st.composite
def snapshots(draw: st.DrawFn):
    silent = draw(st.one_of(st.none(), st.integers(0, 400)))
    pushes = draw(st.integers(0, 4))
    d0 = TODAY + timedelta(days=5)
    history = tuple(push(k, d0 + timedelta(10 * k), d0 + timedelta(10 * (k + 1))) for k in range(pushes))
    return snap(
        stage=draw(stages),
        economic_buyer_id=draw(st.sampled_from([None, "ct_eb"])),
        next_step=draw(st.sampled_from([None, "", "Call"])),
        next_step_date=draw(
            st.one_of(st.none(), st.integers(-20, 20).map(lambda k: TODAY + timedelta(days=k)))
        ),
        decision_process=draw(st.sampled_from([None, "Board"])),
        last_two_way_at=None if silent is None else NOW - timedelta(days=silent),
        engagement_14d=draw(engagements),
        history_90d=history,
    )


@settings(max_examples=300, deadline=None)
@given(snapshots())
def test_bounds_and_explainability(s) -> None:
    res = rigs.score(s, P)
    assert 0 <= res.score <= 100
    for v in (res.r, res.i, res.g, res.s):
        assert 0.0 <= v <= 1.0
    sums = rigs.factor_sums(res)
    w = P.weights
    for factor, value, weight in (("R", res.r, w.r), ("I", res.i, w.i), ("G", res.g, w.g), ("S", res.s, w.s)):
        assert abs(sums[factor] - weight * value) <= 0.001
        if value > 0:
            assert any(r.factor == factor for r in res.reasons)


@settings(max_examples=200, deadline=None)
@given(snapshots())
def test_deterministic(s) -> None:
    assert rigs.score(s, P) == rigs.score(s, P)


@settings(max_examples=200, deadline=None)
@given(days=st.integers(0, 365), extra=st.integers(1, 60), stage=stages)
def test_more_silence_never_lowers_recency(days: int, extra: int, stage: str) -> None:
    a = rigs.score(snap(stage=stage, last_two_way_at=NOW - timedelta(days=days)), P)
    b = rigs.score(snap(stage=stage, last_two_way_at=NOW - timedelta(days=days + extra)), P)
    assert b.r >= a.r
    assert b.score >= a.score


@settings(max_examples=200, deadline=None)
@given(e=engagements, more=st.integers(1, 5), stage=stages)
def test_more_engagement_never_raises_intent_risk(e: Engagement, more: int, stage: str) -> None:
    a = rigs.score(snap(stage=stage, engagement_14d=e), P)
    richer = Engagement(e.replies + more, e.meetings, e.inbound_calls, e.views)
    b = rigs.score(snap(stage=stage, engagement_14d=richer), P)
    assert b.i <= a.i


@settings(max_examples=100, deadline=None)
@given(n=st.integers(0, 4))
def test_extra_push_never_lowers_slippage(n: int) -> None:
    d0 = TODAY + timedelta(days=5)
    hist = [push(k, d0 + timedelta(5 * k), d0 + timedelta(5 * (k + 1))) for k in range(n)]
    a = rigs.score(snap(history_90d=tuple(hist)), P)
    hist.append(push(n, d0 + timedelta(5 * n), d0 + timedelta(5 * (n + 1))))
    b = rigs.score(snap(history_90d=tuple(hist)), P)
    assert b.s >= a.s


def test_engine_module_has_no_io_imports() -> None:
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(rigs))
    names = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")])
    }
    assert names.isdisjoint({"asyncpg", "boto3", "httpx", "requests", "socket", "os", "random", "time"})
