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
from functools import lru_cache
from typing import Any

import orjson

from .settings import get_settings

log = logging.getLogger("watchfloor.queue")

Publisher = Callable[[str, list[int]], Awaitable[None]]
_publisher: Publisher | None = None


def set_publisher(fn: Publisher | None) -> None:
    """Tests install a recording stub here (fixture ``sqs_stub``)."""
    global _publisher
    _publisher = fn


async def publish(workspace_id: str, outbox_ids: list[int], *, delay_s: int = 0) -> None:
    """``delay_s`` (at most 900, the SQS limit) matches a row held back by its ``next_attempt_at``."""
    if not outbox_ids:
        return
    try:
        if _publisher is not None:
            await _publisher(workspace_id, list(outbox_ids))
        else:
            await _sqs_publish(workspace_id, list(outbox_ids), delay_s)
    except Exception:
        log.warning("outbox publish failed; the sweeper will resend", extra={"outbox_ids": outbox_ids})


async def _sqs_publish(workspace_id: str, outbox_ids: list[int], delay_s: int = 0) -> None:
    url = get_settings().outbox_queue_url
    if not url:
        return
    await asyncio.to_thread(_send_batches, url, workspace_id, outbox_ids, delay_s)


@lru_cache(maxsize=1)
def _sqs() -> Any:
    import boto3  # lazy: only in AWS
    from botocore.config import Config

    # Built once, and short: this runs after COMMIT on the caller's request, so a stalled queue must
    # not hold it for botocore's default minute. A send that fails is resent by the sweeper.
    return boto3.client(
        "sqs", config=Config(connect_timeout=2, read_timeout=3, retries={"total_max_attempts": 2})
    )


def _send_batches(url: str, workspace_id: str, outbox_ids: list[int], delay_s: int = 0) -> None:
    sqs = _sqs()
    for start in range(0, len(outbox_ids), 10):
        chunk = outbox_ids[start : start + 10]
        response = sqs.send_message_batch(
            QueueUrl=url,
            Entries=[
                {
                    "Id": str(oid),
                    "MessageBody": orjson.dumps({"ws": workspace_id, "outbox_id": oid}).decode(),
                    "DelaySeconds": min(900, max(0, delay_s)),
                }
                for oid in chunk
            ],
        )
        if response.get("Failed"):  # a partial failure does not raise
            log.warning("outbox publish: %d of %d messages refused", len(response["Failed"]), len(chunk))
