from __future__ import annotations

import asyncpg


async def get(conn: asyncpg.Connection, user_id: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "SELECT id, display_name, email, role, slack_user_id FROM users WHERE id = $1", user_id
    )


async def all_names(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    return await conn.fetch("SELECT id, display_name, slack_user_id FROM users ORDER BY display_name")


async def find_by_name(conn: asyncpg.Connection, name: str) -> list[asyncpg.Record]:
    """Teammates matching a spoken name: exact full name, else first-name/prefix, else trigram (DD §8.5)."""
    exact = await conn.fetch(
        "SELECT id, display_name, slack_user_id FROM users WHERE lower(display_name) = lower($1)", name
    )
    if exact:
        return exact
    prefix = await conn.fetch(
        """SELECT id, display_name, slack_user_id FROM users
           WHERE lower(display_name) LIKE lower($1) || '%'
              OR lower(split_part(display_name, ' ', 1)) = lower(split_part($1, ' ', 1))
           ORDER BY display_name""",
        name,
    )
    if prefix:
        return prefix
    return await conn.fetch(
        """SELECT id, display_name, slack_user_id FROM users
           WHERE similarity(lower(display_name), lower($1)) >= 0.3
           ORDER BY similarity(lower(display_name), lower($1)) DESC, display_name LIMIT 3""",
        name,
    )
