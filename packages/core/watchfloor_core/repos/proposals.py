"""Action proposals and single-use confirmation tokens (DD §8.1-8.2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg

from ..ids import new_id


async def supersede_pending(conn: asyncpg.Connection, *, user_id: str, deal_id: str, action_type: str) -> int:
    status = await conn.execute(
        """UPDATE action_proposals SET status = 'superseded'
           WHERE user_id = $1 AND deal_id = $2 AND action_type = $3 AND status = 'pending'""",
        user_id,
        deal_id,
        action_type,
    )
    return int(status.split()[-1])


async def create(
    conn: asyncpg.Connection,
    *,
    workspace_id: str,
    user_id: str,
    deal_id: str,
    action_type: str,
    params: dict[str, Any],
    params_hash: bytes,
    token_hash: bytes,
    preview: dict[str, Any],
    expires_at: datetime,
    at: datetime,
) -> str:
    pid = new_id("pr", at)
    await conn.execute(
        """INSERT INTO action_proposals (id, workspace_id, user_id, deal_id, action_type, params, params_hash,
                                         token_hash, preview, expires_at, created_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)""",
        pid,
        workspace_id,
        user_id,
        deal_id,
        action_type,
        params,
        params_hash,
        token_hash,
        preview,
        expires_at,
        at,
    )
    return pid


async def claim(
    conn: asyncpg.Connection, *, token_hash: bytes, user_id: str, now: datetime
) -> asyncpg.Record | None:
    """Atomically move a pending, unexpired proposal owned by this user to executed."""
    return await conn.fetchrow(
        """UPDATE action_proposals
           SET status = 'executed', executed_at = $3
           WHERE token_hash = $1 AND user_id = $2 AND status = 'pending' AND expires_at > $3
           RETURNING id, workspace_id, user_id, deal_id, action_type, params, params_hash, preview""",
        token_hash,
        user_id,
        now,
    )


async def get_by_token_hash(conn: asyncpg.Connection, token_hash: bytes) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """SELECT id, user_id, deal_id, action_type, status, result, expires_at, created_at, executed_at
           FROM action_proposals WHERE token_hash = $1""",
        token_hash,
    )


async def extend(
    conn: asyncpg.Connection, *, token_hash: bytes, user_id: str, now: datetime, until: datetime
) -> datetime | None:
    """Push a pending, unexpired proposal's expiry out to ``until``; None if it can't be extended."""
    return await conn.fetchval(
        """UPDATE action_proposals SET expires_at = $4
           WHERE token_hash = $1 AND user_id = $2 AND status = 'pending' AND expires_at > $3 AND $4 > expires_at
           RETURNING expires_at""",
        token_hash,
        user_id,
        now,
        until,
    )


async def claim_undo(
    conn: asyncpg.Connection, *, token_hash: bytes, user_id: str, now: datetime, executed_after: datetime
) -> asyncpg.Record | None:
    """Atomically move this user's executed proposal to undone, if it ran after ``executed_after``."""
    return await conn.fetchrow(
        """UPDATE action_proposals SET status = 'undone', undone_at = $3
           WHERE token_hash = $1 AND user_id = $2 AND status = 'executed' AND executed_at > $4
           RETURNING id, deal_id, action_type, params, params_hash, preview, result, executed_at""",
        token_hash,
        user_id,
        now,
        executed_after,
    )


async def mark_failed(conn: asyncpg.Connection, proposal_id: str) -> None:
    await conn.execute("UPDATE action_proposals SET status = 'failed' WHERE id = $1", proposal_id)


async def set_result(conn: asyncpg.Connection, proposal_id: str, result: dict[str, Any]) -> None:
    await conn.execute("UPDATE action_proposals SET result = $2 WHERE id = $1", proposal_id, result)


async def expire_due(conn: asyncpg.Connection, now: datetime) -> int:
    status = await conn.execute(
        "UPDATE action_proposals SET status = 'expired' WHERE status = 'pending' AND expires_at <= $1", now
    )
    return int(status.split()[-1])


async def outcomes_for_deal(conn: asyncpg.Connection, deal_id: str, limit: int = 10) -> list[asyncpg.Record]:
    return await conn.fetch(
        """SELECT action_type, status, preview, created_at FROM action_proposals
           WHERE deal_id = $1 ORDER BY created_at DESC LIMIT $2""",
        deal_id,
        limit,
    )
