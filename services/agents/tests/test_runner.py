"""The event stream: one terminal event per job, whatever happens."""

from __future__ import annotations

import asyncio
import functools
import json
from typing import Any

import httpx
import pytest
from agents import main
from agents.data_tools import JobData
from agents.runner import triage_events
from agents.schemas import DraftDriver, LeadDraft
from conftest import FakeMCP
from watchfloor_core.models.triage import TriageOutput
from watchfloor_core.settings import Settings


async def _events(payload: Any, settings: Settings, mcp: FakeMCP, **kw: Any) -> list[dict[str, Any]]:
    return [e async for e in triage_events(payload, settings=settings, connect=mcp.connector(), **kw)]


async def test_offline_run_streams_progress_then_a_result(
    payload: dict[str, Any], offline_settings: Settings, mcp: FakeMCP
) -> None:
    events = await _events(payload, offline_settings, mcp)
    assert [e["type"] for e in events] == ["progress", "progress", "progress", "result"]
    assert events[-1]["engine"] == "offline"
    TriageOutput.model_validate(events[-1]["output"])
    assert mcp.opened == mcp.closed == 1
    assert {name for name, _ in mcp.calls} == {"explain_deal_risk", "get_playbook", "get_deal_timeline"}


async def test_invalid_payload_never_touches_mcp(
    payload: dict[str, Any], offline_settings: Settings, mcp: FakeMCP
) -> None:
    events = await _events({**payload, "deal_id": "ac_harbor", "mcp_token": ""}, offline_settings, mcp)
    assert events == [{"type": "error", "code": "invalid_payload", "error": "invalid deal_id, mcp_token"}]
    assert [e["code"] for e in await _events("not a dict", offline_settings, mcp)] == ["invalid_payload"]
    assert mcp.opened == 0


async def test_dev_without_models_is_misconfigured(payload: dict[str, Any], mcp: FakeMCP) -> None:
    events = await _events(payload, Settings(env="dev"), mcp)
    assert [(e["type"], e.get("code")) for e in events] == [("error", "misconfigured")]


async def test_the_lead_gets_the_job_and_memory(
    payload: dict[str, Any], model_settings: Settings, mcp: FakeMCP
) -> None:
    seen: dict[str, Any] = {}

    async def lead(job: JobData, s: Settings, recall: Any) -> LeadDraft:
        seen["account"] = job.account_id
        seen["recall"] = recall.tool_name
        await asyncio.to_thread(job.read, "get_deal_timeline")
        return LeadDraft(
            voice_summary="Harbor & Pine went quiet after the proposal.",
            risk_drivers=[DraftDriver(factor="R", claim="No reply in 9 days", evidence_ids=["act_3"])],
            confidence=0.8,
        )

    events = await _events(payload, model_settings, mcp, lead=lead)
    assert events[-1]["type"] == "result" and events[-1]["engine"] == "strands"
    assert seen == {"account": "ac_harbor", "recall": "recall_preferences"}


async def test_the_deadline_ends_the_job_as_timed_out(
    payload: dict[str, Any], model_settings: Settings, mcp: FakeMCP
) -> None:
    async def slow(job: JobData, s: Settings, recall: Any) -> LeadDraft:
        await asyncio.sleep(5)
        raise AssertionError("unreachable")

    events = await _events(payload, model_settings, mcp, lead=slow, timeout_s=0.2)
    assert events[-1] == {"type": "error", "code": "timed_out", "error": "no result within 0 seconds"}
    assert mcp.closed == 1


async def test_unsupported_drafts_and_failed_reads_are_errors(
    payload: dict[str, Any], model_settings: Settings, offline_settings: Settings
) -> None:
    async def invent(job: JobData, s: Settings, recall: Any) -> LeadDraft:
        return LeadDraft(
            voice_summary="Everything is on fire at Harbor & Pine.",
            risk_drivers=[DraftDriver(factor="S", claim="Invented", evidence_ids=["hist_404"])],
            confidence=0.9,
        )

    events = await _events(payload, model_settings, FakeMCP(), lead=invent)
    assert events[-1]["code"] == "rejected"

    events = await _events(payload, offline_settings, FakeMCP(fail={"explain_deal_risk"}))
    assert events[-1]["code"] == "data_unavailable" and "outside this review" in events[-1]["error"]

    def unreachable(url: str, token: str) -> Any:
        raise ConnectionError("refused")

    events = [e async for e in triage_events(payload, settings=offline_settings, connect=unreachable)]
    assert events[-1] == {
        "type": "error",
        "code": "data_unavailable",
        "error": "could not open the MCP session (ConnectionError)",
    }


async def test_runtime_serves_the_stream_as_sse(
    payload: dict[str, Any], offline_settings: Settings, mcp: FakeMCP, monkeypatch: Any
) -> None:
    def fake_events(p: Any) -> Any:
        return triage_events(p, settings=offline_settings, connect=mcp.connector())

    monkeypatch.setattr(main, "triage_events", fake_events)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as http:
        assert (await http.get("/ping")).status_code == 200
        r = await http.post("/invocations", json=payload)
    assert r.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith("data:")]
    assert events[-1]["type"] == "result"
    TriageOutput.model_validate(events[-1]["output"])


