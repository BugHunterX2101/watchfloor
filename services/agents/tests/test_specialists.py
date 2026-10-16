"""Data-tool binding, specialist budgets, memory recall and model wiring."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from agents import specialists
from agents.data_tools import JobData, make_data_tools, mcp_caller
from agents.data_tools import ToolFailed as Failed
from agents.lead import build_lead, lead_prompt
from agents.memory import make_recall
from agents.models import Misconfigured, haiku, sonnet
from conftest import DEAL, FakeMCP
from watchfloor_core.settings import Settings


def test_data_tools_are_bound_to_the_job(mcp: FakeMCP) -> None:
    job = JobData(deal_id=DEAL, call=mcp)
    tools = make_data_tools(job)
    assert set(tools) == {
        "get_deal_timeline",
        "get_deal_history",
        "explain_deal_risk",
        "get_account_context",
        "get_prior_triage",
        "get_playbook",
    }
    json.loads(tools["get_deal_timeline"](days=500))
    json.loads(tools["get_account_context"]())  # resolves the account through explain_deal_risk first
    assert mcp.calls[0] == ("get_deal_timeline", {"deal_id": DEAL, "days": 90})
    assert mcp.calls[1:] == [
        ("explain_deal_risk", {"deal_id": DEAL}),
        ("get_account_context", {"account_id": "ac_harbor"}),
    ]
    assert {"act_1", "act_6", "act_3"} <= job.evidence_ids()
    assert job.people == {"Maya C.", "Dana Okafor"}


def test_tool_errors_reach_the_model_as_json() -> None:
    job = JobData(deal_id=DEAL, call=FakeMCP(fail={"get_deal_history"}))
    assert "outside this review" in json.loads(make_data_tools(job)["get_deal_history"]())["error"]


def test_mcp_caller_reads_structured_or_text_content() -> None:
    class Client:
        def __init__(self, result: dict[str, Any]) -> None:
            self.result = result

        def call_tool_sync(self, *args: Any, **kw: Any) -> dict[str, Any]:
            return self.result

    ok = {"status": "success", "toolUseId": "t", "content": [], "structuredContent": {"a": 1}}
    text = {"status": "success", "toolUseId": "t", "content": [{"text": '{"b": 2}'}]}
    err = {"status": "error", "toolUseId": "t", "content": [{"text": "DEAL_NOT_FOUND"}]}
    assert mcp_caller(Client(ok))("x", {}) == {"a": 1}
    assert mcp_caller(Client(text))("x", {}) == {"b": 2}
    with pytest.raises(Failed, match="DEAL_NOT_FOUND"):
        mcp_caller(Client(err))("x", {})


class FakeAgent:
    def __init__(self, reply: str = '{"ok": true}', delay: float = 0) -> None:
        self.reply, self.delay = reply, delay

    async def invoke_async(self, prompt: str, **kw: Any) -> str:
        assert "turns" in kw["limits"]
        await asyncio.sleep(self.delay)
        return self.reply


async def test_specialists_are_budgeted(
    model_settings: Settings, job: JobData, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[str] = []

    def fake(name: str, agent: FakeAgent) -> Any:
        def build(*args: Any) -> FakeAgent:
            built.append(name)
            return agent

        return build

    monkeypatch.setattr(specialists.analyst, "build", fake("analyst", FakeAgent('{"evidence": []}')))
    monkeypatch.setattr(specialists.critic, "build", fake("critic", FakeAgent()))
    monkeypatch.setattr(specialists.strategist, "build", fake("strategist", FakeAgent(delay=5)))
    monkeypatch.setitem(specialists.BUDGETS, "playbook_strategist", specialists.Budget(3, 0.05, 2))

    tools = {
        t.tool_name: t
        for t in specialists.make_specialist_tools(make_data_tools(job), recall=None, settings=model_settings)
    }
    assert set(tools) == {"signal_analyst", "account_context", "playbook_strategist", "critic_review"}

    assert await tools["signal_analyst"]() == '{"evidence": []}'
    assert await tools["signal_analyst"]() == '{"evidence": []}'  # cached, not rerun
    assert "ran out of time" in await tools["playbook_strategist"](evidence_json="{}", facts_json="{}")
    for _ in range(2):
        assert await tools["critic_review"](draft_json="{}", evidence_json="{}") == '{"ok": true}'
    assert "budget spent" in await tools["critic_review"](draft_json="{}", evidence_json="{}")
    assert built == ["analyst", "strategist", "critic", "critic"]


def test_only_an_explicit_approval_counts_as_checked() -> None:
    assert specialists.critic_verdict('{"ok": true, "issues": []}') == "ok"
    assert specialists.critic_verdict('```json\n{"ok": true}\n```') == "ok"
    for reply in ('{"ok": false, "issues": ["x"]}', '{"ok": "true"}', "Looks fine to me.", "{not json}", ""):
        assert specialists.critic_verdict(reply) == "issues", reply


async def test_each_specialist_run_is_recorded_on_the_job(job: JobData) -> None:
    class Metered(FakeAgent):
        class event_loop_metrics:  # noqa: N801 - mimics the Strands attribute
            cycle_count = 2
            accumulated_usage = {"inputTokens": 500, "outputTokens": 40, "totalTokens": 540}

    def broken() -> Any:
        raise RuntimeError("no model")

    budget = specialists.Budget(turns=1, timeout_s=0.05, calls=1)
    await specialists.run_specialist("critic_review", lambda: Metered('{"ok": false}'), "p", budget, job=job)
    await specialists.run_specialist("signal_analyst", lambda: Metered("{}"), "p", budget, job=job)
    await specialists.run_specialist("account_context", broken, "p", budget, job=job)
    await specialists.run_specialist("playbook_strategist", lambda: FakeAgent(delay=5), "p", budget, job=job)
    assert job.specialists == {
        "critic_review": "issues",
        "signal_analyst": "ok",
        "account_context": "failed",
        "playbook_strategist": "timed_out",
    }
    assert (job.usage.calls, job.usage.input_tokens, job.usage.output_tokens) == (4, 1000, 80)

    await specialists.run_specialist("critic_review", lambda: Metered('{"ok": true}'), "p", budget, job=job)
    assert job.specialists["critic_review"] == "ok"  # the last critic run decides


def test_the_fast_tier_can_run_on_its_own_gemini_model() -> None:
    from agents.models import model_names, tier_model_id

    one = Settings(env="dev", models_enabled=False, gemini_model_id="gemini-a", gemini_api_key="k")
    two = Settings(
        env="dev",
        models_enabled=False,
        gemini_model_id="gemini-a",
        gemini_haiku_model_id="gemini-b",
        gemini_api_key="k",
    )
    assert (tier_model_id(one, "sonnet"), tier_model_id(one, "haiku"), model_names(one)) == ("gemini-a",) * 3
    assert (tier_model_id(two, "sonnet"), tier_model_id(two, "haiku")) == ("gemini-a", "gemini-b")
    assert model_names(two) == "gemini-a,gemini-b"
    assert haiku(settings=two).get_config()["model_id"] == "gemini-b"
    assert sonnet(settings=two).get_config()["model_id"] == "gemini-a"


def test_recall_wraps_memories_as_untrusted() -> None:
    seen: list[tuple[str, str, str]] = []

    def retrieve(memory_id: str, namespace: str, query: str) -> list[dict[str, Any]]:
        seen.append((memory_id, namespace, query))
        return [{"content": {"text": "User confirmed P01 for Kestrel Biotech."}}, {"content": {}}]

    out = json.loads(make_recall("mem-1", "ws_l:us_p", retrieve)(query="Harbor & Pine"))
    assert seen == [("mem-1", "/actors/ws_l:us_p/", "Harbor & Pine")]
    assert out == [
        '<crm_data trust="untrusted" source="memory" id="mem1">\nUser confirmed P01 for Kestrel Biotech.\n</crm_data>'
    ]
    assert make_recall(None, "ws_l:us_p", retrieve)(query="x") == "[]"

    def down(*args: Any) -> list[dict[str, Any]]:
        raise RuntimeError("throttled")

    assert make_recall("mem-1", "ws_l:us_p", down)(query="x") == "[]"


def test_models_need_their_ids(model_settings: Settings) -> None:
    with pytest.raises(Misconfigured, match="WF_SONNET_MODEL_ID"):
        sonnet(settings=Settings(env="dev"))
    model = haiku(settings=model_settings)
    assert model.get_config()["model_id"] == "test.haiku"
    assert model.get_config()["temperature"] == 0.0
    assert "top_p" not in model.get_config()


def test_lead_has_the_four_specialists(model_settings: Settings, job: JobData) -> None:
    lead = build_lead(job, model_settings, recall=make_recall(None, None))
    assert set(lead.tool_names) == {
        "signal_analyst",
        "account_context",
        "playbook_strategist",
        "critic_review",
    }
    assert 'source="deal" id="dl_harbor"' in lead_prompt(job)


def test_without_bedrock_the_agents_run_on_gemini(model_settings: Settings) -> None:
    """ADR-023: Bedrock first while models are on; Gemini for both tiers once they are off."""
    from strands.models.gemini import GeminiModel

    gemini = {"gemini_model_id": "gemini-test", "gemini_api_key": "k"}
    assert haiku(settings=model_settings.model_copy(update=gemini)).get_config()["model_id"] == "test.haiku"
    off = model_settings.model_copy(update={**gemini, "models_enabled": False})
    for model in (sonnet(settings=off), haiku(settings=off)):
        assert isinstance(model, GeminiModel) and model.get_config()["model_id"] == "gemini-test"
    with pytest.raises(Misconfigured, match="WF_HAIKU_MODEL_ID"):
        haiku(settings=model_settings.model_copy(update={"models_enabled": False}))
