"""Chat turns: the offline router, the Strands path, token refresh and the ui:// view."""

from __future__ import annotations

from pathlib import Path

import httpx
from conftest import (
    BOARD_HTML,
    BRIEFING,
    EXPLAIN,
    HARBOR,
    FakeAuth,
    FakeMcp,
    ScriptedModel,
    make_config,
    sign_in,
)
from harness_api.agent import to_messages
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
