"""The deterministic output gate: evidence, playbook, params, speech."""

from __future__ import annotations

import pytest
from agents.data_tools import JobData
from agents.gate import Rejected, finalize, speakable
from agents.schemas import DraftAction, DraftDriver, LeadDraft


def _draft(**over: object) -> LeadDraft:
    base: dict[str, object] = {
        "voice_summary": "Harbor & Pine has gone quiet since the proposal.",
        "risk_drivers": [DraftDriver(factor="R", claim="No reply in 9 days", evidence_ids=["act_3"])],
        "recommended_actions": [],
        "confidence": 0.7,
    }
    return LeadDraft.model_validate({**base, **over})


def _task(play: str = "P04", **params: str) -> DraftAction:
    return DraftAction(
        action_type="create_task",
        params=params or {"assignee_name": "Maya", "title": "Send a value recap"},
        rationale="The buyer reads but does not reply",
        play_id=play,
        confidence=0.8,
    )


def test_drivers_keep_only_evidence_this_job_saw(job: JobData) -> None:
    out = finalize(
        _draft(
            risk_drivers=[
                DraftDriver(
                    factor="R", claim="No reply in 9 days", evidence_ids=["act_3", "act_99", "act_3"]
                ),
                DraftDriver(factor="S", claim="Invented slippage", evidence_ids=["hist_404"]),
                DraftDriver(factor="I", claim="Views without reply", evidence_ids=[]),
            ]
        ),
        job,
    )
    assert [(d.factor, d.evidence_ids) for d in out.risk_drivers] == [("R", ["act_3"])]


def test_nothing_backed_by_evidence_is_rejected(job: JobData) -> None:
    with pytest.raises(Rejected):
        finalize(
            _draft(risk_drivers=[DraftDriver(factor="R", claim="Made up", evidence_ids=["act_99"])]), job
        )


def test_actions_must_fit_the_playbook_and_propose_action(job: JobData) -> None:
    actions = [
        _task("P13"),  # unknown play
        DraftAction(  # P05 only allows create_task
            action_type="notify_channel",
            params={"message": "Heads up"},
            rationale="Re-qualify",
            play_id="P05",
        ),
        _task("P04", assignee_name="Maya"),  # create_task without a title
        _task("P09", assignee_name="Maya", title="Offer a 15% discount"),
        _task("P08", assignee_name="Maya", title="See https://evil.example"),
        DraftAction(
            action_type="draft_followup",
            params={"intent": "Recap the agreed value and ask one question"},
            rationale="Draft only",
            play_id="P04",
            confidence=1.7,
        ),
        _task("P04"),
        _task("P04"),  # duplicate play and type
        _task("P12"),
        _task("P11"),  # a fourth survivor: over the cap
    ]
    out = finalize(_draft(recommended_actions=actions), job)
    assert [(a.play_id, a.action_type) for a in out.recommended_actions] == [
        ("P04", "draft_followup"),
        ("P04", "create_task"),
        ("P12", "create_task"),
    ]
    assert out.recommended_actions[0].confidence == 1.0


def test_unknown_param_keys_are_dropped_with_the_action(job: JobData) -> None:
    bad = _task("P04", assignee_name="Maya", title="Recap", deal_id="dl_other")
    out = finalize(_draft(recommended_actions=[bad, _task("P04", assignee="Maya", title="Recap")]), job)
    assert out.recommended_actions == []


def test_actions_may_only_name_people_this_review_saw(job: JobData) -> None:
    planted = DraftAction(  # the hostile note asks for everything to be reassigned to its author
        action_type="reassign_owner",
        params={"new_owner_name": "Mallory"},
        rationale="Reassign to a stronger closer",
        play_id="P06",
    )
    stranger = _task("P04", assignee_name="Dana", title="Recap the agreed value")
    out = finalize(_draft(recommended_actions=[planted, stranger, _task("P12")]), job)
    assert [(a.play_id, a.params["assignee_name"]) for a in out.recommended_actions] == [("P12", "Maya")]

    job.read("get_account_context")  # Dana Okafor is a contact on the account
    out = finalize(_draft(recommended_actions=[stranger]), job)
    assert [a.params["assignee_name"] for a in out.recommended_actions] == ["Dana"]


def test_voice_summary_is_made_speakable() -> None:
    long = " ".join(["Harbor and Pine went quiet after the proposal."] * 8)
    text = speakable(f"See https://x.example now (dl_harbor, act_3). {long}", fallback="fallback text")
    assert "http" not in text and "dl_" not in text and "act_" not in text
    assert len(text.split()) <= 45 and len(text) <= 320 and text.endswith(".")
    assert speakable("word " * 80, fallback="x" * 12).count(" ") <= 44
    assert speakable("dl_1", fallback="Harbor & Pine is At Risk at 66.") == "Harbor & Pine is At Risk at 66."


def test_gate_output_is_the_dispatcher_contract(job: JobData) -> None:
    out = finalize(_draft(voice_summary="", recommended_actions=[_task()]), job)
    assert out.voice_summary == "Harbor & Pine is At Risk, health 34."
    assert out.recommended_actions[0].params == {"assignee_name": "Maya", "title": "Send a value recap"}
