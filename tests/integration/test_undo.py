"""Undo on the receipt and more time on a preview (ADR-021).

An executed action can be undone for 30 seconds; its Slack post is held until then, so an undo
withdraws it before anyone sees it. A preview can be given more time, up to 15 minutes in all.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from typing import Any

import pytest
from watchfloor_core.clock import FixedClock
from watchfloor_core.db import ws_tx
from watchfloor_core.services.context import set_clock

from tests.conftest import FROZEN_NOW, HARBOR, KESTREL
from tests.mcp_client import Mcp

# Every test here writes through the tools, so each needs the seeded DB (and skips without it) even
# when it takes only ``mcp``: the pool is opened by ``seeded``, not by the client.
pytestmark = pytest.mark.usefixtures("seeded")

DANIEL = "us_01J9DANIEL00000000000000"  # Kestrel's seeded owner
MARCUS = "us_01J9MARCUS00000000000000"
OWNER_HISTORY = "SELECT count(*) FROM deal_history WHERE deal_id = $1 AND field = 'owner_id'"
LATEST = (
    "SELECT id, status FROM action_proposals WHERE executed_at IS NOT NULL ORDER BY executed_at DESC, id DESC"
)


async def _one(ws: str, sql: str, *args: Any) -> Any:
    async with ws_tx(ws) as conn:
        return await conn.fetchval(sql, *args)


async def _latest(ws: str) -> Any:
    async with ws_tx(ws) as conn:
        return await conn.fetchrow(LATEST + " LIMIT 1")


@pytest.fixture
async def kestrel_restored(seeded: str) -> AsyncIterator[None]:
    """The seeded DB is shared by the session: put Kestrel back with Daniel afterwards."""
    yield
    async with ws_tx(seeded) as conn:
        await conn.execute("UPDATE deals SET owner_id = $2 WHERE id = $1", KESTREL, DANIEL)


@pytest.fixture
def at() -> Iterator[Any]:
    """Move the frozen clock: ``at(seconds)`` puts "now" that far past FROZEN_NOW."""

    def move(seconds: float) -> None:
        set_clock(FixedClock(FROZEN_NOW + timedelta(seconds=seconds)))

    yield move
    set_clock(FixedClock(FROZEN_NOW))


async def _reassign_to_maya(mcp: Mcp) -> tuple[str, dict[str, Any]]:
    proposal = await mcp.ok(
        "propose_action", {"deal_id": KESTREL, "action_type": "reassign_owner", "new_owner_name": "Maya"}
    )
    token = proposal["confirmation_token"]
    return token, await mcp.ok("confirm_action", {"confirmation_token": token})


async def test_undo_restores_the_owner_and_withdraws_the_post(
    seeded: str, mcp: Mcp, sqs_stub: list[int], kestrel_restored: None
) -> None:
    history_before = await _one(seeded, OWNER_HISTORY, KESTREL)
    token, done = await _reassign_to_maya(mcp)
    assert done["undo_until"], "an undoable action says until when"
    post = f"pr:{(await _latest(seeded))['id']}"
    held = await _one(seeded, "SELECT next_attempt_at FROM outbox WHERE dedupe_key = $1", post)
    assert held > FROZEN_NOW + timedelta(seconds=30), "the post waits out the undo window"

    undone = await mcp.ok("undo_action", {"confirmation_token": token})
    assert undone["status"] == "undone" and undone["reverted"] == ["owner_restored", "post_withdrawn"]
    assert undone["summary"].startswith("Undone. Daniel")
    assert await _one(seeded, "SELECT owner_id FROM deals WHERE id = $1", KESTREL) == DANIEL
    assert await _one(seeded, "SELECT status FROM outbox WHERE dedupe_key = $1", post) == "cancelled"
    assert await _one(seeded, OWNER_HISTORY, KESTREL) == history_before, "an undone change leaves no history"

    again = await mcp.call("undo_action", {"confirmation_token": token})
    assert again["isError"] is True and "already undone" in again["structuredContent"]["error"]["message"]
    replay = await mcp.call("confirm_action", {"confirmation_token": token})
    assert replay["isError"] is True and "undone" in replay["structuredContent"]["error"]["message"]


async def test_undo_cancels_a_created_task(seeded: str, mcp: Mcp, sqs_stub: list[int]) -> None:
    proposal = await mcp.ok(
        "propose_action",
        {"deal_id": HARBOR, "action_type": "create_task", "assignee_name": "Maya", "title": "Call the CFO"},
    )
    token = proposal["confirmation_token"]
    await mcp.ok("confirm_action", {"confirmation_token": token})
    undone = await mcp.ok("undo_action", {"confirmation_token": token})
    assert undone["reverted"] == ["task_cancelled", "post_withdrawn"]
    proposal_id = (await _latest(seeded))["id"]
    status = await _one(seeded, "SELECT status FROM tasks WHERE source_proposal_id = $1", proposal_id)
    assert status == "cancelled"


async def test_undo_restores_the_amount_and_drops_its_history(seeded: str, mcp: Mcp) -> None:
    before = await _one(seeded, "SELECT amount_cents FROM deals WHERE id = $1", HARBOR)
    hist = "SELECT count(*) FROM deal_history WHERE deal_id = $1 AND field = 'amount_cents'"
    hist_before = await _one(seeded, hist, HARBOR)
    update = await mcp.ok("log_deal_update", {"deal_id": HARBOR, "amount_usd": 51000})
    token = update["pending_proposal"]["confirmation_token"]
    await mcp.ok("confirm_action", {"confirmation_token": token})
    assert await _one(seeded, "SELECT amount_cents FROM deals WHERE id = $1", HARBOR) == 51000_00

    undone = await mcp.ok("undo_action", {"confirmation_token": token})
    assert undone["reverted"] == ["deal_restored", "risk_recomputed"] and "Health is" in undone["summary"]
    assert await _one(seeded, "SELECT amount_cents FROM deals WHERE id = $1", HARBOR) == before
    assert await _one(seeded, hist, HARBOR) == hist_before


async def test_undo_is_refused_after_the_window(mcp: Mcp, at: Any, kestrel_restored: None) -> None:
    token, _ = await _reassign_to_maya(mcp)
    at(31)
    late = await mcp.call("undo_action", {"confirmation_token": token})
    assert late["isError"] is True
    error = late["structuredContent"]["error"]
    assert error["code"] == "INVALID_ACTION" and "too late" in error["message"] and error["recovery"]


async def test_undo_is_refused_if_the_owner_changed_again(
    seeded: str, mcp: Mcp, kestrel_restored: None
) -> None:
    token, _ = await _reassign_to_maya(mcp)
    async with ws_tx(seeded) as conn:
        await conn.execute("UPDATE deals SET owner_id = $2 WHERE id = $1", KESTREL, MARCUS)
    result = await mcp.call("undo_action", {"confirmation_token": token})
    assert (
        result["isError"] is True and "nothing was undone" in result["structuredContent"]["error"]["message"]
    )
    assert await _one(seeded, "SELECT owner_id FROM deals WHERE id = $1", KESTREL) == MARCUS
    assert (await _latest(seeded))["status"] == "executed", (
        "a refused undo writes nothing, not even its claim"
    )


async def test_a_draft_has_no_undo(mcp: Mcp) -> None:
    proposal = await mcp.ok(
        "propose_action",
        {"deal_id": HARBOR, "action_type": "draft_followup", "intent": "book the security review"},
    )
    token = proposal["confirmation_token"]
    done = await mcp.ok("confirm_action", {"confirmation_token": token})
    assert "undo_until" not in done
    result = await mcp.call("undo_action", {"confirmation_token": token})
    assert result["isError"] is True and "only goes to you" in result["structuredContent"]["error"]["message"]


async def test_more_time_extends_the_same_token_up_to_15_minutes(mcp: Mcp, at: Any) -> None:
    proposal = await mcp.ok(
        "propose_action",
        {"deal_id": HARBOR, "action_type": "notify_channel", "message": "Security review first"},
    )
    token = proposal["confirmation_token"]

    at(4 * 60)  # one minute left: five more on top of it
    first = await mcp.ok("extend_preview", {"confirmation_token": token})
    assert first["expires_at"] > proposal["expires_at"] and "6 more minutes" in first["summary"]

    at(9 * 60)  # expires at 10; five more would pass the 15-minute cap, so it stops at 15
    second = await mcp.ok("extend_preview", {"confirmation_token": token})
    assert "6 more minutes" in second["summary"], "never past 15 minutes after the preview was made"

    at(14 * 60)
    capped = await mcp.call("extend_preview", {"confirmation_token": token})
    assert capped["isError"] is True
    assert "can't be extended again" in capped["structuredContent"]["error"]["message"]

    done = await mcp.ok("confirm_action", {"confirmation_token": token})
    assert done["status"] == "executed", "the extended token still confirms"
    late = await mcp.call("extend_preview", {"confirmation_token": token})
    assert late["isError"] is True and "already approved" in late["structuredContent"]["error"]["message"]


async def test_an_expired_preview_cannot_be_extended(mcp: Mcp, at: Any) -> None:
    proposal = await mcp.ok(
        "propose_action",
        {"deal_id": HARBOR, "action_type": "notify_channel", "message": "Pricing is approved"},
    )
    at(6 * 60)
    result = await mcp.call("extend_preview", {"confirmation_token": proposal["confirmation_token"]})
    assert result["isError"] is True and result["structuredContent"]["error"]["code"] == "TOKEN_EXPIRED"
