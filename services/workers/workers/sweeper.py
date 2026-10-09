"""Sweeper (DD §8.1, §8.4, §10.1): every minute, for each workspace.

- Hand back to SQS outbox rows still pending 60 s past ``next_attempt_at``: a
  lost fast-path send, a finished backoff, or the lapsed lease of a worker that died.
- Expire confirmation proposals past their 5-minute TTL.
- End deep-triage jobs with no result 180 s after creation as ``timed_out``.
- Report ``OutboxBacklog`` and ``OutboxDead`` (the delivery-health alarms).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections import Counter
from datetime import timedelta

from watchfloor_core import queue
from watchfloor_core.db import ws_tx
from watchfloor_core.repos import jobs, outbox, proposals
from watchfloor_core.services import triage
from watchfloor_core.services.context import workspace_time

from . import runtime

log = logging.getLogger("watchfloor.sweeper")
GRACE = timedelta(seconds=60)
JOB_DEADLINE = triage.HARD_TIMEOUT * 2


async def run() -> dict[str, int]:
    stats: Counter[str] = Counter(republished=0, expired_proposals=0, timed_out_jobs=0, backlog=0, dead=0)
    for ws in await runtime.workspace_ids():
        try:
            async with ws_tx(ws) as conn:
                now = (await workspace_time(conn, ws)).now
                stats["expired_proposals"] += await proposals.expire_due(conn, now)
                stats["timed_out_jobs"] += await jobs.time_out_stale(
                    conn, created_before=now - JOB_DEADLINE, at=now
                )
                due = await outbox.due_ids(conn, now - GRACE)
                counts = await outbox.counts(conn)
        except Exception:
            log.exception("sweeper failed for workspace %s", ws)
            stats["failed"] += 1
            continue
        await queue.publish(ws, due)
        stats["republished"] += len(due)
        stats["backlog"] += counts["pending"]
        stats["dead"] += counts["dead"]

    runtime.emit_metrics(
        "sweeper",
        {
            "OutboxBacklog": stats["backlog"],
            "OutboxDead": stats["dead"],
            "OutboxRepublished": stats["republished"],
            "TriageTimedOut": stats["timed_out_jobs"],
        },
    )
    if stats["dead"]:
        log.warning("outbox has %d dead rows; see TD §17.3 to redrive", stats["dead"])
    return dict(stats)


def handler(_event: object, _ctx: object) -> dict[str, int]:
    """EventBridge Scheduler target, rate(1 minute)."""
    return runtime.run_in_lambda(_lambda_run())


async def _lambda_run() -> dict[str, int]:
    await runtime.ensure_pool()
    return await run()


def main(argv: list[str] | None = None) -> int:
    runtime.configure_logging()
    desc = "Re-enqueue due outbox rows, expire proposals, time out jobs."
    argparse.ArgumentParser(description=desc).parse_args(argv)

    async def go() -> None:
        await runtime.ensure_pool()
        print(await run())

    asyncio.run(go())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
