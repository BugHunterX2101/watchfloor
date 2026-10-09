"""Best-effort hand-off of committed outbox rows to the dispatcher queue (DD §8.4).

Runs only after COMMIT. A failed or skipped send loses nothing: the sweeper
re-publishes due rows, and locally (no queue URL) the dispatcher polls the
outbox table directly. Message body: ``{"ws": "ws_…", "outbox_id": 123}``; the
dispatcher needs the workspace to read the row under row-level security.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import orjson

from .settings import get_settings

log = logging.getLogger("watchfloor.queue")

Publisher = Callable[[str, list[int]], Awaitable[None]]
_publisher: Publisher | None = None


def set_publisher(fn: Publisher | None) -> None:
    """Tests install a recording stub here (fixture ``sqs_stub``)."""
    global _publisher
    _publisher = fn


async def publish(workspace_id: str, outbox_ids: list[int]) -> None:
    if not outbox_ids:
        return
    try:
        await (_publisher or _sqs_publish)(workspace_id, list(outbox_ids))
    except Exception:
        log.warning("outbox publish failed; the sweeper will resend", extra={"outbox_ids": outbox_ids})


async def _sqs_publish(workspace_id: str, outbox_ids: list[int]) -> None:
    url = get_settings().outbox_queue_url
    if not url:
        return
    await asyncio.to_thread(_send_batches, url, workspace_id, outbox_ids)


def _send_batches(url: str, workspace_id: str, outbox_ids: list[int]) -> None:
    import boto3  # lazy: only in AWS

    sqs = boto3.client("sqs")
    for start in range(0, len(outbox_ids), 10):
        chunk = outbox_ids[start : start + 10]
        sqs.send_message_batch(
            QueueUrl=url,
            Entries=[
                {"Id": str(oid), "MessageBody": orjson.dumps({"ws": workspace_id, "outbox_id": oid}).decode()}
                for oid in chunk
            ],
        )
