"""scripts/demo.py and the seeder Lambda's demo operations against the seeded test database (DD §24)."""

from __future__ import annotations

import json
import secrets
from datetime import timedelta

import pytest

from db.seed import ops
from tests.conftest import KESTREL, OWNER_URL, WS
from tests.unit.test_demo_cli import demo


async def test_status_reads_the_seeded_workspace(seeded: str) -> None:
    async with demo.scoped(OWNER_URL, WS) as conn:
        st = await demo.status(conn, WS)
    assert st["name"] == demo.scenarios.WORKSPACE_NAME
    assert sum(st["bands"].values()) > 0
    assert st["scenarios"][KESTREL][0] is not None
    assert st["board"] and st["board"][0][3] == "at_risk"


async def test_clock_offset_round_trip(seeded: str) -> None:
    async with demo.scoped(OWNER_URL, WS) as conn:
        await demo.set_offset(conn, WS, timedelta(hours=14))
        assert await conn.fetchval("SELECT clock_offset FROM workspaces WHERE id = $1", WS) == timedelta(
            hours=14
        )
        await demo.set_offset(conn, WS, timedelta(0))
        assert await conn.fetchval("SELECT clock_offset FROM workspaces WHERE id = $1", WS) == timedelta(0)


async def test_outbox_retry_rearms_only_dead_rows(seeded: str) -> None:
    key = f"demo-test-{secrets.token_hex(4)}"
    async with demo.scoped(OWNER_URL, WS) as conn:
        dead = await conn.fetchval(
            """INSERT INTO outbox (workspace_id, kind, payload, dedupe_key, status, attempts, last_error)
               VALUES ($1, 'slack_message', '{}'::jsonb, $2, 'dead', 5, 'boom') RETURNING id""",
            WS,
            f"{key}-dead",
        )
        sent = await conn.fetchval(
            """INSERT INTO outbox (workspace_id, kind, payload, dedupe_key, status, attempts)
               VALUES ($1, 'slack_message', '{}'::jsonb, $2, 'sent', 1) RETURNING id""",
            WS,
            f"{key}-sent",
        )
        try:
            assert await demo.retry_dead(conn, WS, [dead, sent], all_dead=False) == [dead]
            row = await conn.fetchrow("SELECT status, attempts, last_error FROM outbox WHERE id = $1", dead)
            assert dict(row) == {"status": "pending", "attempts": 0, "last_error": None}
            assert await conn.fetchval("SELECT status FROM outbox WHERE id = $1", sent) == "sent"
        finally:
            await conn.execute("DELETE FROM outbox WHERE id = ANY($1::bigint[])", [dead, sent])


async def test_the_seeder_lambdas_demo_operations(seeded: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """``{"demo": ...}`` events (``db.seed.ops.run_op``), as ``demo.py --env dev|demo`` sends them."""
    monkeypatch.setenv("WF_DEMO_MODE", "true")
    got = await ops.run_op(OWNER_URL, WS, {"demo": "status"})
    st = ops.status_from_json(json.loads(json.dumps(got["status"])))
    assert got["demo_mode"] and got["scenarios_ok"] == ops.scenarios_ok(st)  # other tests move the scores
    assert st["name"] == demo.scenarios.WORKSPACE_NAME and st["scenarios"][KESTREL][0] is not None

    try:
        assert (await ops.run_op(OWNER_URL, WS, {"demo": "clock", "offset": "+14h"}))[
            "clock_offset"
        ] == "+14h"
        st = ops.status_from_json((await ops.run_op(OWNER_URL, WS, {"demo": "status"}))["status"])
        assert st["clock_offset"] == timedelta(hours=14)
    finally:
        await ops.run_op(OWNER_URL, WS, {"demo": "clock", "offset": "0"})

    assert (await ops.run_op(OWNER_URL, WS, {"demo": "outbox-retry", "ids": [-1]}))["rearmed"] == []
    for bad in ({"demo": "outbox-retry"}, {"demo": "clock", "offset": "+99x"}, {"demo": "drop"}):
        with pytest.raises(ValueError):
            await ops.run_op(OWNER_URL, WS, bad)
    with pytest.raises(LookupError):
        await ops.run_op(OWNER_URL, "ws_missing", {"demo": "clock", "offset": "0"})
