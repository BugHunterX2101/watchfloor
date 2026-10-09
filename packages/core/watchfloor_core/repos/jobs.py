"""Deep-triage jobs (DD §10.1)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import asyncpg

from ..ids import new_id

JOB_COLUMNS = (
    "id, workspace_id, user_id, deal_id, status, trace_id, result, error, created_at, started_at, finished_at"
)


async def find_reusable(
    conn: asyncpg.Connection, deal_id: str, now: datetime, window: timedelta
) -> asyncpg.Record | None:
    """A queued/running job, or one finished within the dedupe window, for the same deal."""
    return await conn.fetchrow(
        f"""SELECT {JOB_COLUMNS} FROM triage_jobs
            WHERE deal_id = $1 AND (status IN ('queued', 'running')
                                    OR (status = 'done' AND finished_at > $2))
            ORDER BY created_at DESC LIMIT 1""",  # noqa: S608
        deal_id,
        now - window,
    )


async def count_active(conn: asyncpg.Connection) -> int:
    return await conn.fetchval("SELECT count(*) FROM triage_jobs WHERE status IN ('queued', 'running')")


async def create(
    conn: asyncpg.Connection,
    *,
    workspace_id: str,
    user_id: str,
    deal_id: str,
    trace_id: str | None,
    at: datetime,
) -> str:
    jid = new_id("job", at)
    await conn.execute(
        """INSERT INTO triage_jobs (id, workspace_id, user_id, deal_id, status, trace_id, created_at)
           VALUES ($1, $2, $3, $4, 'queued', $5, $6)""",
        jid,
        workspace_id,
        user_id,
        deal_id,
        trace_id,
        at,
    )
    return jid


async def get(conn: asyncpg.Connection, job_id: str) -> asyncpg.Record | None:
    return await conn.fetchrow(f"SELECT {JOB_COLUMNS} FROM triage_jobs WHERE id = $1", job_id)  # noqa: S608


async def latest_for_deal(conn: asyncpg.Connection, deal_id: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        f"SELECT {JOB_COLUMNS} FROM triage_jobs WHERE deal_id = $1 ORDER BY created_at DESC LIMIT 1",  # noqa: S608
        deal_id,
    )


async def mark_running(conn: asyncpg.Connection, job_id: str, at: datetime) -> asyncpg.Record | None:
    """Idempotent: returns None if the job is already terminal."""
    return await conn.fetchrow(
        f"""UPDATE triage_jobs SET status = 'running', started_at = COALESCE(started_at, $2)
            WHERE id = $1 AND status IN ('queued', 'running') RETURNING {JOB_COLUMNS}""",  # noqa: S608
        job_id,
        at,
    )


async def complete(conn: asyncpg.Connection, job_id: str, result: dict[str, Any], at: datetime) -> bool:
    status = await conn.execute(
        """UPDATE triage_jobs SET status = 'done', result = $2, finished_at = $3, error = NULL
           WHERE id = $1 AND status IN ('queued', 'running')""",
        job_id,
        result,
        at,
    )
    return status.endswith(" 1")


async def fail(
    conn: asyncpg.Connection, job_id: str, error: str, at: datetime, *, timed_out: bool = False
) -> None:
    await conn.execute(
        """UPDATE triage_jobs SET status = $2, error = $3, finished_at = $4
           WHERE id = $1 AND status IN ('queued', 'running')""",
        job_id,
        "timed_out" if timed_out else "failed",
        error[:500],
        at,
    )


async def time_out_stale(conn: asyncpg.Connection, *, created_before: datetime, at: datetime) -> int:
    """Jobs still queued or running past the deadline end as timed_out (sweeper)."""
    status = await conn.execute(
        """UPDATE triage_jobs SET status = 'timed_out', finished_at = $2,
                  error = COALESCE(error, 'no result before the deadline')
           WHERE status IN ('queued', 'running') AND created_at < $1""",
        created_before,
        at,
    )
    return int(status.split()[-1])


async def prior(conn: asyncpg.Connection, deal_id: str, limit: int) -> list[asyncpg.Record]:
    return await conn.fetch(
        f"""SELECT {JOB_COLUMNS} FROM triage_jobs
            WHERE deal_id = $1 AND status = 'done' ORDER BY finished_at DESC LIMIT $2""",  # noqa: S608
        deal_id,
        limit,
    )
