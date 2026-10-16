"""Chat turns: the offline router, the Strands path, token refresh and the ui:// view."""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path

import httpx
import pytest
from conftest import (
    BOARD_HTML,
    BRIEFING,
    DONE,
    EXPLAIN,
    HARBOR,
    PREVIEW,
    FakeAuth,
    FakeMcp,
    ScriptedModel,
    make_config,
    sign_in,
)
from harness_api.agent import TURN_TIMEOUT_S, bedrock_model, gemini_model, to_messages
from harness_api.app import create_app
from harness_api.offline import deal_words


async def test_signed_out_turns_are_refused(client: httpx.AsyncClient) -> None:
    r = await client.post("/converse", json={"message": "what needs my attention?"})
    assert r.status_code == 401 and r.json()["error"] == "signed_out"
    assert (await client.post("/converse", content="message=hi")).status_code == 415


async def test_offline_briefing_turn_shows_the_board(client: httpx.AsyncClient, mcp: FakeMcp) -> None:
    await sign_in(client)
    r = await client.post("/converse", json={"message": "What needs my attention today?"})
    body = r.json()
    assert r.status_code == 200 and body["engine"] == "offline" and body["reply"] == BRIEFING
    assert [c["tool"] for c in body["calls"]] == ["get_risk_briefing"] and body["calls"][0]["ok"]
    assert body["ui"]["uri"] == "ui://watchfloor/triage-board" and body["ui"]["html"] == BOARD_HTML
    assert mcp.headers[-1]["mcp-protocol-version"] == "2025-11-25"
    assert mcp.headers[-1]["authorization"] == "Bearer at1"


async def test_offline_router_finds_the_deal_by_name(client: httpx.AsyncClient, mcp: FakeMcp) -> None:
    await sign_in(client)
    r = await client.post("/converse", json={"message": "Why is Harbor & Pine at risk?"})
    assert r.json()["reply"] == EXPLAIN
    assert mcp.calls[-2] == ("lookup_deal", {"name_query": "Harbor & Pine"})
    assert mcp.calls[-1] == ("explain_deal_risk", {"deal_id": HARBOR})
    assert r.json()["ui"] is None  # explain has no view in this fake

    r = await client.post("/converse", json={"message": "Run a deep review on Harbor"})
    assert mcp.names()[-1] == "start_deep_triage" and "started" in r.json()["reply"]
    r = await client.post("/converse", json={"message": "Why is it at risk?"})
    assert r.json()["reply"] == "Which deal do you mean?"


async def test_offline_follow_up_uses_the_deal_named_before(client: httpx.AsyncClient, mcp: FakeMcp) -> None:
    """The board suggests "Dig into it.": the offline router finds "it" in the user's last question."""
    await sign_in(client)
    history = [
        {"role": "user", "text": "Why is Harbor & Pine at risk?"},
        {"role": "assistant", "text": EXPLAIN},
    ]
    r = await client.post("/converse", json={"message": "Dig into it.", "history": history})
    assert r.status_code == 200
    assert mcp.calls[-2] == ("lookup_deal", {"name_query": "Harbor & Pine"})
    assert mcp.names()[-1] == "start_deep_triage"


async def test_long_history_lines_are_clipped_not_refused(client: httpx.AsyncClient) -> None:
    """One long model reply must not turn every later turn into a 400."""
    await sign_in(client)
    history = [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "x" * 5000}]
    r = await client.post("/converse", json={"message": "What needs my attention today?", "history": history})
    assert r.status_code == 200 and r.json()["reply"] == BRIEFING


async def test_view_html_is_fetched_once_per_process(client: httpx.AsyncClient, mcp: FakeMcp) -> None:
    await sign_in(client)
    await client.post("/converse", json={"message": "What needs my attention today?"})
    first = len(mcp.headers)  # tools/list, tools/call, resources/read
    r = await client.post("/converse", json={"message": "What needs my attention today?"})
    assert r.json()["ui"]["html"] == BOARD_HTML
    assert len(mcp.headers) - first == 2, "the second turn reuses the cached view HTML"


