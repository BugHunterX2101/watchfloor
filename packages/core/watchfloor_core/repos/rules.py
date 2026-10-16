"""Standing orders and the alerts they fire (DD §9)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg

from ..ids import new_id


async def create(
    conn: asyncpg.Connection,
    *,
    workspace_id: str,
    user_id: str,
    rule: dict[str, Any],
    description: str,
    at: datetime,
) -> str:
    rid = new_id("rule", at)
    await conn.execute(
        """INSERT INTO standing_orders (id, workspace_id, user_id, rule, description, created_at)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        rid,
        workspace_id,
        user_id,
        rule,
        description,
        at,
    )
    return rid


async def find_active(conn: asyncpg.Connection, user_id: str, rule: dict[str, Any]) -> asyncpg.Record | None:
    """The user's active standing order with exactly this rule (jsonb equality ignores key order)."""
    return await conn.fetchrow(
        """SELECT id, description FROM standing_orders
           WHERE user_id = $1 AND active AND rule = $2 ORDER BY created_at LIMIT 1""",
        user_id,
        rule,
    )


async def count_active(conn: asyncpg.Connection, user_id: str) -> int:
    return await conn.fetchval("SELECT count(*) FROM standing_orders WHERE user_id = $1 AND active", user_id)


async def list_active(conn: asyncpg.Connection, user_id: str) -> list[asyncpg.Record]:
    return await conn.fetch(
        """SELECT id, description, rule, created_at, last_triggered_at FROM standing_orders
           WHERE user_id = $1 AND active ORDER BY created_at""",
        user_id,
    )


async def all_active(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    return await conn.fetch(
        """SELECT so.id, so.user_id, so.rule, so.description, so.workspace_id
           FROM standing_orders so WHERE so.active ORDER BY so.created_at"""
    )


async def deactivate(
    conn: asyncpg.Connection, user_id: str, rule_id: str, at: datetime
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """UPDATE standing_orders SET active = false, removed_at = $3
           WHERE id = $2 AND user_id = $1 AND active RETURNING id, description""",
        user_id,
        rule_id,
        at,
    )


async def matches(
    conn: asyncpg.Connection, *, rule_id: str, conditions: dict[str, Any], now: datetime
) -> list[str]:
    """Open deals matching every present condition, minus those alerted for this rule in the last 24 h.

    quiet_days counts days since the last inbound activity (a reply), per DD §9.1.
    """
    rows = await conn.fetch(
        """SELECT d.id
           FROM deals d
           JOIN risk_scores rs ON rs.deal_id = d.id
           JOIN accounts a     ON a.id = d.account_id
           LEFT JOIN LATERAL (
             SELECT max(occurred_at) AS last_inbound
             FROM activities
             WHERE deal_id = d.id AND direction = 'inbound' AND type <> 'doc_view' AND occurred_at <= $6
           ) li ON true
           WHERE d.stage NOT IN ('won', 'lost')
             AND ($1::bigint       IS NULL OR d.amount_cents >= $1)
             AND ($2::int          IS NULL OR COALESCE(li.last_inbound, '-infinity')
                                              < $6::timestamptz - make_interval(days => $2))
             AND ($3::deal_stage[] IS NULL OR d.stage = ANY ($3))
             AND ($4::int          IS NULL OR rs.score >= $4)
             AND ($5::text         IS NULL OR a.segment = $5)
             AND NOT EXISTS (
                   SELECT 1 FROM alerts al
                   WHERE al.rule_id = $7 AND al.deal_id = d.id
                     AND al.sent_at > $6::timestamptz - interval '24 hours')
           ORDER BY rs.score DESC, d.id""",
        conditions.get("min_amount_cents"),
        conditions.get("quiet_days"),
        conditions.get("stages"),
        conditions.get("min_score"),
        conditions.get("segment"),
        now,
        rule_id,
    )
    return [r["id"] for r in rows]


async def record_alert(
    conn: asyncpg.Connection, *, workspace_id: str, rule_id: str, deal_id: str, at: datetime
) -> str:
    aid = new_id("al", at)
    await conn.execute(
        "INSERT INTO alerts (id, workspace_id, rule_id, deal_id, sent_at) VALUES ($1, $2, $3, $4, $5)",
        aid,
        workspace_id,
        rule_id,
        deal_id,
        at,
    )
    await conn.execute("UPDATE standing_orders SET last_triggered_at = $2 WHERE id = $1", rule_id, at)
    return aid


async def fired_since(conn: asyncpg.Connection, user_id: str, since: datetime | None) -> list[asyncpg.Record]:
    return await conn.fetch(
        """SELECT so.id AS rule_id, so.description,
                  array_agg(DISTINCT al.deal_id ORDER BY al.deal_id) AS deal_ids
           FROM alerts al JOIN standing_orders so ON so.id = al.rule_id
           WHERE so.user_id = $1 AND al.sent_at > COALESCE($2::timestamptz, '-infinity')
           GROUP BY so.id, so.description, so.created_at
           ORDER BY so.created_at""",
        user_id,
        since,
    )
