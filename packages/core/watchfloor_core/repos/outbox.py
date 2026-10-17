"""Transactional outbox (DD §8.4, ADR-008).

Rows are committed with the business write; delivery is at-least-once with a
unique dedupe key, exponential backoff and a dead state that raises an alarm.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import asyncpg

BACKOFF = (timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10), timedelta(minutes=30))
MAX_ATTEMPTS = len(BACKOFF) + 1


async def enqueue(
    conn: asyncpg.Connection,
    *,
    workspace_id: str,
    kind: str,
    payload: dict[str, Any],
    dedupe_key: str | None,
    at: datetime,
    deliver_at: datetime | None = None,
) -> int | None:
    """Returns the new row id, or None when the dedupe key already exists.

    ``deliver_at`` holds the row back until then (an undo window): no worker can claim it earlier.
    """
    return await conn.fetchval(
        """INSERT INTO outbox (workspace_id, kind, payload, dedupe_key, next_attempt_at, created_at)
           VALUES ($1, $2, $3, $4, $6, $5)
           ON CONFLICT (dedupe_key) DO NOTHING RETURNING id""",
        workspace_id,
        kind,
        payload,
        dedupe_key,
        at,
        deliver_at or at,
    )


async def cancel(conn: asyncpg.Connection, dedupe_key: str) -> bool:
    """Withdraw a row no worker has tried yet (an undone action's Slack post). False if it is too late."""
    status = await conn.execute(
        "UPDATE outbox SET status = 'cancelled' WHERE dedupe_key = $1 AND status = 'pending' AND attempts = 0",
        dedupe_key,
    )
    return status.endswith(" 1")


async def due_ids(conn: asyncpg.Connection, before: datetime, limit: int = 100) -> list[int]:
    """Pending rows whose next attempt is at or before ``before`` (sweeper, watcher, local poller)."""
    rows = await conn.fetch(
        """SELECT id FROM outbox WHERE status = 'pending' AND next_attempt_at <= $1
           ORDER BY next_attempt_at, id LIMIT $2""",
        before,
        limit,
    )
    return [r["id"] for r in rows]


async def abandon_exhausted(conn: asyncpg.Connection, outbox_id: int, *, now: datetime) -> str | None:
    """Mark dead a due row that has already used every attempt without ever reporting back.

    :func:`record_failure` is what ends the last attempt, so a row still pending with
    ``MAX_ATTEMPTS`` claims behind it lost its worker each time (a timeout, an out-of-memory kill).
    Without this the sweeper would hand it back for ever, one more agent run or model call per
    lease. Returns the row's kind when it was abandoned, else None.
    """
    return await conn.fetchval(
        """UPDATE outbox SET status = 'dead', last_error = $3
           WHERE id = $1 AND status = 'pending' AND next_attempt_at <= $2 AND attempts >= $4
           RETURNING kind""",
        outbox_id,
        now,
        f"abandoned: {MAX_ATTEMPTS} attempts ended without a result (the worker died)",
        MAX_ATTEMPTS,
    )


async def claim(
    conn: asyncpg.Connection, outbox_id: int, *, now: datetime, lease: timedelta
) -> asyncpg.Record | None:
    """Take a due row for one delivery attempt; None if it is sent, dead, leased or backing off.

    The lease pushes ``next_attempt_at`` forward and commits at once, so slow work
    (Slack, Bedrock, a 90 s agent run) holds no lock and duplicate messages skip
    the row. A worker that dies mid-attempt simply lets the lease lapse.
    """
    return await conn.fetchrow(
        """UPDATE outbox SET attempts = attempts + 1, next_attempt_at = $2::timestamptz + $3::interval
           WHERE id = $1 AND status = 'pending' AND next_attempt_at <= $2
           RETURNING id, workspace_id, kind, payload, attempts""",
        outbox_id,
        now,
        lease,
    )


async def get(conn: asyncpg.Connection, outbox_id: int) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """SELECT id, workspace_id, kind, payload, status, attempts, next_attempt_at, last_error
           FROM outbox WHERE id = $1""",
        outbox_id,
    )


async def mark_sent(conn: asyncpg.Connection, outbox_id: int, at: datetime) -> None:
    await conn.execute(
        "UPDATE outbox SET status = 'sent', sent_at = $2, last_error = NULL WHERE id = $1", outbox_id, at
    )


async def mark_dead(conn: asyncpg.Connection, outbox_id: int, error: str) -> None:
    await conn.execute(
        "UPDATE outbox SET status = 'dead', last_error = $2 WHERE id = $1", outbox_id, error[:500]
    )


async def record_failure(
    conn: asyncpg.Connection,
    outbox_id: int,
    *,
    attempts: int,
    error: str,
    now: datetime,
    retry_after: timedelta | None = None,
) -> str:
    """After failed attempt number ``attempts`` (counted by :func:`claim`): back off, or go dead.

    Attempts 1-4 wait 30 s, 2 min, 10 min, 30 min (or the server's Retry-After
    when longer); a fifth failure marks the row dead, which raises an alarm.
    """
    if attempts >= MAX_ATTEMPTS:
        await mark_dead(conn, outbox_id, error)
        return "dead"
    wait = max(BACKOFF[attempts - 1], retry_after or timedelta(0))
    await conn.execute(
        "UPDATE outbox SET last_error = $2, next_attempt_at = $3 WHERE id = $1",
        outbox_id,
        error[:500],
        now + wait,
    )
    return "retry"


async def set_payload(conn: asyncpg.Connection, outbox_id: int, payload: dict[str, Any]) -> None:
    """Keep expensive intermediate results (a generated draft) so a retry only redoes delivery."""
    await conn.execute("UPDATE outbox SET payload = $2 WHERE id = $1", outbox_id, payload)


async def counts(conn: asyncpg.Connection) -> asyncpg.Record:
    return await conn.fetchrow(
        """SELECT count(*) FILTER (WHERE status = 'pending') AS pending,
                  count(*) FILTER (WHERE status = 'dead') AS dead FROM outbox"""
    )


async def rearm(conn: asyncpg.Connection, outbox_id: int, now: datetime) -> bool:
    status = await conn.execute(
        "UPDATE outbox SET status = 'pending', attempts = 0, next_attempt_at = $2 WHERE id = $1 AND status = 'dead'",
        outbox_id,
        now,
    )
    return status.endswith(" 1")
