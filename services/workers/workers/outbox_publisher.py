"""Hand due outbox rows to SQS (TD §6.4). Used by the watcher and the sweeper.

A no-op locally, where no queue URL is set and the dispatcher polls the table.
"""

from __future__ import annotations

from datetime import timedelta

from watchfloor_core import queue
from watchfloor_core.db import ws_tx
from watchfloor_core.repos import outbox
from watchfloor_core.services.context import workspace_time

from . import runtime


async def flush_due(*, grace: timedelta = timedelta(0), limit: int = 200) -> int:
    """Publish rows pending ``grace`` past their next attempt, per workspace. Returns how many."""
    total = 0
    for ws in await runtime.workspace_ids():
        async with ws_tx(ws) as conn:
            now = (await workspace_time(conn, ws)).now
            ids = await outbox.due_ids(conn, now - grace, limit)
        await queue.publish(ws, ids)
        total += len(ids)
    return total
