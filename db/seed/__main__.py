"""Seed (or reset) the synthetic workspace.

    python -m db.seed --workspace ws_lumenridge --seed 42

Writes every row relative to "now", replays the production scorer over the
past 14 days to build trends and band history, then asserts the scenario
scores (Kestrel 71, Harbor & Pine 66, Orbital Dental 58, Northgate 18).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg
from watchfloor_core import rigs
from watchfloor_core.db import _init_connection
from watchfloor_core.rigs_params import RigsParams, load_params
from watchfloor_core.services import scoring

from . import generator, scenarios
from .model import Deal, snapshot

DEFAULT_OWNER_URL = "postgresql://wf_owner:devpassword@127.0.0.1:55432/watchfloor"

TENANT_TABLES_DELETE_ORDER = (
    "alerts",
    "outbox",
    "audit_log",
    "tasks",
    "action_proposals",
    "triage_jobs",
    "standing_orders",
    "risk_score_daily",
    "risk_scores",
    "deal_history",
    "activities",
    "deals",
    "contacts",
    "accounts",
    "user_prefs",
    "users",
)


async def reset_workspace(conn: asyncpg.Connection, ws: str) -> None:
    for table in TENANT_TABLES_DELETE_ORDER:
        await conn.execute(f"DELETE FROM {table} WHERE workspace_id = $1", ws)  # noqa: S608
    await conn.execute("DELETE FROM workspaces WHERE id = $1", ws)


async def write(
    conn: asyncpg.Connection, ws: str, deals: list[Deal], closed: list[Deal], now: datetime
) -> None:
    await conn.execute(
        "INSERT INTO workspaces (id, name, timezone) VALUES ($1, $2, $3)",
        ws,
        scenarios.WORKSPACE_NAME,
        scenarios.TIMEZONE,
    )
    await conn.executemany(
        """INSERT INTO users (id, workspace_id, display_name, email, role, slack_user_id)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        [(u.id, ws, u.display_name, u.email, u.role, u.slack_user_id) for u in scenarios.USERS],
    )
    await conn.executemany(
        "INSERT INTO user_prefs (user_id, workspace_id) VALUES ($1, $2)",
        [(u.id, ws) for u in scenarios.USERS],
    )
    accounts = {d.account.id: d.account for d in [*deals, *closed]}
    await conn.executemany(
        "INSERT INTO accounts (id, workspace_id, name, segment, owner_id, created_at) VALUES ($1,$2,$3,$4,$5,$6)",
        [(a.id, ws, a.name, a.segment, a.owner_id, now - timedelta(days=200)) for a in accounts.values()],
    )
    await conn.executemany(
        """INSERT INTO contacts (id, workspace_id, account_id, name, title, is_economic_buyer)
           VALUES ($1,$2,$3,$4,$5,$6)""",
        [
            (c.id, ws, a.id, c.name, c.title, c.is_economic_buyer)
            for a in accounts.values()
            for c in a.contacts
        ],
    )
    await conn.executemany(
        """INSERT INTO deals (id, workspace_id, account_id, name, amount_cents, stage, close_date, owner_id,
                              next_step, next_step_date, economic_buyer_id, decision_process, created_at, updated_at)
           VALUES ($1,$2,$3,$4,$5,$6::deal_stage,$7,$8,$9,$10,$11,$12,$13,$13)""",
        [
            (
                d.id,
                ws,
                d.account.id,
                d.name,
                d.amount_cents,
                d.stage,
                d.close_date,
                d.owner_id,
                d.next_step,
                d.next_step_date,
                d.economic_buyer_id,
                d.decision_process,
                d.created_at,
            )
            for d in [*deals, *closed]
        ],
    )
    await conn.executemany(
        """INSERT INTO activities (id, workspace_id, deal_id, contact_id, type, direction, occurred_at, summary, body)
           VALUES ($1,$2,$3,$4,$5::act_type,$6::act_dir,$7,$8,$9)""",
        [
            (a.id, ws, d.id, a.contact_id, a.type, a.direction, a.occurred_at, a.summary, a.body)
            for d in deals
            for a in d.activities
        ],
    )
    await conn.executemany(
        """INSERT INTO deal_history (id, workspace_id, deal_id, field, old_value, new_value, changed_by, changed_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
        [
            (h.id, ws, d.id, h.field, h.old_value, h.new_value, h.changed_by, h.changed_at)
            for d in deals
            for h in d.history
        ],
    )


async def seed(
    url: str, *, workspace: str, seed_value: int, now: datetime, verbose: bool = True
) -> dict[str, int]:
    params = load_params()
    tz = ZoneInfo(scenarios.TIMEZONE)
    today = now.astimezone(tz).date()
    rng = random.Random(seed_value)
    demo = scenarios.build(now, today, rng)
    background, closed = generator.build(now, today, rng, params)
    deals = [*demo, *background]

    conn = await asyncpg.connect(url)
    await _init_connection(conn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.workspace_id', $1, true)", workspace)
            await reset_workspace(conn, workspace)
            await write(conn, workspace, deals, closed, now)
            # Replay the production scorer day by day: trends and band_since are computed, not invented.
            for k in range(13, -1, -1):
                t = now - timedelta(days=k)
                t_today = t.astimezone(tz).date()
                await scoring.recompute(conn, workspace_id=workspace, now=t, today=t_today, params=params)
                await scoring.daily_snapshot(conn, today=t_today)
            await conn.execute(
                "UPDATE users SET last_briefing_at = $2 WHERE id = $1",
                scenarios.PRIYA,
                now - timedelta(days=1),
            )
            stored = {
                r["deal_id"]: (r["score"], r["band"])
                for r in await conn.fetch("SELECT deal_id, score, band::text AS band FROM risk_scores")
            }
            _assert_scores(deals, stored, now, today, params)
            board = [
                r["id"]
                for r in await conn.fetch(
                    """SELECT d.id FROM risk_scores rs JOIN deals d ON d.id = rs.deal_id
                       WHERE rs.band IN ('at_risk','watch')
                       ORDER BY (rs.band = 'at_risk') DESC, rs.score::bigint * d.amount_cents DESC, d.id LIMIT 3"""
                )
            ]
            if tuple(board) != scenarios.DEMO_BOARD:
                raise AssertionError(f"demo board drifted: {board}")
    finally:
        await conn.close()

    bands: dict[str, int] = {"healthy": 0, "watch": 0, "at_risk": 0}
    for _, band in stored.values():
        bands[band] += 1
    if verbose:
        print(
            f"seeded {workspace}: {len(deals)} open deals, {len(closed)} closed, "
            f"{sum(len(d.activities) for d in deals)} activities; bands {bands}"
        )
        for did, (score, band) in scenarios.EXPECTED.items():
            print(f"  {did}: {score} {band}")
    return bands


def _assert_scores(
    deals: list[Deal], stored: dict[str, tuple[int, str]], now: datetime, today: date, params: RigsParams
) -> None:
    for d in deals:
        expected = rigs.score(snapshot(d, now, today), params).score
        if stored[d.id][0] != expected:
            raise AssertionError(f"{d.id}: database path scored {stored[d.id][0]}, in-memory {expected}")
    for did, want in scenarios.EXPECTED.items():
        if stored[did] != want:
            raise AssertionError(f"scenario {did} expected {want}, got {stored[did]}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=os.environ.get("WF_OWNER_DATABASE_URL", DEFAULT_OWNER_URL))
    ap.add_argument("--workspace", default=scenarios.WORKSPACE_ID)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--now", help="ISO timestamp to seed relative to (default: current time)")
    args = ap.parse_args(argv)
    from watchfloor_core.clock import Clock

    now = datetime.fromisoformat(args.now) if args.now else Clock().now()
    asyncio.run(seed(args.url, workspace=args.workspace, seed_value=args.seed, now=now))
    return 0


if __name__ == "__main__":
    sys.exit(main())
