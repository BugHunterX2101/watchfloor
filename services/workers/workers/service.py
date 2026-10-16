"""The workers as one long-running process, for ECS Fargate where Lambda is refused (ADR-025).

It does what four functions do on Lambda, with the same code:

- **dispatcher**: one SQS consumer per queue (jobs, outbox). Each message goes through
  :func:`workers.dispatcher.handle_batch` exactly as an event source mapping would hand it over, and
  is deleted only when that reports no failure. A failure or a crash leaves the message for SQS to
  redeliver after its visibility timeout, then the dead-letter queue (``maxReceiveCount``), as before;
- **sweeper** every minute, **watcher** every 15 minutes, **canary** every 5 minutes (where its
  secret is configured), each with the timeout its function had.

At most ``MAX_IN_FLIGHT`` messages are received at once, a long poll included, so a message is
never received before there is room to work on it (its visibility timeout runs from receipt).
``SIGTERM`` (an ECS deployment or scale-in) stops receiving and gives in-flight messages up to
``DRAIN_S`` to finish, inside the task's 120 s stop timeout; whatever is cut off is redelivered.

    python -m workers.service
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import time
from collections.abc import Awaitable, Callable
from typing import Any

from watchfloor_core import db
from watchfloor_core.settings import get_settings

from . import canary, dispatcher, runtime, sweeper, watcher

log = logging.getLogger("watchfloor.service")

MAX_IN_FLIGHT = 4  # two pollers plus two deliveries; the dispatcher Lambda ran at most three
POOL_SIZE = 6  # deliveries, the sweeper and the watcher at once
WAIT_S = 20  # SQS long poll
DRAIN_S = 100.0
SCHEDULE: tuple[tuple[str, float, float, float], ...] = (
    # name, every (s), first run after (s), timeout (s): the Lambda timeouts
    ("sweeper", 60.0, 0.0, 30.0),
    ("watcher", 900.0, 5.0, 60.0),
    ("canary", 300.0, 60.0, 30.0),
)

Batch = Callable[[dict[str, Any], Any], Awaitable[dict[str, Any]]]


def sqs_client() -> Any:
    import boto3  # lazy: only in AWS
    from botocore.config import Config

    return boto3.client(
        "sqs",
        config=Config(
            connect_timeout=5, read_timeout=WAIT_S + 10, retries={"mode": "standard", "max_attempts": 3}
        ),
    )


async def deliver(sqs: Any, url: str, message: dict[str, Any], deps: Any, handle: Batch) -> bool:
    """One message, as the event source mapping delivers it. True when it was deleted."""
    event = {"Records": [{"messageId": message["MessageId"], "body": message["Body"]}]}
    try:
        result = await handle(event, deps)
    except Exception:
        log.exception("message %s crashed; SQS will redeliver it", message["MessageId"])
        return False
    if result.get("batchItemFailures"):
        return False  # handle_batch has logged why; SQS redelivers it
    try:
        await asyncio.to_thread(sqs.delete_message, QueueUrl=url, ReceiptHandle=message["ReceiptHandle"])
    except Exception:  # delivery is idempotent on the outbox row, so a redelivery is harmless
        log.exception("could not delete message %s; it will be redelivered", message["MessageId"])
        return False
    return True


async def consume(
    sqs: Any,
    url: str,
    deps: Any,
    *,
    gate: asyncio.Semaphore,
    stop: asyncio.Event,
    in_flight: set[asyncio.Task[bool]],
    handle: Batch = dispatcher.handle_batch,
) -> None:
    """Receive from one queue until ``stop``; each message is delivered on its own task."""
    while not stop.is_set():
        await gate.acquire()
        handed_off = False
        try:
            if stop.is_set():
                return
            try:
                resp = await asyncio.to_thread(
                    sqs.receive_message, QueueUrl=url, MaxNumberOfMessages=1, WaitTimeSeconds=WAIT_S
                )
            except Exception:
                log.exception("receive from %s failed; retrying", url.rsplit("/", 1)[-1])
                await _sleep(5.0, stop)
                continue
            for message in resp.get("Messages", []):
                task = asyncio.create_task(_release_after(gate, deliver(sqs, url, message, deps, handle)))
                in_flight.add(task)
                task.add_done_callback(in_flight.discard)
                handed_off = True
        finally:
            if not handed_off:
                gate.release()


async def _release_after(gate: asyncio.Semaphore, work: Awaitable[bool]) -> bool:
    try:
        return await work
    finally:
        gate.release()


async def every(
    name: str,
    period_s: float,
    first_s: float,
    timeout_s: float,
    job: Callable[[], Awaitable[Any]],
    stop: asyncio.Event,
) -> None:
    """Run ``job`` every ``period_s`` until ``stop``; a failure or a timeout waits for the next turn."""
    await _sleep(first_s, stop)
    while not stop.is_set():
        t0 = time.monotonic()
        try:
            await asyncio.wait_for(job(), timeout_s)
        except TimeoutError:
            log.error("%s did not finish in %.0f s", name, timeout_s)
        except Exception:
            log.exception("%s failed", name)
        await _sleep(max(0.0, period_s - (time.monotonic() - t0)), stop)


async def _sleep(seconds: float, stop: asyncio.Event) -> None:
    """Sleep ``seconds``, or less if ``stop`` is set first."""
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), seconds)


def jobs() -> dict[str, Callable[[], Awaitable[Any]]]:
    found: dict[str, Callable[[], Awaitable[Any]]] = {"sweeper": sweeper.run, "watcher": watcher.run}
    s = get_settings()
    if s.canary_secret_arn or (s.canary_email and s.canary_password):
        found["canary"] = canary.run
    return found


async def serve() -> None:
    s = get_settings()
    queues = [url for url in (s.jobs_queue_url, s.outbox_queue_url) if url]
    if not queues:
        raise SystemExit("WF_JOBS_QUEUE_URL and WF_OUTBOX_QUEUE_URL are not set")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError):  # Windows: Ctrl+C raises instead
            loop.add_signal_handler(sig, stop.set)

    await runtime.ensure_pool(max_size=POOL_SIZE)
    deps = dispatcher.Deps.from_settings()
    sqs = sqs_client()
    gate = asyncio.Semaphore(MAX_IN_FLIGHT)
    in_flight: set[asyncio.Task[bool]] = set()
    scheduled = jobs()
    tasks = [
        asyncio.create_task(consume(sqs, url, deps, gate=gate, stop=stop, in_flight=in_flight))
        for url in queues
    ] + [
        asyncio.create_task(every(name, period, first, timeout, scheduled[name], stop))
        for name, period, first, timeout in SCHEDULE
        if name in scheduled
    ]
    log.info("worker service up: %d queues, schedules %s", len(queues), sorted(scheduled))
    await stop.wait()

    log.info("stopping: %d messages in flight", len(in_flight))
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    if in_flight:
        _, unfinished = await asyncio.wait(set(in_flight), timeout=DRAIN_S)
        if unfinished:
            log.warning("%d messages cut off; SQS will redeliver them", len(unfinished))
    await db.close_pool()


def main() -> int:
    runtime.configure_logging()
    asyncio.run(serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