def test_deal_words_strip_the_intent() -> None:
    assert deal_words("Why is Kestrel Biotech's deal at risk?") == "Kestrel Biotech"
    assert deal_words("how's orbital dental doing") == "orbital dental"
    assert deal_words("why?") == ""


async def test_expired_tokens_are_refreshed_once(
    client: httpx.AsyncClient, auth: FakeAuth, mcp: FakeMcp
) -> None:
    await sign_in(client)
    mcp.valid.discard("at1")  # the server stops accepting the first access token
    r = await client.post("/converse", json={"message": "briefing"})
    assert r.status_code == 200 and auth.tokens["grant_type"] == "refresh_token"
    assert auth.tokens["refresh_token"] == "rt1" and "wf_h=" in r.headers["set-cookie"]
    assert mcp.headers[-1]["authorization"] == "Bearer at2"

    mcp.valid.clear()
    auth.refusals.add("refresh_token")
    r = await client.post("/converse", json={"message": "briefing"})
    assert r.status_code == 401 and r.json()["error"] == "signed_out"


async def test_strands_turn_calls_mcp_tools_and_speaks_the_summary(
    tmp_path: Path, auth: FakeAuth, mcp: FakeMcp
) -> None:
    model = ScriptedModel("explain_deal_risk", {"deal_id": HARBOR})
    app = create_app(
        make_config(tmp_path, env="dev", haiku_model_id="test.haiku"),
        auth_transport=httpx.MockTransport(auth),
        mcp_transport=httpx.MockTransport(mcp),
        model_factory=lambda cfg: model,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://harness.test") as c:
        await sign_in(c)
        history = [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "Hello."}]
        r = await c.post("/converse", json={"message": "Why is Harbor at risk?", "history": history})
    body = r.json()
    assert r.status_code == 200 and body["engine"] == "strands" and body["reply"] == EXPLAIN
    assert mcp.calls == [("explain_deal_risk", {"deal_id": HARBOR})]
    assert [m["role"] for m in model.seen[0]] == ["user", "assistant", "user"]


