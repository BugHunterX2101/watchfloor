"""CF-03 and CF-04: scope-filtered tools/list, matching the committed snapshot (DD §6.1, §22.2)."""

from __future__ import annotations

import orjson
from mcp_server.tools.snapshot import SNAPSHOT_PATH

from tests.conftest import AGENT_SCOPE, TokenFactory
from tests.mcp_client import Mcp

USER_TOOLS = [
    "get_risk_briefing",
    "explain_deal_risk",
    "lookup_deal",
    "start_deep_triage",
    "get_triage_result",
    "propose_action",
    "extend_preview",
    "list_standing_orders",
    "confirm_action",
    "undo_action",
    "set_standing_order",
    "remove_standing_order",
    "log_deal_update",
    "update_briefing_preferences",
]
WRITE_TOOLS = {
    "confirm_action",
    "undo_action",
    "set_standing_order",
    "remove_standing_order",
    "log_deal_update",
    "update_briefing_preferences",
}
AGENT_TOOLS = {
    "explain_deal_risk",
    "get_deal_timeline",
    "get_deal_history",
    "get_account_context",
    "get_prior_triage",
    "get_playbook",
}
UI_BINDINGS = {
    "get_risk_briefing": "ui://watchfloor/triage-board",
    "explain_deal_risk": "ui://watchfloor/deal-detail",
    "get_triage_result": "ui://watchfloor/deal-detail",
    "propose_action": "ui://watchfloor/confirm-sheet",
    "log_deal_update": "ui://watchfloor/confirm-sheet",
}


def _snapshot() -> dict[str, list[dict]]:
    return orjson.loads(SNAPSHOT_PATH.read_bytes())


async def test_cf03_user_sees_exactly_14_tools_matching_snapshot(mcp: Mcp) -> None:
    tools = await mcp.tools()
    assert [t["name"] for t in tools] == USER_TOOLS
    assert tools == _snapshot()["user"], (
        "tools/list drifted; run make snapshot and re-run the utterance sheet"
    )


async def test_cf04_agent_sees_6_tools_and_no_write_tools(mcp: Mcp, token_factory: TokenFactory) -> None:
    agent = Mcp(mcp.client, token_factory(sub="svc:triage", scope=AGENT_SCOPE, job="job_any"))
    tools = await agent.tools()
    names = {t["name"] for t in tools}
    assert names == AGENT_TOOLS
    assert not names & WRITE_TOOLS
    assert tools == _snapshot()["agent"]


async def test_read_only_token_sees_8_tools(mcp: Mcp, token_factory: TokenFactory) -> None:
    reader = Mcp(mcp.client, token_factory(scope="watchfloor.read"))
    names = [t["name"] for t in await reader.tools()]
    assert names == [n for n in USER_TOOLS if n not in WRITE_TOOLS]
    env = await reader.call_raw("confirm_action", {"confirmation_token": "wf1.xxxxxxxxxxxxxxxxxxxx"})
    assert env["error"]["code"] == -32602


async def test_every_tool_declares_flat_schemas_and_annotations(mcp: Mcp) -> None:
    for tool in await mcp.tools():
        schema = tool["inputSchema"]
        assert schema["type"] == "object" and schema.get("additionalProperties") is False, tool["name"]
        assert "$defs" not in schema and "$ref" not in orjson.dumps(schema).decode(), tool["name"]
        out = tool["outputSchema"]
        assert out["type"] == "object" and "summary" in out["required"], tool["name"]
        assert "annotations" in tool, tool["name"]
        assert tool["description"].startswith("Use "), tool["name"]
        resource = tool.get("_meta", {}).get("ui", {}).get("resourceUri")
        assert resource == UI_BINDINGS.get(tool["name"]), tool["name"]


async def test_resources_list_and_read(mcp: Mcp) -> None:
    env = await mcp.rpc("resources/list")
    uris = {r["uri"] for r in env["result"]["resources"]}
    assert uris == set(UI_BINDINGS.values())
    env = await mcp.rpc("resources/read", {"uri": "ui://watchfloor/deal-detail"})
    content = env["result"]["contents"][0]
    assert content["mimeType"] == "text/html;profile=mcp-app"
    assert content["text"].lstrip().lower().startswith("<!doctype html>")
    env = await mcp.rpc("resources/read", {"uri": "ui://watchfloor/nope"})
    assert env["error"]["code"] == -32002
