"""Deals, and set-based loading of RIGS snapshots."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

import asyncpg

from ..ids import new_id
from ..models.domain import DealSnapshot, Engagement, FieldChange

TWO_WAY_SQL = (
    "((type = 'email' AND direction = 'inbound') "
    "OR (type = 'call' AND direction = 'inbound') OR type = 'meeting')"
)
TRACKED_FIELDS = (
    "close_date",
    "stage",
    "amount_cents",
    "owner_id",
    "next_step",
    "next_step_date",
    "decision_process",
)


async def load_snapshots(
    conn: asyncpg.Connection,
    *,
    as_of: datetime,
    today: date,
    deal_ids: list[str] | None = None,
    window_days: int = 14,
    history_days: int = 90,
) -> dict[str, DealSnapshot]:
    """Build RIGS inputs for open deals in four set-based queries (no N+1)."""
    deals = await conn.fetch(
        """SELECT id, stage::text AS stage, amount_cents, economic_buyer_id, next_step,
                  next_step_date, decision_process
           FROM deals
           WHERE stage NOT IN ('won', 'lost') AND ($1::text[] IS NULL OR id = ANY($1))""",
        deal_ids,
    )
    if not deals:
        return {}
    last = {
        r["deal_id"]: r
        for r in await conn.fetch(
            f"""SELECT DISTINCT ON (deal_id) deal_id, id, occurred_at
                FROM activities
                WHERE occurred_at <= $1 AND {TWO_WAY_SQL} AND ($2::text[] IS NULL OR deal_id = ANY($2))
                ORDER BY deal_id, occurred_at DESC""",  # noqa: S608 - TWO_WAY_SQL is a constant
            as_of,
            deal_ids,
        )
    }
    eng = {
        r["deal_id"]: r
        for r in await conn.fetch(
            f"""SELECT deal_id,
                  count(*) FILTER (WHERE type = 'email' AND direction = 'inbound') AS replies,
                  count(*) FILTER (WHERE type = 'meeting')                         AS meetings,
                  count(*) FILTER (WHERE type = 'call' AND direction = 'inbound')  AS calls,
                  count(*) FILTER (WHERE type = 'doc_view')                        AS views,
                  array_agg(id ORDER BY occurred_at DESC) FILTER (WHERE {TWO_WAY_SQL}) AS two_way_ids,
                  array_agg(id ORDER BY occurred_at DESC) FILTER (WHERE type = 'doc_view') AS view_ids
                FROM activities
                WHERE occurred_at > $1::timestamptz - make_interval(days => $3) AND occurred_at <= $1::timestamptz
                  AND ($2::text[] IS NULL OR deal_id = ANY($2))
                GROUP BY deal_id""",  # noqa: S608 - TWO_WAY_SQL is a constant
            as_of,
            deal_ids,
            window_days,
        )
    }
    history: dict[str, list[FieldChange]] = {}
    for r in await conn.fetch(
        """SELECT id, deal_id, field, old_value, new_value, changed_at
           FROM deal_history
           WHERE field IN ('close_date', 'stage', 'amount_cents')
             AND changed_at > $1::timestamptz - make_interval(days => $3) AND changed_at <= $1::timestamptz
             AND ($2::text[] IS NULL OR deal_id = ANY($2))
           ORDER BY changed_at, id""",
        as_of,
        deal_ids,
        history_days,
    ):
        change = _field_change(r)
        if change is not None:
            history.setdefault(r["deal_id"], []).append(change)

    out: dict[str, DealSnapshot] = {}
    for d in deals:
        did = d["id"]
        lt = last.get(did)
        e = eng.get(did)
        engagement = (
            Engagement(
                replies=e["replies"],
                meetings=e["meetings"],
                inbound_calls=e["calls"],
                views=e["views"],
                activity_ids=tuple(e["two_way_ids"] or ()),
                view_ids=tuple(e["view_ids"] or ()),
            )
            if e
            else Engagement()
        )
        out[did] = DealSnapshot(
            deal_id=did,
            stage=d["stage"],
            amount_cents=d["amount_cents"],
            economic_buyer_id=d["economic_buyer_id"],
            next_step=d["next_step"],
            next_step_date=d["next_step_date"],
            decision_process=d["decision_process"],
            last_two_way_at=lt["occurred_at"] if lt else None,
            last_two_way_id=lt["id"] if lt else None,
            engagement_14d=engagement,
            history_90d=tuple(history.get(did, ())),
            now=as_of,
            today=today,
        )
    return out


def _field_change(r: asyncpg.Record) -> FieldChange | None:
    old, new = r["old_value"], r["new_value"]
    if old is None or new is None:
        return None
    field = r["field"]
    try:
        if field == "close_date":
            return FieldChange(
                r["id"], field, date.fromisoformat(old), date.fromisoformat(new), r["changed_at"]
            )
        if field == "amount_cents":
            return FieldChange(r["id"], field, int(old), int(new), r["changed_at"])
        return FieldChange(r["id"], field, old, new, r["changed_at"])
    except ValueError:
        return None


async def get_card(conn: asyncpg.Connection, deal_id: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """SELECT d.id, d.name, d.amount_cents, d.stage::text AS stage, d.close_date, d.next_step,
                  d.next_step_date, d.owner_id, d.account_id, d.economic_buyer_id, d.decision_process,
                  a.name AS account, a.segment, u.display_name AS owner
           FROM deals d
           JOIN accounts a ON a.id = d.account_id
           JOIN users u    ON u.id = d.owner_id
           WHERE d.id = $1""",
        deal_id,
    )


async def lock(conn: asyncpg.Connection, deal_id: str) -> asyncpg.Record | None:
    return await conn.fetchrow("SELECT id, stage::text AS stage FROM deals WHERE id = $1 FOR UPDATE", deal_id)


def like_escape(text: str) -> str:
    """User words as a literal LIKE pattern (ESCAPE '!'): "50%" must not match every name."""
    return text.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def spoken_name(text: str) -> str:
    """Names as speech gives them: "Harbor & Pine" arrives as "Harbor and Pine"."""
    return " ".join(re.sub(r"\s*&\s*", " and ", text.lower()).split())


def clear_winner(rows: list[Any]) -> list[Any]:
    """An exact name beats weaker matches outright: "Harbor & Pine" is one deal, not a question
    about the Pinecrest deals too. Two deals at the same account still tie, and still ask."""
    if (
        len(rows) > 1
        and rows[0]["match_score"] >= 0.9
        and rows[1]["match_score"] < rows[0]["match_score"] - 0.25
    ):
        return rows[:1]
    return rows


async def find_by_name(
    conn: asyncpg.Connection, query: str, *, owner: str | None = None, limit: int = 3, threshold: float = 0.3
) -> list[asyncpg.Record]:
    """Fuzzy match on account and deal names (pg_trgm word similarity), open deals only. "&" and
    "and" match each other on both sides, as in `spoken_name`."""
    query = spoken_name(query)
    return await conn.fetch(
        """SELECT * FROM (
             SELECT d.id AS deal_id, d.name, a.name AS account, d.amount_cents, d.stage::text AS stage,
                    d.close_date, d.next_step, u.display_name AS owner,
                    greatest(word_similarity($1, regexp_replace(lower(a.name), '\\s*&\\s*', ' and ', 'g')),
                             word_similarity($1, regexp_replace(lower(d.name), '\\s*&\\s*', ' and ', 'g')),
                             CASE WHEN regexp_replace(lower(a.name), '\\s*&\\s*', ' and ', 'g')
                                       LIKE $5::text || '%' ESCAPE '!' THEN 1.0 ELSE 0 END
                    )::float8 AS match_score
             FROM deals d
             JOIN accounts a ON a.id = d.account_id
             JOIN users u    ON u.id = d.owner_id
             WHERE d.stage NOT IN ('won', 'lost')
               AND ($2::text IS NULL OR u.display_name ILIKE $2 || '%' ESCAPE '!'
                    OR u.display_name ILIKE '% ' || $2 || '%' ESCAPE '!')
           ) m
           WHERE match_score >= $3
           ORDER BY match_score DESC, amount_cents DESC, deal_id
           LIMIT $4""",
        query,
        like_escape(owner) if owner else None,
        threshold,
        limit,
        like_escape(query),
    )


async def apply_fields(
    conn: asyncpg.Connection,
    deal_id: str,
    changes: dict[str, Any],
    *,
    changed_by: str,
    at: datetime,
) -> list[str]:
    """Update deal fields and record each tracked change in deal_history. Returns history ids."""
    cols = ", ".join(f"{k}::text AS {k}" if k == "stage" else k for k in TRACKED_FIELDS)
    current = await conn.fetchrow(f"SELECT workspace_id, {cols} FROM deals WHERE id = $1", deal_id)  # noqa: S608
    if current is None:
        raise LookupError(deal_id)
    hist_ids: list[str] = []
    sets: list[str] = []
    args: list[Any] = [deal_id]
    for field, value in changes.items():
        if field not in TRACKED_FIELDS:
            raise ValueError(f"untracked field {field}")
        old = current[field]
        if old == value:
            continue
        args.append(value)
        cast = "::deal_stage" if field == "stage" else ""
        sets.append(f"{field} = ${len(args)}{cast}")
        hid = new_id("hist", at)
        hist_ids.append(hid)
        await conn.execute(
            """INSERT INTO deal_history (id, workspace_id, deal_id, field, old_value, new_value, changed_by, changed_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            hid,
            current["workspace_id"],
            deal_id,
            field,
            None if old is None else str(old),
            None if value is None else str(value),
            changed_by,
            at,
        )
    if sets:
        args.append(at)
        await conn.execute(
            f"UPDATE deals SET {', '.join(sets)}, updated_at = ${len(args)} WHERE id = $1",  # noqa: S608
            *args,
        )
    return hist_ids


async def revert_fields(
    conn: asyncpg.Connection, deal_id: str, values: dict[str, Any], *, history_ids: list[str]
) -> None:
    """Undo an apply_fields call: restore the old values and drop the history rows it wrote
    (``history_ids``, as it returned them), so an undone close-date push is not scored as a push."""
    sets: list[str] = []
    args: list[Any] = [deal_id]
    for field, value in values.items():
        if field not in TRACKED_FIELDS or field == "stage":
            raise ValueError(f"cannot revert field {field}")
        args.append(value)
        sets.append(f"{field} = ${len(args)}")
    await conn.execute(f"UPDATE deals SET {', '.join(sets)} WHERE id = $1", *args)  # noqa: S608
    await conn.execute(
        "DELETE FROM deal_history WHERE deal_id = $1 AND id = ANY($2::text[])", deal_id, history_ids
    )


async def account_deals(conn: asyncpg.Connection, account_id: str) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT id, name, stage::text AS stage, amount_cents FROM deals WHERE account_id = $1 ORDER BY created_at",
        account_id,
    )


async def open_deal_count_for_owner(conn: asyncpg.Connection, owner_id: str) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM deals WHERE owner_id = $1 AND stage NOT IN ('won', 'lost')", owner_id
    )
