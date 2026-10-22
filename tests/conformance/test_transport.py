"""Streamable HTTP transport cases CF-01, CF-02 and CF-05 to CF-11 (DD §22.3). No database needed."""

from __future__ import annotations

from tests.mcp_client import PROTOCOL_VERSION, Mcp

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "alexa-plus", "version": "x"},
    },
}


def _without_version(mcp: Mcp) -> dict[str, str]:
    h = mcp.headers()
    h.pop("MCP-Protocol-Version")
    return h


async def test_cf01_initialize_echoes_version_and_issues_no_session(mcp: Mcp) -> None:
    r = await mcp.client.post("/mcp", json=INITIALIZE, headers=_without_version(mcp))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    result = r.json()["result"]
    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "watchfloor"
    assert "tools" in result["capabilities"] and "resources" in result["capabilities"]
    assert "propose_action then confirm_action" in result["instructions"]
    assert "mcp-session-id" not in r.headers


async def test_cf02_notification_is_accepted_with_empty_body(mcp: Mcp) -> None:
    r = await mcp.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202
    assert r.content == b""


async def test_cf05_missing_protocol_version_is_400(mcp: Mcp) -> None:
    msg = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    r = await mcp.client.post("/mcp", json=msg, headers=_without_version(mcp))
    assert r.status_code == 400


async def test_cf06_unsupported_protocol_version_is_400(mcp: Mcp) -> None:
    r = await mcp.post(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, **{"MCP-Protocol-Version": "2099-01-01"}
    )
    assert r.status_code == 400
    r = await mcp.post(INITIALIZE, **{"MCP-Protocol-Version": "2099-01-01"})
    assert r.status_code == 400


def _initialize(version: str) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": version,
            "capabilities": {},
            "clientInfo": {"name": "kiro", "version": "x"},
        },
    }


async def test_a_2025_06_18_client_keeps_its_version_after_initialize(mcp: Mcp) -> None:
    """Kiro offers 2025-06-18 (ADR-034): the server agrees, then serves requests carrying that header."""
    r = await mcp.client.post("/mcp", json=_initialize("2025-06-18"), headers=_without_version(mcp))
    assert r.status_code == 200
    assert r.json()["result"]["protocolVersion"] == "2025-06-18"
    r = await mcp.post(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, **{"MCP-Protocol-Version": "2025-06-18"}
    )
    assert r.status_code == 200
    assert r.json()["result"]["tools"]


async def test_initialize_with_an_unallowed_version_gets_a_version_the_gate_accepts(mcp: Mcp) -> None:
    """The SDK echoes any revision it knows (2024-11-05 too); the gate makes it counter-offer an allowed one."""
    for offered in ("2024-11-05", "2025-03-26", "2099-01-01"):
        r = await mcp.client.post("/mcp", json=_initialize(offered), headers=_without_version(mcp))
        assert r.status_code == 200, offered
        agreed = r.json()["result"]["protocolVersion"]
        assert agreed == PROTOCOL_VERSION, offered
        r = await mcp.post(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, **{"MCP-Protocol-Version": agreed}
        )
        assert r.status_code == 200, offered


async def test_cf07_accept_without_event_stream_is_406(mcp: Mcp) -> None:
    r = await mcp.post({"jsonrpc": "2.0", "id": 4, "method": "tools/list"}, Accept="application/json")
    assert r.status_code == 406


async def test_cf08_get_and_delete_are_405(mcp: Mcp) -> None:
    for method in ("GET", "DELETE"):
        r = await mcp.client.request(method, "/mcp", headers=mcp.headers())
        assert r.status_code == 405, method
        assert r.headers["allow"] == "POST"


async def test_cf09_malformed_json_is_400_with_parse_error(mcp: Mcp) -> None:
    r = await mcp.client.post("/mcp", content=b'{"jsonrpc": "2.0", "id": ', headers=mcp.headers())
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32700


async def test_cf10_unknown_method_is_32601(mcp: Mcp) -> None:
    env = await mcp.rpc("watchfloor/selfDestruct")
    assert env["error"]["code"] == -32601


async def test_cf11_schema_violations_are_32602_with_field_path(mcp: Mcp) -> None:
    env = await mcp.call_raw("explain_deal_risk", {"deal_id": "not-a-deal"})
    assert env["error"]["code"] == -32602
    assert "deal_id" in env["error"]["message"]

    env = await mcp.call_raw("get_risk_briefing", {"limit": 9})
    assert env["error"]["code"] == -32602
    assert "limit" in env["error"]["message"]

    env = await mcp.call_raw("get_risk_briefing", {"verbose": True})
    assert env["error"]["code"] == -32602
    assert "verbose" in env["error"]["message"]

    env = await mcp.call_raw(
        "propose_action", {"deal_id": "dl_01J9KESTREL0000000000000", "action_type": "create_task"}
    )
    assert env["error"]["code"] == -32602
    assert "create_task requires: assignee_name, title" in env["error"]["message"]


async def test_unknown_tool_is_32602(mcp: Mcp) -> None:
    env = await mcp.call_raw("drop_all_deals", {})
    assert env["error"] == {"code": -32602, "message": "Unknown tool: drop_all_deals"}


async def test_traceparent_is_honoured_and_echoed(mcp: Mcp) -> None:
    trace = "4bf92f3577b34da6a3ce929d0e0e4736"
    r = await mcp.post(
        {"jsonrpc": "2.0", "id": 5, "method": "ping"}, traceparent=f"00-{trace}-00f067aa0ba902b7-01"
    )
    assert r.status_code == 200
    assert r.headers["traceparent"].startswith(f"00-{trace}-")
