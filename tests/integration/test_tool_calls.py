"""End-to-end tool calls over HTTP against the seeded workspace (J1, J3, J5; DD §5.5, §16.1)."""

from __future__ import annotations

import asyncpg
import httpx
import pytest
from watchfloor_core.db import ws_tx

from tests.conftest import HARBOR, KESTREL, OWNER_URL, TokenFactory
from tests.mcp_client import Mcp


async def test_briefing_leads_with_the_demo_board(seeded: str, mcp: Mcp) -> None:
    briefing = await mcp.ok("get_risk_briefing", {"limit": 3})
    assert [d["deal_id"] for d in briefing["deals"]][:2] == [KESTREL, HARBOR]
    assert briefing["deals"][0]["score"] == 71 and briefing["deals"][0]["band"] == "at_risk"
    assert briefing["all_clear"] is False
    assert len(briefing["summary"].split()) <= 60


async def test_explain_returns_rigs_breakdown(seeded: str, mcp: Mcp) -> None:
    explained = await mcp.ok("explain_deal_risk", {"deal_id": KESTREL})
    assert explained["risk"]["score"] == 71
    assert [r["code"] for r in explained["reasons"]][:1] == ["SILENT_DAYS"]
    assert len(explained["trend_14d"]) == 14


async def test_business_errors_are_tool_results_not_protocol_errors(seeded: str, mcp: Mcp) -> None:
    result = await mcp.call("explain_deal_risk", {"deal_id": "dl_01J9DOESNOTEXIST000000000"})
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "DEAL_NOT_FOUND"


async def test_every_call_is_audited_with_its_trace_id(seeded: str, mcp: Mcp) -> None:
    trace = "0af7651916cd43dd8448eb211c80319c"
    call = {"name": "lookup_deal", "arguments": {"name_query": "harbor"}}
    r = await mcp.post(
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": call},
        traceparent=f"00-{trace}-b7ad6b7169203331-01",
    )
    assert r.status_code == 200
    async with ws_tx(seeded) as conn:
        row = await conn.fetchrow(
            "SELECT tool, outcome, user_id, args_hash FROM audit_log WHERE trace_id = $1", trace
        )
    assert row["tool"] == "lookup_deal" and row["outcome"] == "ok"
    assert row["user_id"] == "us_01J9PRIYA000000000000000"
    assert len(row["args_hash"]) == 32, "arguments are stored only as a SHA-256"


async def test_log_update_applies_now_and_proposes_forecast_changes(seeded: str, mcp: Mcp) -> None:
    update = await mcp.ok(
        "log_deal_update",
        {"deal_id": HARBOR, "note": "IT wants a security review first.", "amount_usd": 61000},
    )
    assert update["applied_fields"] == ["note"]
    pending = update["pending_proposal"]
    assert pending["requires_confirmation"] is True and pending["confirmation_token"].startswith("wf1.")


@pytest.fixture
async def other_workspace(seeded: str) -> str:
    """A second, empty tenant with one user, written as the owner role."""
    conn = await asyncpg.connect(OWNER_URL)
    try:
        await conn.execute(
            "INSERT INTO workspaces (id, name) VALUES ('ws_other', 'Other Co') ON CONFLICT DO NOTHING"
        )
        await conn.execute("SELECT set_config('app.workspace_id', 'ws_other', false)")
        await conn.execute(
            """INSERT INTO users (id, workspace_id, display_name, email, role)
               VALUES ('us_OUTSIDER', 'ws_other', 'Olive Outsider', 'olive@other.example', 'owner')
               ON CONFLICT DO NOTHING"""
        )
    finally:
        await conn.close()
    return "ws_other"


async def test_other_workspace_sees_nothing(
    other_workspace: str, client: httpx.AsyncClient, token_factory: TokenFactory
) -> None:
    outsider = Mcp(client, token_factory(sub="us_OUTSIDER", ws=other_workspace))
    result = await outsider.call("explain_deal_risk", {"deal_id": KESTREL})
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "DEAL_NOT_FOUND"
    briefing = await outsider.ok("get_risk_briefing", {})
    assert briefing["deals"] == [] and briefing["all_clear"] is True


async def test_token_for_unknown_workspace_is_rejected(
    seeded: str, client: httpx.AsyncClient, token_factory: TokenFactory
) -> None:
    ghost = Mcp(client, token_factory(sub="us_GHOST", ws="ws_deleted"))
    env = await ghost.call_raw("get_risk_briefing", {})
    assert env["error"] == {"code": -32600, "message": "Unknown workspace"}


async def test_healthz_reports_database(seeded: str, client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True and r.json()["db"] == "ok"


async def test_deep_triage_is_refused_up_front_without_models(
    seeded: str, mcp: Mcp, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WF_MODELS_ENABLED=false (an AWS Free plan account): no job is queued that could only fail later."""
    from watchfloor_core.settings import reset_settings

    monkeypatch.setenv("WF_MODELS_ENABLED", "false")
    reset_settings()
    try:
        before = await _job_count()
        result = await mcp.call("start_deep_triage", {"deal_id": HARBOR})
        assert result["isError"] is True
        err = result["structuredContent"]["error"]
        assert err["code"] == "TEMPORARILY_UNAVAILABLE" and "aren't available" in err["message"]
        assert "scored reasons" in err["recovery"]
        assert await _job_count() == before
    finally:
        monkeypatch.delenv("WF_MODELS_ENABLED")
        reset_settings()


async def _job_count() -> int:
    conn = await asyncpg.connect(OWNER_URL)
    try:
        return await conn.fetchval("SELECT count(*) FROM triage_jobs")
    finally:
        await conn.close()