def _draft() -> LeadDraft:
    return LeadDraft(
        voice_summary="Harbor & Pine went quiet after the proposal.",
        risk_drivers=[DraftDriver(factor="R", claim="No reply in 9 days", evidence_ids=["act_3"])],
        confidence=0.8,
    )


async def test_a_failing_model_hands_the_review_to_the_offline_engine(
    payload: dict[str, Any], model_settings: Settings, mcp: FakeMCP
) -> None:
    """ADR-023: a throttled Lead (no back-off) ends in the offline engine's result, not a failed job."""
    from strands.types.exceptions import ModelThrottledException

    async def throttled(job: JobData, s: Settings, recall: Any) -> LeadDraft:
        raise ModelThrottledException("quota exhausted")

    events = await _events(payload, model_settings, mcp, lead=throttled)
    assert events[-1]["type"] == "result" and events[-1]["engine"] == "offline"
    TriageOutput.model_validate(events[-1]["output"])
    assert mcp.opened == mcp.closed == 1


async def test_gemini_alone_runs_the_agents(payload: dict[str, Any], mcp: FakeMCP) -> None:
    gemini_only = Settings(env="dev", models_enabled=False, gemini_model_id="gemini-test", gemini_api_key="k")
    seen: list[str | None] = []

    async def lead(job: JobData, s: Settings, recall: Any) -> LeadDraft:
        seen.append(s.gemini_model_id)
        await asyncio.to_thread(job.read, "get_deal_timeline")
        return _draft()

    events = await _events(payload, gemini_only, mcp, lead=lead)
    assert events[-1]["type"] == "result" and events[-1]["engine"] == "strands"
    assert seen == ["gemini-test"]


async def test_the_output_records_who_answered_and_what_checked_it(
    payload: dict[str, Any], model_settings: Settings, offline_settings: Settings, mcp: FakeMCP
) -> None:
    def lead_with(critic: str | None) -> Any:
        async def lead(job: JobData, s: Settings, recall: Any) -> LeadDraft:
            await asyncio.to_thread(job.read, "get_deal_timeline")
            job.specialists.update({"signal_analyst": "ok", "account_context": "failed"})
            if critic:
                job.specialists["critic_review"] = critic
            return _draft()

        return lead

    approved = (await _events(payload, model_settings, mcp, lead=lead_with("ok")))[-1]["output"]
    assert approved["engine"] == "strands" and approved["checked"] is True
    assert approved["confidence"] == 0.8
    assert approved["specialists"] == {
        "signal_analyst": "ok",
        "account_context": "failed",
        "critic_review": "ok",
    }

    for critic in (
        "issues",
        "timed_out",
        None,
    ):  # unresolved issues, a critic that never answered, none at all
        out = TriageOutput.model_validate(
            (await _events(payload, model_settings, mcp, lead=lead_with(critic)))[-1]["output"]
        )
        assert out.checked is False and out.confidence == 0.5, critic

    offline = (await _events(payload, offline_settings, mcp))[-1]["output"]
    assert offline["engine"] == "offline" and offline["checked"] is None and offline["specialists"] == {}


async def test_every_job_logs_its_model_usage(
    payload: dict[str, Any], model_settings: Settings, mcp: FakeMCP, caplog: pytest.LogCaptureFixture
) -> None:
    class Metrics:
        cycle_count = 3
        accumulated_usage = {"inputTokens": 1200, "outputTokens": 90, "totalTokens": 1290}

    async def lead(job: JobData, s: Settings, recall: Any) -> LeadDraft:
        await asyncio.to_thread(job.read, "get_deal_timeline")
        job.usage.add(Metrics())
        job.usage.add(Metrics())
        return _draft()

    with caplog.at_level("INFO", logger="watchfloor.usage"):
        await _events(payload, model_settings, mcp, lead=lead)
    [line] = [r.getMessage() for r in caplog.records if r.name == "watchfloor.usage"]
    assert line == (
        "llm_usage scope=triage engine=strands model=test.sonnet,test.haiku calls=6 input_tokens=2400"
        f" output_tokens=180 job={payload['job_id']} outcome=result"
    )


def test_the_lambda_handler_returns_the_whole_stream(
    payload: dict[str, Any], offline_settings: Settings, mcp: FakeMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-023: the dispatcher reads the function's events as it reads the runtime's SSE stream."""
    from agents import handler

    bound = functools.partial(triage_events, settings=offline_settings, connect=mcp.connector())
    monkeypatch.setattr(handler, "triage_events", bound)
    out = handler.handler(payload, None)
    assert [e["type"] for e in out["events"]] == ["progress", "progress", "progress", "result"]
    json.dumps(out)  # what Lambda serializes
