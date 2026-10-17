"""Approvals stay exactly-once and consistent when callers race (plan P0: confirm, undo, extend).

Every test fires its calls together, through the same ASGI app and a pool smaller than the
number of callers, so the callers really do queue on the proposal and deal row locks.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from watchfloor_core.db import ws_tx

from tests.conftest import FROZEN_NOW, HARBOR, KESTREL, TokenFactory
from tests.mcp_client import Mcp

DANIEL = "us_01J9DANIEL00000000000000"  # Kestrel's seeded owner
MARCUS = "us_01J9MARCUS00000000000000"
MAYA = "us_01J9MAYA0000000000000000"
PRIYA = "us_01J9PRIYA000000000000000"
CALLERS = 8

OWNER_HISTORY = "SELECT count(*) FROM deal_history WHERE deal_id = $1 AND field = 'owner_id'"
AUDITED = (
    "SELECT count(*) FROM audit_log WHERE tool = $2 "
    "AND args_hash = (SELECT params_hash FROM action_proposals WHERE id = $1)"
)


async def _val(ws: str, sql: str, *args: Any) -> Any:
    async with ws_tx(ws) as conn:
        return await conn.fetchval(sql, *args)


@pytest.fixture
async def kestrel_restored(seeded: str) -> AsyncIterator[None]:
    """The seeded DB is shared by the session: put Kestrel back with Daniel afterwards."""
    yield
    async with ws_tx(seeded) as conn:
        await conn.execute("UPDATE deals SET owner_id = $2 WHERE id = $1", KESTREL, DANIEL)


def _marcus(mcp: Mcp, token_factory: TokenFactory) -> Mcp:
    return Mcp(mcp.client, token_factory(sub=MARCUS))


async def test_simultaneous_confirms_run_the_action_once(seeded: str, mcp: Mcp, sqs_stub: list[int]) -> None:
    title = "Race check: one task however many clicks"
    proposal = await mcp.ok(
        "propose_action",
        {"deal_id": KESTREL, "action_type": "create_task", "assignee_name": "Maya", "title": title},
    )
    call = ("confirm_action", {"confirmation_token": proposal["confirmation_token"]})
    results = await asyncio.gather(*(mcp.call(*call) for _ in range(CALLERS)))

    errors = [r["structuredContent"] for r in results if r.get("isError")]
    assert not errors, errors
    outcomes = Counter(r["structuredContent"]["status"] for r in results)
    assert outcomes == {"executed": 1, "already_executed": CALLERS - 1}
    assert len({r["structuredContent"]["audit_id"] for r in results}) == 1, (
        "every caller gets the one receipt"
    )

    pid = proposal["proposal_id"]
    assert await _val(seeded, "SELECT count(*) FROM tasks WHERE title = $1", title) == 1
    assert await _val(seeded, "SELECT count(*) FROM outbox WHERE dedupe_key = $1", f"pr:{pid}") == 1
    assert await _val(seeded, AUDITED, pid, "action:create_task") == 1


async def test_two_previews_of_one_change_cannot_both_win(
    seeded: str, mcp: Mcp, token_factory: TokenFactory, kestrel_restored: None
) -> None:
    """Priya and Marcus each preview a different new owner for Kestrel, both from Daniel.

    Approving both at once must apply one and refuse the other as stale, never both.
    """
    marcus = _marcus(mcp, token_factory)
    history_before = await _val(seeded, OWNER_HISTORY, KESTREL)

    to_maya = await mcp.ok(
        "propose_action", {"deal_id": KESTREL, "action_type": "reassign_owner", "new_owner_name": "Maya"}
    )
    to_priya = await marcus.ok(
        "propose_action", {"deal_id": KESTREL, "action_type": "reassign_owner", "new_owner_name": "Priya"}
    )
    first, second = await asyncio.gather(
        mcp.call("confirm_action", {"confirmation_token": to_maya["confirmation_token"]}),
        marcus.call("confirm_action", {"confirmation_token": to_priya["confirmation_token"]}),
    )

    done = [r for r in (first, second) if not r.get("isError")]
    refused = [r for r in (first, second) if r.get("isError")]
    assert len(done) == 1 and len(refused) == 1, (first, second)
    error = refused[0]["structuredContent"]["error"]
    assert error["code"] == "INVALID_ACTION" and "nothing was changed" in error["message"]

    winner = {"Maya": MAYA, "Priya": PRIYA}[done[0]["structuredContent"]["result"]["new_owner"].split()[0]]
    assert await _val(seeded, "SELECT owner_id FROM deals WHERE id = $1", KESTREL) == winner
    assert await _val(seeded, OWNER_HISTORY, KESTREL) == history_before + 1, (
        "one owner change, one history row"
    )


async def test_simultaneous_undos_revert_once(
    seeded: str, mcp: Mcp, sqs_stub: list[int], kestrel_restored: None
) -> None:
    history_before = await _val(seeded, OWNER_HISTORY, KESTREL)
    proposal = await mcp.ok(
        "propose_action", {"deal_id": KESTREL, "action_type": "reassign_owner", "new_owner_name": "Maya"}
    )
    token = proposal["confirmation_token"]
    await mcp.ok("confirm_action", {"confirmation_token": token})

    pid = proposal["proposal_id"]
    undone_before = await _val(
        seeded, AUDITED, pid, "undo:reassign_owner"
    )  # same params as other tests' rows
    call = ("undo_action", {"confirmation_token": token})
    results = await asyncio.gather(*(mcp.call(*call) for _ in range(CALLERS)))

    winners = [r for r in results if not r.get("isError")]
    assert len(winners) == 1 and winners[0]["structuredContent"]["status"] == "undone"
    losers = {r["structuredContent"]["error"]["message"] for r in results if r.get("isError")}
    assert losers == {"That action was already undone."}
    assert await _val(seeded, "SELECT owner_id FROM deals WHERE id = $1", KESTREL) == DANIEL
    assert await _val(seeded, OWNER_HISTORY, KESTREL) == history_before, "an undone change leaves no history"
    assert await _val(seeded, "SELECT status FROM outbox WHERE dedupe_key = $1", f"pr:{pid}") == "cancelled"
    assert await _val(seeded, AUDITED, pid, "undo:reassign_owner") == undone_before + 1


async def test_a_confirm_racing_its_own_undo_leaves_one_consistent_state(
    seeded: str, mcp: Mcp, sqs_stub: list[int], kestrel_restored: None
) -> None:
    proposal = await mcp.ok(
        "propose_action", {"deal_id": KESTREL, "action_type": "reassign_owner", "new_owner_name": "Maya"}
    )
    token = proposal["confirmation_token"]
    confirm, undo = await asyncio.gather(
        mcp.call("confirm_action", {"confirmation_token": token}),
        mcp.call("undo_action", {"confirmation_token": token}),
    )
    assert not confirm.get("isError"), "the confirm is never the one refused"
    status = await _val(seeded, "SELECT status FROM action_proposals WHERE id = $1", proposal["proposal_id"])
    owner = await _val(seeded, "SELECT owner_id FROM deals WHERE id = $1", KESTREL)
    if undo.get("isError"):  # the undo ran before the approval committed: nothing to undo yet
        assert status == "executed" and owner == MAYA
    else:
        assert status == "undone" and owner == DANIEL


async def test_simultaneous_extensions_never_pass_fifteen_minutes(seeded: str, mcp: Mcp) -> None:
    proposal = await mcp.ok(
        "propose_action",
        {"deal_id": HARBOR, "action_type": "notify_channel", "message": "Race check: extend"},
    )
    token = proposal["confirmation_token"]
    await asyncio.gather(*(mcp.call("extend_preview", {"confirmation_token": token}) for _ in range(CALLERS)))

    async with ws_tx(seeded) as conn:
        row = await conn.fetchrow(
            "SELECT created_at, expires_at FROM action_proposals WHERE id = $1", proposal["proposal_id"]
        )
    assert row["expires_at"] <= row["created_at"] + timedelta(minutes=15), "previews last 15 minutes at most"
    assert row["expires_at"] > FROZEN_NOW + timedelta(minutes=5), "at least one extension took effect"


async def test_nobody_else_can_confirm_extend_or_undo_a_preview(
    seeded: str, mcp: Mcp, token_factory: TokenFactory, sqs_stub: list[int]
) -> None:
    """The token is bound to the user who asked: a colleague holding it learns nothing and changes nothing."""
    proposal = await mcp.ok(
        "propose_action",
        {"deal_id": HARBOR, "action_type": "notify_channel", "message": "Not for Marcus to approve"},
    )
    token = proposal["confirmation_token"]
    marcus = _marcus(mcp, token_factory)
    for tool in ("confirm_action", "extend_preview", "undo_action"):
        result = await marcus.call(tool, {"confirmation_token": token})
        assert result["isError"] is True, tool
        assert result["structuredContent"]["error"]["code"] == "TOKEN_MISMATCH", tool
    status = await _val(seeded, "SELECT status FROM action_proposals WHERE id = $1", proposal["proposal_id"])
    assert status == "pending"
