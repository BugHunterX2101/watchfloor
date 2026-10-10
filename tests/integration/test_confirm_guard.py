"""An approval covers the change its preview showed (ADR-019): if the deal moved on between the
preview and the confirm, the action is refused and nothing is written (DD §8)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from watchfloor_core.db import ws_tx

from tests.conftest import HARBOR, KESTREL, MAYA
from tests.mcp_client import Mcp

DANIEL = "us_01J9DANIEL00000000000000"  # Kestrel's seeded owner
MARCUS = "us_01J9MARCUS00000000000000"


async def _owner(ws: str, deal_id: str) -> str:
    async with ws_tx(ws) as conn:
        return await conn.fetchval("SELECT owner_id FROM deals WHERE id = $1", deal_id)


async def _set_owner(ws: str, deal_id: str, owner_id: str) -> None:
    async with ws_tx(ws) as conn:
        await conn.execute("UPDATE deals SET owner_id = $2 WHERE id = $1", deal_id, owner_id)


@pytest.fixture
async def kestrel_owner_restored(seeded: str) -> AsyncIterator[None]:
    """The seeded DB is shared by the session: put Kestrel back with Daniel afterwards."""
    yield
    await _set_owner(seeded, KESTREL, DANIEL)


async def test_reassign_runs_when_nothing_changed(
    seeded: str, mcp: Mcp, sqs_stub: list[int], kestrel_owner_restored: None
) -> None:
    proposal = await mcp.ok(
        "propose_action", {"deal_id": KESTREL, "action_type": "reassign_owner", "new_owner_name": "Maya"}
    )
    done = await mcp.ok("confirm_action", {"confirmation_token": proposal["confirmation_token"]})
    assert done["status"] == "executed" and "owner_changed" in done["side_effects"]
    assert await _owner(seeded, KESTREL) == MAYA


async def test_reassign_is_refused_if_the_owner_changed_after_the_preview(
    seeded: str, mcp: Mcp, kestrel_owner_restored: None
) -> None:
    proposal = await mcp.ok(
        "propose_action", {"deal_id": KESTREL, "action_type": "reassign_owner", "new_owner_name": "Maya"}
    )
    await _set_owner(seeded, KESTREL, MARCUS)  # someone else moved the deal meanwhile

    result = await mcp.call("confirm_action", {"confirmation_token": proposal["confirmation_token"]})
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["code"] == "INVALID_ACTION"
    assert "nothing was changed" in error["message"] and error["recovery"]
    assert await _owner(seeded, KESTREL) == MARCUS, "the approval must not overwrite the newer owner"


async def test_field_update_is_refused_if_the_amount_changed_after_the_preview(seeded: str, mcp: Mcp) -> None:
    async with ws_tx(seeded) as conn:
        before = await conn.fetchval("SELECT amount_cents FROM deals WHERE id = $1", HARBOR)
    update = await mcp.ok("log_deal_update", {"deal_id": HARBOR, "amount_usd": 58000})
    token = update["pending_proposal"]["confirmation_token"]
    try:
        async with ws_tx(seeded) as conn:
            await conn.execute("UPDATE deals SET amount_cents = $2 WHERE id = $1", HARBOR, before + 100_00)
        result = await mcp.call("confirm_action", {"confirmation_token": token})
        assert result["isError"] is True
        assert result["structuredContent"]["error"]["code"] == "INVALID_ACTION"
        async with ws_tx(seeded) as conn:
            assert (
                await conn.fetchval("SELECT amount_cents FROM deals WHERE id = $1", HARBOR) == before + 100_00
            )
    finally:
        async with ws_tx(seeded) as conn:
            await conn.execute("UPDATE deals SET amount_cents = $2 WHERE id = $1", HARBOR, before)
