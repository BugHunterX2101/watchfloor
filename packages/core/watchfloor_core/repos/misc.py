"""Small aggregates: tasks, audit log, rate limits, activities, contacts and purges."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import asyncpg

from ..ids import new_id


# -- tasks (DD delta D8: Watchfloor's own table is the v1 system of record)
async def create_task(
    conn: asyncpg.Connection,
    *,
    workspace_id: str,
    deal_id: str,
    assignee_id: str,
    title: str,
    due_date: date | None,
    created_by: str,
    source_proposal_id: str,
    at: datetime,
) -> str:
    tid = new_id("tk", at)
    await conn.execute(
        """INSERT INTO tasks (id, workspace_id, deal_id, assignee_id, title, due_date, created_by,
                              source_proposal_id, created_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
        tid,
        workspace_id,
        deal_id,
        assignee_id,
        title,
        due_date,
        created_by,
        source_proposal_id,
        at,
    )
    return tid


async def cancel_tasks_from_proposal(conn: asyncpg.Connection, proposal_id: str) -> int:
    status = await conn.execute(
        "UPDATE tasks SET status = 'cancelled' WHERE source_proposal_id = $1 AND status = 'open'", proposal_id
    )
    return int(status.split()[-1])


# -- audit log (one row per tools/call; argument values are hashed, never stored)
async def record_audit(
    conn: asyncpg.Connection,
    *,
    workspace_id: str,
    user_id: str | None,
    tool: str,
    args_hash: bytes,
    outcome: str,
    error_code: str | None,
    latency_ms: int | None,
    trace_id: str | None,
    at: datetime,
) -> int:
    return await conn.fetchval(
        """INSERT INTO audit_log (workspace_id, user_id, tool, args_hash, outcome, error_code, latency_ms, trace_id, at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING id""",
        workspace_id,
        user_id,
        tool,
        args_hash,
        outcome,
        error_code,
        latency_ms,
        trace_id,
        at,
    )


# -- rate limits (fixed one-minute windows; table has no workspace_id, so no RLS)
async def rate_hit(conn: asyncpg.Connection, subject: str) -> int:
    return await conn.fetchval(
        """INSERT INTO rate_limits (subject, window_start, count)
           VALUES ($1, date_trunc('minute', now()), 1)
           ON CONFLICT (subject, window_start) DO UPDATE SET count = rate_limits.count + 1
           RETURNING count""",
        subject,
    )


async def purge_rate_limits(conn: asyncpg.Connection) -> None:
    await conn.execute("DELETE FROM rate_limits WHERE window_start < now() - interval '10 minutes'")


# -- activities
async def insert_activity(
    conn: asyncpg.Connection,
    *,
    workspace_id: str,
    deal_id: str,
    type_: str,
    direction: str,
    occurred_at: datetime,
    summary: str,
    body: str | None = None,
    contact_id: str | None = None,
) -> str:
    aid = new_id("act", occurred_at)
    await conn.execute(
        """INSERT INTO activities (id, workspace_id, deal_id, contact_id, type, direction, occurred_at, summary, body)
           VALUES ($1, $2, $3, $4, $5::act_type, $6::act_dir, $7, $8, $9)""",
        aid,
        workspace_id,
        deal_id,
        contact_id,
        type_,
        direction,
        occurred_at,
        summary,
        body,
    )
    return aid


async def timeline(
    conn: asyncpg.Connection, deal_id: str, *, since: datetime, limit: int = 200
) -> list[asyncpg.Record]:
    return await conn.fetch(
        """SELECT id, type::text AS type, direction::text AS direction, occurred_at, summary, body
           FROM activities WHERE deal_id = $1 AND occurred_at >= $2
           ORDER BY occurred_at DESC LIMIT $3""",
        deal_id,
        since,
        limit,
    )


async def evidence_ids(conn: asyncpg.Connection, deal_id: str) -> set[str]:
    """Every id an agent may cite for a deal: its activities and field-history rows."""
    rows = await conn.fetch(
        "SELECT id FROM activities WHERE deal_id = $1 UNION ALL SELECT id FROM deal_history WHERE deal_id = $1",
        deal_id,
    )
    return {r["id"] for r in rows}


async def history(conn: asyncpg.Connection, deal_id: str, limit: int = 100) -> list[asyncpg.Record]:
    return await conn.fetch(
        """SELECT id, field, old_value, new_value, changed_at FROM deal_history
           WHERE deal_id = $1 ORDER BY changed_at DESC LIMIT $2""",
        deal_id,
        limit,
    )


async def account(conn: asyncpg.Connection, account_id: str) -> asyncpg.Record | None:
    return await conn.fetchrow("SELECT id, name, segment FROM accounts WHERE id = $1", account_id)


async def contacts(conn: asyncpg.Connection, account_id: str) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT id, name, title, is_economic_buyer FROM contacts WHERE account_id = $1 ORDER BY name",
        account_id,
    )


# -- retention (DD §13.4)
async def purge_old(conn: asyncpg.Connection, now: datetime) -> None:
    await conn.execute("DELETE FROM audit_log WHERE at < $1", now - timedelta(days=30))
    await conn.execute(
        "DELETE FROM outbox WHERE status IN ('sent', 'cancelled') AND created_at < $1",
        now - timedelta(days=30),
    )
    await conn.execute("DELETE FROM risk_score_daily WHERE day < $1", (now - timedelta(days=180)).date())