async def test_nova_reasoning_is_never_spoken(tmp_path: Path, auth: FakeAuth, mcp: FakeMcp) -> None:
    model = ScriptedModel("explain_deal_risk", {"deal_id": HARBOR}, thinking="Read the summary aloud.")
    app = create_app(
        make_config(tmp_path, env="dev", haiku_model_id="amazon.nova-lite-v1:0"),
        auth_transport=httpx.MockTransport(auth),
        mcp_transport=httpx.MockTransport(mcp),
        model_factory=lambda cfg: model,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://harness.test") as c:
        await sign_in(c)
        r = await c.post("/converse", json={"message": "Why is Harbor at risk?"})
    assert r.status_code == 200 and r.json()["reply"] == EXPLAIN


def test_nova_runs_greedy_in_the_harness(tmp_path: Path) -> None:
    config = bedrock_model(
        make_config(tmp_path, env="dev", haiku_model_id="amazon.nova-lite-v1:0")
    ).get_config()
    assert config["temperature"] == 0.0 and config["max_tokens"] == 1024
    assert config["additional_request_fields"] == {"inferenceConfig": {"topK": 1}}


class FailingModel(ScriptedModel):
    """A model Bedrock refuses, e.g. throttled or not enabled for the account."""

    async def stream(self, *a: object, **k: object):  # type: ignore[override]
        raise RuntimeError("ThrottlingException")
        yield {}


async def test_a_failed_model_turn_falls_back_to_the_offline_engine(
    tmp_path: Path, auth: FakeAuth, mcp: FakeMcp
) -> None:
    app = create_app(
        make_config(tmp_path, env="dev", haiku_model_id="amazon.nova-lite-v1:0"),
        auth_transport=httpx.MockTransport(auth),
        mcp_transport=httpx.MockTransport(mcp),
        model_factory=lambda cfg: FailingModel("get_risk_briefing"),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://harness.test") as c:
        await sign_in(c)
        r = await c.post("/converse", json={"message": "What needs my attention today?"})
    body = r.json()
    assert r.status_code == 200 and body["engine"] == "offline" and body["reply"] == BRIEFING


class ThrottledModel(ScriptedModel):
    """Bedrock with the account's quota at 0: every call is throttled."""

    async def stream(self, *a: object, **k: object):  # type: ignore[override]
        from strands.types.exceptions import ModelThrottledException

        raise ModelThrottledException("Too many tokens per day, please wait before trying again.")
        yield {}


async def test_a_throttled_model_falls_back_without_backing_off(
    tmp_path: Path, auth: FakeAuth, mcp: FakeMcp
) -> None:
    app = create_app(
        make_config(tmp_path, env="dev", haiku_model_id="amazon.nova-lite-v1:0"),
        auth_transport=httpx.MockTransport(auth),
        mcp_transport=httpx.MockTransport(mcp),
        model_factory=lambda cfg: ThrottledModel("get_risk_briefing"),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://harness.test") as c:
        await sign_in(c)
        started = time.monotonic()
        r = await c.post("/converse", json={"message": "What needs my attention today?"})
        elapsed = time.monotonic() - started
    body = r.json()
    assert r.status_code == 200 and body["engine"] == "offline" and body["reply"] == BRIEFING
    assert elapsed < 3  # Strands' default retry would sleep 4 s before its first retry


GEMINI = {"gemini_model_id": "gemini-flash-lite-latest", "gemini_api_key": "test-key"}


async def test_a_throttled_bedrock_hands_the_turn_to_gemini(
    tmp_path: Path, auth: FakeAuth, mcp: FakeMcp, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness_api import agent

    monkeypatch.setattr(agent, "bedrock_model", lambda cfg: ThrottledModel("get_risk_briefing"))
    monkeypatch.setattr(
        agent, "gemini_model", lambda cfg: ScriptedModel("explain_deal_risk", {"deal_id": HARBOR})
    )
    app = create_app(
        make_config(tmp_path, env="dev", haiku_model_id="amazon.nova-lite-v1:0", **GEMINI),
        auth_transport=httpx.MockTransport(auth),
        mcp_transport=httpx.MockTransport(mcp),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://harness.test") as c:
        await sign_in(c)
        r = await c.post("/converse", json={"message": "Why is Harbor at risk?"})
        health = (await c.get("/healthz")).json()
    body = r.json()
    assert r.status_code == 200 and body["engine"] == "strands" and body["reply"] == EXPLAIN
    assert health == {"ok": True, "engine": "strands", "models": ["bedrock", "gemini"]}


def test_gemini_alone_runs_the_model_engine(tmp_path: Path) -> None:
    cfg = make_config(tmp_path, env="dev", models_enabled=False, **GEMINI)
    assert cfg.engine == "strands"
    model = gemini_model(cfg)
    assert model.get_config()["model_id"] == "gemini-flash-lite-latest"
    assert model.client_args["api_key"] == "test-key"
    assert model.client_args["http_options"] == {"timeout": int(TURN_TIMEOUT_S * 1000)}


def test_the_harness_fits_inside_api_gateway(tmp_path: Path) -> None:
    model = bedrock_model(make_config(tmp_path, env="dev", haiku_model_id="amazon.nova-lite-v1:0"))
    config = model.client.meta.config
    assert TURN_TIMEOUT_S < 30 and config.read_timeout == TURN_TIMEOUT_S
    assert config.retries == {"mode": "standard", "total_max_attempts": 3}


async def test_deployed_environments_need_a_model(tmp_path: Path, auth: FakeAuth, mcp: FakeMcp) -> None:
    app = create_app(
        make_config(tmp_path, env="demo"),
        auth_transport=httpx.MockTransport(auth),
        mcp_transport=httpx.MockTransport(mcp),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://harness.test") as c:
        await sign_in(c)
        r = await c.post("/converse", json={"message": "briefing"})
    assert r.status_code == 503 and r.json()["error"] == "misconfigured"
    assert mcp.calls == []


async def test_views_call_allowlisted_tools_with_the_users_token(
    client: httpx.AsyncClient, mcp: FakeMcp
) -> None:
    body = {"name": "explain_deal_risk", "arguments": {"deal_id": HARBOR}}
    assert (await client.post("/api/tool", json=body)).status_code == 401

    await sign_in(client)
    r = await client.post("/api/tool", json=body)
    out = r.json()
    assert r.status_code == 200 and out["isError"] is False
    assert out["structuredContent"] == {"summary": EXPLAIN}
    assert out["call"]["tool"] == "explain_deal_risk" and mcp.headers[-1]["authorization"] == "Bearer at1"

    r = await client.post("/api/tool", json={"name": "set_standing_order", "arguments": {}})
    assert r.status_code == 403 and r.json()["error"] == "not_allowed"
    assert mcp.names() == ["explain_deal_risk"]  # the refused call never reached MCP


class StepsModel(ScriptedModel):
    """Calls each (tool, arguments) in turn, one per model call, then speaks the last result's summary.

    ``fail_after`` raises instead of answering once the steps are done, like a model throttled mid-turn.
    """

    def __init__(self, *steps: tuple[str, dict[str, object]], fail_after: bool = False) -> None:
        super().__init__(steps[0][0])
        self.steps, self.fail_after = list(steps), fail_after
        self.prompts: list[str] = []

    async def stream(self, messages, *a, **k):  # type: ignore[override]
        self.prompts.append(a[1] if len(a) > 1 else k.get("system_prompt") or "")
        done = sum(
            1 for m in messages if m["role"] == "user" and any("toolResult" in b for b in m["content"])
        )
        if done < len(self.steps):
            tool, args = self.steps[done]
            yield {"messageStart": {"role": "assistant"}}
            yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": f"t{done}", "name": tool}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(args)}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
            return
        if self.fail_after:
            raise RuntimeError("ThrottlingException")
        last = messages[-1]["content"][0]["toolResult"]
        text = last["content"][0]["text"]
        with contextlib.suppress(ValueError, KeyError, TypeError):  # a refusal is plain text
            text = json.loads(text)["summary"]
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockDelta": {"delta": {"text": text}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}


def _model_app(
    tmp_path: Path, auth: FakeAuth, mcp: FakeMcp, models: list[ScriptedModel]
) -> httpx.AsyncClient:
    """A signed-in-able client whose turns use ``models`` in order, one per turn."""
    app = create_app(
        make_config(tmp_path, env="dev", haiku_model_id="test.haiku"),
        auth_transport=httpx.MockTransport(auth),
        mcp_transport=httpx.MockTransport(mcp),
        model_factory=lambda cfg: models.pop(0),
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://harness.test")


PROPOSE = ("propose_action", {"deal_id": HARBOR})


async def test_a_spoken_yes_confirms_the_preview_of_the_turn_before(
    tmp_path: Path, auth: FakeAuth, mcp: FakeMcp
) -> None:
    """A1: the token rides in the encrypted cookie, and only the next turn may confirm with it."""
    first = StepsModel(PROPOSE, ("confirm_action", {"confirmation_token": "tok_1"}))
    wrong = StepsModel(("confirm_action", {"confirmation_token": "tok_other"}))
    yes = StepsModel(("confirm_action", {"confirmation_token": "tok_1"}))
    undo = StepsModel(("undo_action", {"confirmation_token": "tok_1"}))
    async with _model_app(tmp_path, auth, mcp, [first, wrong, yes, undo]) as c:
        await sign_in(c)
        r = await c.post("/converse", json={"message": "Have Maya call the CFO by Friday."})
        assert mcp.names() == ["propose_action"], "the same-turn confirmation never reached the server"
        assert "hasn't answered this preview" in r.json()["reply"]
        sealed = r.cookies.get("wf_h_pending")
        assert sealed and "tok_1" not in sealed  # encrypted, not readable by the page

        r = await c.post("/converse", json={"message": "Yes."})
        assert mcp.names() == ["propose_action"] and "isn't the preview" in r.json()["reply"]

        r = await c.post("/converse", json={"message": "Yes, go ahead."})
        assert 'confirmation_token "tok_1"' in yes.prompts[0] and PREVIEW in yes.prompts[0]
        assert (
            mcp.calls[-1] == ("confirm_action", {"confirmation_token": "tok_1"}) and r.json()["reply"] == DONE
        )

        r = await c.post("/converse", json={"message": "Actually, undo that."})
        assert 'undo_action with confirmation_token "tok_1"' in undo.prompts[0]
        assert mcp.names()[-1] == "undo_action"
        assert "wf_h_pending" not in c.cookies  # nothing waits any more


async def test_the_offline_engine_confirms_a_preview_made_in_a_view(
    client: httpx.AsyncClient, mcp: FakeMcp
) -> None:
    await sign_in(client)
    r = await client.post("/api/tool", json={"name": "propose_action", "arguments": {"deal_id": HARBOR}})
    assert r.status_code == 200 and r.cookies.get("wf_h_pending")

    r = await client.post("/converse", json={"message": "yes, but make it Monday"})
    assert mcp.names()[-1] != "confirm_action"  # not a plain yes: the router does something else

    r = await client.post("/converse", json={"message": "Yes, please."})
    assert r.json()["engine"] == "offline" and r.json()["reply"] == DONE
    assert mcp.calls[-1] == ("confirm_action", {"confirmation_token": "tok_1"})
    r = await client.post("/converse", json={"message": "Undo that."})
    assert mcp.calls[-1] == ("undo_action", {"confirmation_token": "tok_1"})

    await client.post("/api/logout")
    assert "wf_h_pending" not in client.cookies


async def test_a_model_that_failed_after_a_change_is_not_replayed(
    tmp_path: Path, auth: FakeAuth, mcp: FakeMcp
) -> None:
    """A4: no second model or offline router after a write; a read-only failure still falls back."""
    wrote = StepsModel(("set_standing_order", {"min_score": 65}), fail_after=True)
    read = StepsModel(("explain_deal_risk", {"deal_id": HARBOR}), fail_after=True)
    async with _model_app(tmp_path, auth, mcp, [wrote, read]) as c:
        await sign_in(c)
        r = await c.post("/converse", json={"message": "Watch for deals under health 35."})
        body = r.json()
        assert mcp.names() == ["set_standing_order"]
        assert (
            body["engine"] == "strands" and body["reply"].startswith("Saved.") and "stopped" in body["reply"]
        )

        r = await c.post("/converse", json={"message": "What needs my attention today?"})
        assert r.json()["engine"] == "offline" and mcp.names()[-1] == "get_risk_briefing"


async def test_every_turn_logs_its_model_usage(
    tmp_path: Path, auth: FakeAuth, mcp: FakeMcp, caplog: pytest.LogCaptureFixture
) -> None:
    async with _model_app(
        tmp_path, auth, mcp, [ScriptedModel("explain_deal_risk", {"deal_id": HARBOR})]
    ) as c:
        await sign_in(c)
        with caplog.at_level("INFO", logger="watchfloor.usage"):
            await c.post("/converse", json={"message": "Why is Harbor at risk?"})
    [line] = [r.getMessage() for r in caplog.records if r.name == "watchfloor.usage"]
    assert line.startswith("llm_usage scope=harness engine=strands model=model calls=2 input_tokens=0")


def test_the_fast_tier_gemini_model_runs_the_harness(tmp_path: Path) -> None:
    cfg = make_config(
        tmp_path, env="dev", models_enabled=False, gemini_haiku_model_id="gemini-fast", **GEMINI
    )
    assert gemini_model(cfg).get_config()["model_id"] == "gemini-fast"


def test_history_becomes_alternating_strands_messages() -> None:
    msgs = to_messages(
        [("assistant", "stray"), ("user", "a"), ("user", "b"), ("assistant", "c"), ("user", "pending")]
    )
    assert msgs == [
        {"role": "user", "content": [{"text": "a\nb"}]},
        {"role": "assistant", "content": [{"text": "c"}]},
    ]


def test_the_offline_engine_runs_wherever_models_are_off(tmp_path: Path) -> None:
    """An AWS Free plan environment (WF_MODELS_ENABLED=false) still converses, and says which engine."""
    haiku = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert make_config(tmp_path, env="dev", models_enabled=False, haiku_model_id=haiku).engine == "offline"
    assert make_config(tmp_path, env="dev", haiku_model_id=haiku).engine == "strands"
    assert make_config(tmp_path, env="dev").engine is None
