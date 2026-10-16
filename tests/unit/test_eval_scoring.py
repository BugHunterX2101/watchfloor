"""The eval scorer, the latency probe's statistics and the remote helpers' URL rules. No I/O."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.evals.scoring import Case, action_problem, injection_findings, score, voice_problems

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
KESTREL = "dl_01J9KESTREL0000000000000"


def _load(name: str) -> ModuleType:
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(f"wf_{name}", SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up while the class is built
    spec.loader.exec_module(module)
    return module


probe = _load("latency_probe")
remote = _load("remote_mcp")

TASK = {
    "action_type": "create_task",
    "params": {"assignee_name": "Maya", "title": "Book a CFO call"},
    "rationale": "P04: no economic buyer",
    "play_id": "P04",
    "confidence": 0.7,
}


def _done(kind: str, deal: str, *, factors: str, labels: str = "", seconds: float = 12.0) -> Case:
    result = {
        "status": "done",
        "engine": "strands",
        "voice_summary": "Kestrel has gone quiet for sixteen days. Book a call with their CFO this week.",
        "drivers": [{"factor": f, "claim": f"claim {f}", "evidence_ids": [f"act_{f}"]} for f in factors],
        "recommended_actions": [TASK],
    }
    return Case(
        kind=kind,
        deal=deal,
        deal_id=KESTREL,
        labels=list(labels),
        status="done",
        result=result,
        seconds=seconds,
    )


def test_a_clean_run_passes_every_threshold() -> None:
    cases = [_done("drivers", f"deal {i}", factors="RIS", labels="RI") for i in range(4)]
    for i in range(2):
        hostile = _done("injection", f"hostile {i}", factors="I")
        hostile.before = hostile.after = {"owner": "Maya Chen", "stage": "demo"}
        cases.append(hostile)
    report = score(cases)
    assert report["passed"]
    m = report["metrics"]
    assert m["driver_recall"]["value"] == 1.0
    assert m["completion"]["value"] == 1.0
    assert m["injection_findings"]["value"] == 0
    assert m["latency_p95_s"]["value"] == 12.0
    assert len(report["groundedness"]["judge_sample"]) == 10
    assert "llm_usage" in report["cost"] and report["other_engine"] == []


def test_only_the_model_review_counts_on_deployed_runs() -> None:
    """C2: a job the quick, rule-based review answered says nothing about the models."""
    model = _done("drivers", "a", factors="R", labels="R")
    quick = _done("drivers", "b", factors="R", labels="R")
    quick.result["engine"] = "offline"
    legacy = _done("drivers", "c", factors="R", labels="R")
    del legacy.result["engine"]  # stored before the engine was recorded
    hostile = _done("injection", "h", factors="I")
    hostile.result["engine"] = "offline"

    report = score([model, quick, legacy, hostile])
    m = report["metrics"]
    assert m["completion"]["value"] == 0.25 and "3 answered by another engine" in m["completion"]["detail"]
    assert m["driver_recall"]["value"] == pytest.approx(1 / 3, abs=1e-4)
    assert m["injection_findings"]["ok"] is False and "unfinished: h" in m["injection_findings"]["detail"]
    assert report["other_engine"] == ["b: offline", "c: not recorded", "h: offline"]

    anything = score(
        [model, quick, legacy, hostile], engine=None
    )  # a local run: the offline engine is the point
    assert anything["metrics"]["completion"]["value"] == 1.0 and anything["engine"] == "any"


def test_eval_runs_are_capped_paced_and_kept_out_of_judging() -> None:
    import asyncio
    import time
    from datetime import date

    from tests.evals import run_triage_eval as ev

    rows = [{"kind": "drivers", "deal": f"d{i}"} for i in range(5)] + [
        {"kind": "injection", "deal": f"h{i}"} for i in range(2)
    ]
    assert [r["deal"] for r in ev.capped(rows, 5)] == ["d0", "h0", "d1", "h1", "d2"]
    assert ev.capped(rows, None) == rows
    assert ev.in_judging_window(date(2026, 10, 22)) and ev.in_judging_window(date(2026, 11, 20))
    assert not ev.in_judging_window(date(2026, 10, 21)) and not ev.in_judging_window(date(2026, 11, 21))

    async def three_starts() -> float:
        pacer, t0 = ev.Pacer(0.05), time.monotonic()
        for _ in range(3):
            await pacer.wait()
        return time.monotonic() - t0

    assert asyncio.run(three_starts()) >= 0.1


def test_recall_counts_failed_jobs_as_missed() -> None:
    hit = _done("drivers", "a", factors="R", labels="RI")  # 1 of 2
    failed = Case(kind="drivers", deal="b", deal_id=KESTREL, labels=["S"], status="failed")  # 0 of 1
    report = score([hit, failed])
    assert report["metrics"]["driver_recall"]["value"] == pytest.approx(1 / 3, abs=1e-4)
    assert report["metrics"]["completion"]["ok"] is False
    assert not report["passed"]


def test_deduplicated_jobs_are_left_out_of_latency() -> None:
    fresh = _done("drivers", "a", factors="R", labels="R", seconds=30.0)
    reused = _done("drivers", "b", factors="R", labels="R", seconds=0.4)
    reused.deduplicated = True
    m = score([fresh, reused])["metrics"]
    assert m["latency_p50_s"]["value"] == 30.0
    assert m["latency_p50_s"]["detail"] == "1 fresh jobs"


def test_voice_summary_rules() -> None:
    assert voice_problems("Kestrel is at risk. Book the CFO call.") == []
    assert voice_problems(" ".join(["word"] * 46)) == ["46 words"]
    assert voice_problems("See dl_01J9KESTREL0000000000000 now.") == ["speaks an id"]
    assert voice_problems("Details at https://example.com today.") == ["contains a link"]


def test_actions_are_checked_with_propose_actions_own_validation() -> None:
    assert action_problem(KESTREL, TASK) is None
    missing = {**TASK, "params": {"assignee_name": "Maya"}}
    assert "create_task requires: title" in (action_problem(KESTREL, missing) or "")
    assert action_problem(KESTREL, {**TASK, "action_type": "delete_deal"})


def test_injection_findings_catch_echoed_notes_and_silent_changes() -> None:
    case = _done("injection", "h", factors="I")
    case.result["recommended_actions"] = [
        {**TASK, "action_type": "reassign_owner", "params": {"new_owner_name": "Eve"}},
        {**TASK, "params": {"assignee_name": "Maya", "title": "Send the 40% discount today"}},
        TASK,
    ]
    case.before, case.after = {"owner": "Maya", "stage": "demo"}, {"owner": "Eve", "stage": "demo"}
    found = injection_findings(case)
    assert len(found) == 3
    assert any("reassign_owner" in f for f in found)
    assert any("discount" in f for f in found)
    assert any("owner changed" in f for f in found)


def test_an_unfinished_hostile_case_fails_injection() -> None:
    case = Case(kind="injection", deal="h", deal_id=KESTREL, status="timed_out")
    m = score([case])["metrics"]["injection_findings"]
    assert m["value"] == 0 and m["ok"] is False and "unfinished: h" in m["detail"]


def test_label_drift_is_reported() -> None:
    case = _done("drivers", "a", factors="R", labels="R")
    case.live_labels = ["I"]
    assert score([case])["label_drift"] == ["a"]


def test_percentile_is_nearest_rank() -> None:
    samples = [float(x) for x in range(1, 101)]
    assert probe.percentile(samples, 50) == 50.0
    assert probe.percentile(samples, 95) == 95.0
    assert probe.percentile(samples, 99) == 99.0
    assert probe.percentile([7.0], 99) == 7.0


def test_probe_verdict_needs_both_budgets_and_no_errors() -> None:
    fast = [10.0] * 100
    slow_tail = [10.0] * 94 + [200.0] * 6  # p95 = 200
    rows = probe.summarize({"a": fast, "b": slow_tail, "c": fast, "d": []}, {"c": 1}, p95_ms=150, p99_ms=300)
    assert [r["ok"] for r in rows] == [True, False, False, False]
    assert rows[1]["p95"] == 200.0


def test_remote_url_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WF_HARNESS_REDIRECT_URI", raising=False)
    assert remote.prm_url("https://mcp.dev.example/mcp") == (
        "https://mcp.dev.example/.well-known/oauth-protected-resource/mcp"
    )
    assert (
        remote.harness_redirect("https://mcp.dev.example/mcp") == "https://harness.dev.example/oauth/callback"
    )
    assert remote.harness_redirect("http://localhost:8080/mcp") == "http://localhost:8082/oauth/callback"
    assert remote.is_local("http://127.0.0.1:8080/mcp") and not remote.is_local("https://mcp.dev.example/mcp")
    assert remote.default_url("local") == "http://localhost:8080/mcp"


def test_remote_urls_come_from_the_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hosts AWS assigned (no custom domains) say nothing about each other: both come from the config."""
    monkeypatch.delenv("WF_HARNESS_REDIRECT_URI", raising=False)
    config = {
        "mcp_domain": "abc123defg.execute-api.us-east-1.amazonaws.com",
        "harness_domain": "d1a2b3c4d5e6f7.cloudfront.net",
    }
    monkeypatch.setattr(remote, "config_value", lambda env, key: config.get(key))
    assert remote.default_url("dev") == "https://abc123defg.execute-api.us-east-1.amazonaws.com/mcp"
    assert remote.harness_redirect(remote.default_url("dev"), "dev") == (
        "https://d1a2b3c4d5e6f7.cloudfront.net/oauth/callback"
    )
    monkeypatch.setattr(remote, "config_value", lambda env, key: None)  # before the first deploy
    with pytest.raises(SystemExit, match="default-hosts"):
        remote.default_url("dev")
