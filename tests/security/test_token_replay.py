"""Confirmation tokens are single-use, bound to their parameters, and never guessable (DD §8.2)."""

from __future__ import annotations

from watchfloor_core.db import ws_tx

from tests.conftest import KESTREL
from tests.mcp_client import Mcp


async def _task_count(ws: str, title: str) -> int:
    async with ws_tx(ws) as conn:
        return await conn.fetchval("SELECT count(*) FROM tasks WHERE title = $1", title)


async def test_confirm_is_single_use(seeded: str, mcp: Mcp, sqs_stub: list[int]) -> None:
    title = "Replay check: send the security addendum"
    proposal = await mcp.ok(
        "propose_action",
        {"deal_id": KESTREL, "action_type": "create_task", "assignee_name": "Maya", "title": title},
    )
    assert proposal["requires_confirmation"] is True
    first = await mcp.ok("confirm_action", {"confirmation_token": proposal["confirmation_token"]})
    second = await mcp.ok("confirm_action", {"confirmation_token": proposal["confirmation_token"]})
    assert first["status"] == "executed"
    assert second["status"] == "already_executed"
    assert second["audit_id"] == first["audit_id"]
    assert await _task_count(seeded, title) == 1
    assert sqs_stub, "the Slack post must be handed to the queue after commit"


async def test_unknown_token_is_a_business_error(seeded: str, mcp: Mcp) -> None:
    result = await mcp.call(
        "confirm_action", {"confirmation_token": "wf1.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}
    )
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["code"] == "TOKEN_MISMATCH"
    assert error["message"] and result["structuredContent"]["summary"]


async def test_a_newer_proposal_supersedes_the_pending_one(seeded: str, mcp: Mcp) -> None:
    base = {"deal_id": KESTREL, "action_type": "notify_channel"}
    old = await mcp.ok("propose_action", {**base, "message": "Kestrel needs an exec sponsor."})
    new = await mcp.ok("propose_action", {**base, "message": "Kestrel needs an exec sponsor this week."})
    stale = await mcp.call("confirm_action", {"confirmation_token": old["confirmation_token"]})
    assert stale["isError"] is True
    done = await mcp.ok("confirm_action", {"confirmation_token": new["confirmation_token"]})
    assert done["status"] == "executed"
