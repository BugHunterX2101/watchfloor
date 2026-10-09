"""Explicit user preferences (DD delta D3): read on the hot path, so they live in Postgres."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg

FIELDS = ("briefing_limit", "include_amounts", "focus_segment", "alert_channel")


async def get(conn: asyncpg.Connection, user_id: str, workspace_id: str) -> asyncpg.Record:
    row = await conn.fetchrow(
        "SELECT briefing_limit, include_amounts, focus_segment, alert_channel FROM user_prefs WHERE user_id = $1",
        user_id,
    )
    if row is None:
        row = await conn.fetchrow(
            """INSERT INTO user_prefs (user_id, workspace_id) VALUES ($1, $2)
               ON CONFLICT (user_id) DO UPDATE SET user_id = EXCLUDED.user_id
               RETURNING briefing_limit, include_amounts, focus_segment, alert_channel""",
            user_id,
            workspace_id,
        )
    return row


async def update(
    conn: asyncpg.Connection, user_id: str, workspace_id: str, changes: dict[str, Any], at: datetime
) -> asyncpg.Record:
    await get(conn, user_id, workspace_id)
    sets, args = [], [user_id]
    for field, value in changes.items():
        if field not in FIELDS:
            raise ValueError(field)
        args.append(value)
        sets.append(f"{field} = ${len(args)}")
    args.append(at)
    return await conn.fetchrow(
        f"""UPDATE user_prefs SET {", ".join(sets)}, updated_at = ${len(args)} WHERE user_id = $1
            RETURNING briefing_limit, include_amounts, focus_segment, alert_channel""",  # noqa: S608 - columns come from FIELDS
        *args,
    )


async def last_briefing_at(conn: asyncpg.Connection, user_id: str) -> datetime | None:
    return await conn.fetchval("SELECT last_briefing_at FROM users WHERE id = $1", user_id)


async def touch_last_briefing(conn: asyncpg.Connection, user_id: str, at: datetime) -> None:
    await conn.execute("UPDATE users SET last_briefing_at = $2 WHERE id = $1", user_id, at)
