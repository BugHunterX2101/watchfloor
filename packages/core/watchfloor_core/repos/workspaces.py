from __future__ import annotations

from datetime import timedelta

import asyncpg


async def all_ids(conn: asyncpg.Connection) -> list[str]:
    return [r["id"] for r in await conn.fetch("SELECT id FROM workspaces ORDER BY id")]


async def get(conn: asyncpg.Connection, workspace_id: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "SELECT id, name, timezone, currency, clock_offset FROM workspaces WHERE id = $1", workspace_id
    )


async def set_clock_offset(conn: asyncpg.Connection, workspace_id: str, offset: timedelta) -> None:
    await conn.execute("UPDATE workspaces SET clock_offset = $2 WHERE id = $1", workspace_id, offset)
