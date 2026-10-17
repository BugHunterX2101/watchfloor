"""A user of one workspace gets nothing from another: no data, no writes, not even a hint (plan P0).

Olive belongs to ``ws_other``; every id below belongs to ``ws_lumenridge``. Whatever Olive asks, the
answer must be the one she would get for an id that exists nowhere, and Lumenridge must not change.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from watchfloor_core.db import ws_tx

from tests.conftest import HARBOR, KESTREL, PRIYA, WS, TokenFactory
from tests.mcp_client import Mcp

OUTSIDER = "us_OUTSIDER"
NOWHERE = "dl_01J9NOSUCHDEAL0000000000"

# Every tool that takes a deal id, with the rest of its arguments.
DEAL_TOOLS = [
    ("explain_deal_risk", {}),
    ("start_deep_triage", {}),
    ("get_triage_result", {}),
    ("log_deal_update", {"note": "A note from outside the workspace"}),
    ("propose_action", {"action_type": "notify_channel", "message": "A post from outside the workspace"}),
]

FOOTPRINT = """SELECT (SELECT count(*) FROM action_proposals), (SELECT count(*) FROM activities),
                      (SELECT count(*) FROM triage_jobs), (SELECT count(*) FROM tasks),
                      (SELECT count(*) FROM outbox), (SELECT count(*) FROM standing_orders),
                      (SELECT count(*) FROM deal_history), (SELECT count(*) FROM alerts)"""


async def _footprint() -> tuple[int, ...]:
    async with ws_tx(WS) as conn:
        return tuple(await conn.fetchrow(FOOTPRINT))


@pytest.fixture
def outsider(other_workspace: str, client: Any, token_factory: TokenFactory) -> Mcp:
    return Mcp(client, token_factory(sub=OUTSIDER, ws=other_workspace))


@pytest.mark.parametrize(("tool", "extra"), DEAL_TOOLS, ids=[t for t, _ in DEAL_TOOLS])
async def test_another_workspaces_deal_looks_exactly_like_no_deal(
    tool: str, extra: dict[str, Any], outsider: Mcp
) -> None:
    before = await _footprint()
    real = await outsider.call(tool, {"deal_id": KESTREL, **extra})
    nowhere = await outsider.call(tool, {"deal_id": NOWHERE, **extra})

    assert real["isError"] is True and nowhere["isError"] is True
    assert real["structuredContent"]["error"] == nowhere["structuredContent"]["error"], "no existence oracle"
    assert real["structuredContent"]["error"]["code"] in {"DEAL_NOT_FOUND", "JOB_NOT_FOUND"}
    assert "kestrel" not in json.dumps(real).lower()
    assert await _footprint() == before, "Lumenridge is untouched"


async def test_names_and_lists_of_another_workspace_stay_hidden(outsider: Mcp) -> None:
    found = await outsider.call("lookup_deal", {"name_query": "Kestrel"})
    assert found["isError"] is True and found["structuredContent"]["error"]["code"] == "DEAL_NOT_FOUND"
    assert "candidates" not in found["structuredContent"]
    rules = await outsider.ok("list_standing_orders", {})
    assert rules["rules"] == []


async def test_another_workspaces_standing_order_cannot_be_removed(mcp: Mcp, outsider: Mcp) -> None:
    rule = await mcp.ok("set_standing_order", {"min_amount_usd": 77_777, "quiet_days": 9})
    try:
        result = await outsider.call("remove_standing_order", {"rule_id": rule["rule_id"]})
        assert result["isError"] is True, "removing someone else's rule must not report success"
        listed = await mcp.ok("list_standing_orders", {})
        assert rule["rule_id"] in {r["rule_id"] for r in listed["rules"]}
    finally:
        await mcp.call("remove_standing_order", {"rule_id": rule["rule_id"]})


async def test_another_workspaces_tokens_and_jobs_are_not_usable(
    seeded: str, mcp: Mcp, outsider: Mcp, sqs_stub: list[int]
) -> None:
    proposal = await mcp.ok(
        "propose_action",
        {"deal_id": HARBOR, "action_type": "notify_channel", "message": "Only for Lumenridge"},
    )
    token = proposal["confirmation_token"]
    for tool in ("confirm_action", "extend_preview", "undo_action"):
        result = await outsider.call(tool, {"confirmation_token": token})
        assert result["isError"] is True and result["structuredContent"]["error"]["code"] == "TOKEN_MISMATCH"
    async with ws_tx(WS) as conn:
        status = await conn.fetchval(
            "SELECT status FROM action_proposals WHERE id = $1", proposal["proposal_id"]
        )
    assert status == "pending"

    job = await mcp.ok("start_deep_triage", {"deal_id": HARBOR})
    result = await outsider.call("get_triage_result", {"job_id": job["job_id"]})
    assert result["isError"] is True and "harbor" not in json.dumps(result).lower()


@pytest.mark.parametrize(
    ("who", "claimed_ws"),
    [(PRIYA, "ws_other"), (OUTSIDER, WS)],
    ids=["lumenridge-user-in-other-workspace", "outsider-in-lumenridge"],
)
async def test_a_user_the_workspace_does_not_have_is_refused(
    who: str, claimed_ws: str, other_workspace: str, client: Any, token_factory: TokenFactory
) -> None:
    """Tokens come from our auth server, so this is defence in depth: the pair (user, workspace) must exist."""
    caller = Mcp(client, token_factory(sub=who, ws=claimed_ws))
    before = await _footprint()
    for tool, args in (("get_risk_briefing", {}), ("start_deep_triage", {"deal_id": KESTREL})):
        env = await caller.call_raw(tool, args)
        assert env.get("error", {}).get("message") == "Unknown user", f"{tool}: {env}"
        assert "kestrel" not in json.dumps(env).lower()
    assert await _footprint() == before
