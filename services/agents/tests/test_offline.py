"""The offline engine: deterministic, evidence-backed, playbook-ordered."""

from __future__ import annotations

from agents.data_tools import JobData
from agents.gate import finalize
from agents.offline import offline_triage, triggers
from conftest import DEAL, FakeMCP


def test_triggers_include_derived_codes(job: JobData) -> None:
    fired = triggers(job)
    assert {"SILENT_DAYS", "VIEWS_WITHOUT_REPLY", "CLOSE_DATE_PUSHED", "NO_ECONOMIC_BUYER"} <= fired.keys()
    assert fired["AT_RISK_HIGH_VALUE"] == "At Risk with $84K open"
    assert fired["SECURITY_REVIEW"] == "Security review moved to next month"


def test_offline_triage_passes_the_gate(mcp: FakeMCP) -> None:
    job = JobData(deal_id=DEAL, call=mcp)
    job.read("explain_deal_risk")
    job.read("get_playbook")
    out = finalize(offline_triage(job), job)  # reads the timeline itself

    assert ("get_deal_timeline", {"deal_id": DEAL}) in mcp.calls
    assert [(d.factor, d.evidence_ids) for d in out.risk_drivers] == [
        ("R", ["act_3"]),
        ("I", ["act_4", "act_5"]),
        ("S", ["hist_1", "hist_2"]),
    ]
    assert [a.play_id for a in out.recommended_actions] == ["P01", "P02", "P03"]
    first = out.recommended_actions[0]
    assert first.action_type == "create_task"
    assert first.params == {
        "assignee_name": "Maya",
        "title": "Multi-thread to the economic buyer for Harbor & Pine",
    }
    assert out.voice_summary == (
        "Harbor & Pine is At Risk, health 34. No reply from the buyer in 9 days. "
        "Suggested next step: multi-thread to the economic buyer."
    )
    assert out.confidence == 0.6


def test_a_healthy_deal_with_only_gaps_cites_the_latest_buyer_conversation(job: JobData) -> None:
    assert job.explain is not None
    job.explain["risk"] = {"score": 19, "band": "healthy"}
    job.explain["reasons"] = [r for r in job.explain["reasons"] if not r["evidence_ids"]]  # gaps only
    out = finalize(offline_triage(job), job)
    assert [(d.factor, d.claim, d.evidence_ids) for d in out.risk_drivers] == [
        ("R", "Latest buyer conversation: an inbound email on 2026-10-01.", ["act_2"])
    ]
    assert out.voice_summary.startswith("Harbor & Pine is Healthy, health 81.")


def test_hostile_notes_change_nothing(job: JobData) -> None:
    out = finalize(offline_triage(job), job)
    assert all(a.action_type != "reassign_owner" for a in out.recommended_actions)
    assert "reassign" not in out.voice_summary.lower()
