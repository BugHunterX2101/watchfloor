"""Precomputed risk scores: the hot path only reads these."""

from __future__ import annotations

from datetime import date, datetime

import asyncpg

from ..models.domain import RigsResult

_UPSERT = """INSERT INTO risk_scores (deal_id, workspace_id, score, band, r, i, g, s, reasons,
                            band_since, computed_at, model_version)
   VALUES ($1, $2, $3, $4::risk_band, $5, $6, $7, $8, $9, $10, $11, $12)
   ON CONFLICT (deal_id) DO UPDATE SET
     score = EXCLUDED.score, band = EXCLUDED.band, r = EXCLUDED.r, i = EXCLUDED.i,
     g = EXCLUDED.g, s = EXCLUDED.s, reasons = EXCLUDED.reasons,
     band_since = EXCLUDED.band_since, computed_at = EXCLUDED.computed_at,
     model_version = EXCLUDED.model_version"""


async def previous(conn: asyncpg.Connection, deal_ids: list[str] | None = None) -> dict[str, asyncpg.Record]:
    rows = await conn.fetch(
        """SELECT deal_id, band::text AS band, band_since, score FROM risk_scores
           WHERE ($1::text[] IS NULL OR deal_id = ANY($1))""",
        deal_ids,
    )
    return {r["deal_id"]: r for r in rows}


async def upsert_many(conn: asyncpg.Connection, rows: list[tuple[object, ...]]) -> None:
    """Score rows (:func:`upsert_row`) in one pipelined round trip: the watcher rescores a whole workspace.

    Row order: deal_id, workspace_id, score, band, r, i, g, s, reasons, band_since, computed_at, model_version.
    """
    if rows:
        await conn.executemany(_UPSERT, rows)


def upsert_row(
    *,
    deal_id: str,
    workspace_id: str,
    result: RigsResult,
    band: str,
    band_since: datetime,
    computed_at: datetime,
) -> tuple[object, ...]:
    return (
        deal_id,
        workspace_id,
        result.score,
        band,
        round(result.r, 3),
        round(result.i, 3),
        round(result.g, 3),
        round(result.s, 3),
        [r.to_json() for r in result.reasons],
        band_since,
        computed_at,
        result.model_version,
    )


async def delete_closed(conn: asyncpg.Connection) -> int:
    status = await conn.execute(
        "DELETE FROM risk_scores rs USING deals d WHERE d.id = rs.deal_id AND d.stage IN ('won', 'lost')"
    )
    return int(status.split()[-1])


async def get(conn: asyncpg.Connection, deal_id: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """SELECT deal_id, score, band::text AS band, r, i, g, s, reasons, band_since, computed_at, model_version
           FROM risk_scores WHERE deal_id = $1""",
        deal_id,
    )


async def ranked_risks(
    conn: asyncpg.Connection, *, limit: int, segment: str | None, since: datetime | None
) -> list[asyncpg.Record]:
    """At Risk first, then score x amount (PRD US-1, DD §6.4). RLS supplies the workspace."""
    return await conn.fetch(
        """SELECT d.id AS deal_id, d.name, a.name AS account, d.amount_cents, d.stage::text AS stage,
                  u.display_name AS owner, rs.score, rs.band::text AS band,
                  rs.reasons -> 0 ->> 'text' AS top_reason,
                  rs.band_since > COALESCE($2::timestamptz, '-infinity') AS new_since_last_briefing
           FROM risk_scores rs
           JOIN deals d    ON d.id = rs.deal_id
           JOIN accounts a ON a.id = d.account_id
           JOIN users u    ON u.id = d.owner_id
           WHERE rs.band IN ('at_risk', 'watch')
             AND d.stage NOT IN ('won', 'lost')
             AND ($3::text IS NULL OR a.segment = $3)
           ORDER BY (rs.band = 'at_risk') DESC, rs.score::bigint * d.amount_cents DESC, d.id
           LIMIT $1""",
        limit,
        since,
        segment,
    )


async def attention_counts(
    conn: asyncpg.Connection, *, segment: str | None, since: datetime | None
) -> asyncpg.Record:
    return await conn.fetchrow(
        """SELECT count(*) FILTER (WHERE rs.band IN ('at_risk', 'watch')) AS attention,
                  count(*) FILTER (WHERE rs.band = 'at_risk') AS at_risk,
                  count(*) FILTER (WHERE rs.band IN ('at_risk', 'watch')
                                   AND rs.band_since > COALESCE($2::timestamptz, '-infinity')) AS new_count,
                  max(rs.computed_at) AS computed_at
           FROM risk_scores rs
           JOIN deals d    ON d.id = rs.deal_id
           JOIN accounts a ON a.id = d.account_id
           WHERE d.stage NOT IN ('won', 'lost') AND ($1::text IS NULL OR a.segment = $1)""",
        segment,
        since,
    )


async def trend(conn: asyncpg.Connection, deal_id: str, *, since_day: date) -> list[tuple[date, int]]:
    rows = await conn.fetch(
        "SELECT day, score FROM risk_score_daily WHERE deal_id = $1 AND day >= $2 ORDER BY day",
        deal_id,
        since_day,
    )
    return [(r["day"], r["score"]) for r in rows]


async def snapshot_daily(conn: asyncpg.Connection, *, day: date) -> int:
    status = await conn.execute(
        """INSERT INTO risk_score_daily (deal_id, day, workspace_id, score, band)
           SELECT rs.deal_id, $1, rs.workspace_id, rs.score, rs.band
           FROM risk_scores rs JOIN deals d ON d.id = rs.deal_id
           WHERE d.stage NOT IN ('won', 'lost')
           ON CONFLICT (deal_id, day) DO UPDATE SET score = EXCLUDED.score, band = EXCLUDED.band""",
        day,
    )
    return int(status.split()[-1])


async def has_daily(conn: asyncpg.Connection, *, day: date) -> bool:
    return await conn.fetchval("SELECT EXISTS (SELECT 1 FROM risk_score_daily WHERE day = $1)", day)


async def insert_daily(conn: asyncpg.Connection, rows: list[tuple[str, date, str, int, str]]) -> None:
    await conn.executemany(
        """INSERT INTO risk_score_daily (deal_id, day, workspace_id, score, band)
           VALUES ($1, $2, $3, $4, $5::risk_band)
           ON CONFLICT (deal_id, day) DO UPDATE SET score = EXCLUDED.score, band = EXCLUDED.band""",
        rows,
    )
