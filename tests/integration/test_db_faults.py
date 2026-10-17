"""The service when its database misbehaves: dropped connections and a saturated pool (plan phase 3).

A caller must get a clean, retryable answer, never a hang or "Internal error", and the very next
call must work again without a restart.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import asyncpg
import httpx
from auth_server.app import create_app
from auth_server.clients import HARNESS
from auth_server.signer import FileSigner
from auth_server.storage import AuthStore
from watchfloor_core import db

from tests.conftest import ISSUER, KID, OWNER_URL, TEST_DB
from tests.integration.test_auth_server import AUTH_URL
from tests.mcp_client import Mcp


async def _kill_app_connections() -> int:
    """What an RDS failover does: every wf_app session ends at once."""
    conn = await asyncpg.connect(OWNER_URL)
    try:
        rows = await conn.fetch(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND usename = 'wf_app'",
            TEST_DB,
        )
    finally:
        await conn.close()
    return len(rows)


async def test_after_every_connection_drops_callers_recover_without_a_restart(seeded: str, mcp: Mcp) -> None:
    await mcp.ok("get_risk_briefing", {})  # the pool is warm
    assert await _kill_app_connections() >= 1

    first = await mcp.call_raw("get_risk_briefing", {})
    assert "error" not in first, f"a dropped connection must not surface as {first.get('error')}"
    result = first["result"]
    if result.get("isError"):  # acceptable only as a clean, retryable answer
        assert result["structuredContent"]["error"]["code"] == "TEMPORARILY_UNAVAILABLE"

    assert (await mcp.ok("get_risk_briefing", {}))["deals"], "the next call works again"


async def test_a_saturated_pool_answers_unavailable_quickly_instead_of_hanging(seeded: str, mcp: Mcp) -> None:
    pool = db.pool()
    held = [await pool.acquire() for _ in range(pool.get_max_size())]
    try:
        t0 = time.perf_counter()
        result = await asyncio.wait_for(mcp.call("get_risk_briefing", {}), timeout=20)
        waited = time.perf_counter() - t0
    finally:
        for conn in held:
            await pool.release(conn)

    assert (
        result["isError"] is True
        and result["structuredContent"]["error"]["code"] == "TEMPORARILY_UNAVAILABLE"
    )
    assert waited < 8, f"callers must not queue for {waited:.0f} s on a full pool"
    assert (await mcp.ok("get_risk_briefing", {}))["deals"], "and the pool serves again once it frees up"


async def test_the_auth_server_answers_503_with_retry_after_when_its_database_is_full(
    seeded: str, signing_key: tuple[Any, dict[str, Any]]
) -> None:
    private, _ = signing_key
    store = await AuthStore.open(AUTH_URL, min_size=1, max_size=1)
    pool = store._pool  # noqa: SLF001 - to hold its only connection
    held = await pool.acquire()
    try:
        app = create_app(store=store, signer=FileSigner(private, kid=KID))
        transport = httpx.ASGITransport(app=app, client=("10.9.9.9", 50000))
        async with httpx.AsyncClient(transport=transport, base_url=ISSUER) as c:
            form = {
                "grant_type": "authorization_code",
                "code": "x",
                "client_id": HARNESS,
                "redirect_uri": "x",
            }
            response = await asyncio.wait_for(c.post("/token", data=form), timeout=10)
    finally:
        await pool.release(held)
        await store.close()

    assert response.status_code == 503
    assert response.json()["error"] == "temporarily_unavailable"
    assert response.headers["retry-after"] == "5" and response.headers["cache-control"] == "no-store"
