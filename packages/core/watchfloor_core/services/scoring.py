"""Recompute and persist RIGS scores (DD §7.6).

Bands carry hysteresis, so each recompute reads the previous band. ``band_since``
changes only when the band changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

import asyncpg

from .. import rigs
from ..models.domain import RigsResult
from ..repos import deals, scores
from ..rigs_params import RigsParams, load_params


@dataclass(frozen=True)
class ScoreChange:
    deal_id: str
    score_before: int | None
    score_after: int
    band_before: str | None
    band_after: str
    result: RigsResult


async def recompute(
    conn: asyncpg.Connection,
    *,
    workspace_id: str,
    now: datetime,
    today: date,
    deal_ids: list[str] | None = None,
    params: RigsParams | None = None,
) -> dict[str, ScoreChange]:
    p = params or load_params()
    snaps = await deals.load_snapshots(
        conn,
        as_of=now,
        today=today,
        deal_ids=deal_ids,
        window_days=p.engagement_window_days,
        history_days=p.slippage.window_days,
    )
    prev = await scores.previous(conn, deal_ids)
    changes: dict[str, ScoreChange] = {}
    rows: list[tuple[object, ...]] = []
    for did, snap in snaps.items():
        result = rigs.score(snap, p)
        before = prev.get(did)
        band_before = before["band"] if before else None
        band = rigs.band_for(result.score, band_before, p.bands)  # type: ignore[arg-type]
        band_since = before["band_since"] if before and band == band_before else now
        rows.append(
            scores.upsert_row(
                deal_id=did,
                workspace_id=workspace_id,
                result=result,
                band=band,
                band_since=band_since,
                computed_at=now,
            )
        )
        changes[did] = ScoreChange(
            deal_id=did,
            score_before=before["score"] if before else None,
            score_after=result.score,
            band_before=band_before,
            band_after=band,
            result=result,
        )
    # One pipelined round trip for the whole batch: the watcher rescores every open deal each run.
    await scores.upsert_many(conn, rows)
    if deal_ids is None:
        await scores.delete_closed(conn)
    return changes


async def daily_snapshot(conn: asyncpg.Connection, *, today: date) -> int:
    return await scores.snapshot_daily(conn, day=today)


SNAPSHOT_AT = time(0, 5)  # 00:05 workspace time (DD §7.6)


async def daily_snapshot_if_due(conn: asyncpg.Connection, *, local_now: datetime) -> int:
    """Write today's trend row once, on the first watcher run at or after 00:05 local time."""
    today = local_now.date()
    if local_now.time() < SNAPSHOT_AT or await scores.has_daily(conn, day=today):
        return 0
    return await daily_snapshot(conn, today=today)
