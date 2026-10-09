"""Watcher (DD §9.2, TD §6.4): every 15 minutes, for each workspace.

1. Recompute RIGS for every open deal (time decay); bands keep their hysteresis.
2. Evaluate standing orders: alerts plus outbox rows, one per rule and deal per 24 h.
3. Write the daily trend snapshot on the first run at or after 00:05 local time.
4. Purge audit and sent-outbox rows after 30 days, trend rows after 180.

Each workspace commits on its own, so one failing workspace never blocks the
others. Afterwards due outbox rows are handed to SQS for the dispatcher.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from collections import Counter

from watchfloor_core import db
from watchfloor_core.db import ws_tx
from watchfloor_core.repos import misc
from watchfloor_core.services import rules, scoring
from watchfloor_core.services.context import workspace_time

from . import outbox_publisher, runtime

log = logging.getLogger("watchfloor.watcher")


async def run() -> dict[str, int]:
    t0 = time.perf_counter()
    stats: Counter[str] = Counter(workspaces=0, failed=0, scored=0, alerts=0, snapshot_rows=0)
    for ws in await runtime.workspace_ids():
        try:
            async with ws_tx(ws, statement_timeout="15s") as conn:
                t = await workspace_time(conn, ws)
                changes = await scoring.recompute(conn, workspace_id=ws, now=t.now, today=t.today)
                alerts = await rules.evaluate_rules(conn, workspace_id=ws, now=t.now)
                snapshot = await scoring.daily_snapshot_if_due(conn, local_now=t.now.astimezone(t.tz))
                await misc.purge_old(conn, t.now)
        except Exception:
            log.exception("watcher failed for workspace %s", ws)
            stats["failed"] += 1
            continue
        stats["workspaces"] += 1
        stats["scored"] += len(changes)
        stats["alerts"] += alerts
        stats["snapshot_rows"] += snapshot

    async with db.system_tx() as conn:
        await misc.purge_rate_limits(conn)
    stats["handed_off"] = await outbox_publisher.flush_due()
    stats["duration_ms"] = runtime.elapsed_ms(t0)
    runtime.emit_metrics(
        "watcher",
        {
            "WatcherDealsScored": stats["scored"],
            "WatcherAlertsQueued": stats["alerts"],
            "WatcherFailures": stats["failed"],
            "WatcherDurationMs": stats["duration_ms"],
        },
    )
    log.info("watcher run: %s", dict(stats))
    return dict(stats)


def handler(_event: object, _ctx: object) -> dict[str, int]:
    """EventBridge Scheduler target, rate(15 minutes)."""
    return runtime.run_in_lambda(_lambda_run())


async def _lambda_run() -> dict[str, int]:
    await runtime.ensure_pool()
    return await run()


def main(argv: list[str] | None = None) -> int:
    runtime.configure_logging()
    ap = argparse.ArgumentParser(description="Recompute risk, evaluate standing orders, snapshot and purge.")
    ap.add_argument("--every", type=float, help="repeat every N seconds instead of running once")
    opts = ap.parse_args(argv)

    async def go() -> None:
        await runtime.ensure_pool()
        while True:
            print(await run())
            if not opts.every:
                return
            await asyncio.sleep(opts.every)

    asyncio.run(go())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
