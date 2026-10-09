"""Outbox dispatcher (DD §8.4, §10.2): an SQS consumer in AWS, a table poller locally.

Every message names one outbox row, ``{"ws": …, "outbox_id": …}``. Delivery is
at-least-once and idempotent on the row. :func:`process` first *claims* it
(attempts + 1, plus a lease that pushes ``next_attempt_at`` forward, committed at
once), then does the slow work outside any transaction, then records one of:

- ``sent``: delivered, or nothing left to do (the job had already finished);
- ``retry``: transient failure, backed off 30 s → 2 min → 10 min → 30 min;
- ``dead``: permanent failure, or the fifth failure in a row (alarm);
- ``skipped``: another worker holds the row, it is backing off, or it is done.

Business failures never raise into SQS. Only an unexpected crash leaves the
message for redelivery (``maxReceiveCount`` 3, then the dead-letter queue).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import asyncpg
import orjson
from watchfloor_core import queue
from watchfloor_core.db import ws_tx
from watchfloor_core.models.triage import TriageOutput
from watchfloor_core.repos import deals, jobs, misc, outbox, prefs, users
from watchfloor_core.services import slack_blocks, triage
from watchfloor_core.services.context import workspace_time
from watchfloor_core.settings import get_settings

from . import runtime
from .agentcore_client import AgentTimeout, Invoker, invoker_from_settings
from .drafts import Drafter, DraftRequest, drafter_from_settings
from .errors import GiveUp, RetryLater
from .job_tokens import Minter, minter_from_settings
from .memory_events import Recorder, recorder_from_settings
from .slack import webhook_from_settings

log = logging.getLogger("watchfloor.dispatcher")
LEASE = timedelta(minutes=3)  # = SQS visibility timeout, longer than one agent run
DEFAULT_CHANNEL = "#deals-at-risk"
DRAFT_CONTEXT = timedelta(days=30)

SlackPoster = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class Deps:
    """Everything that talks to the outside world, so tests can swap in fakes."""

    slack: SlackPoster
    invoke_agent: Invoker
    mint_job_token: Minter
    draft: Drafter
    remember: Recorder

    @classmethod
    def from_settings(cls) -> Deps:
        s = get_settings()
        return cls(
            slack=webhook_from_settings(s),
            invoke_agent=invoker_from_settings(s),
            mint_job_token=minter_from_settings(s),
            draft=drafter_from_settings(s),
            remember=recorder_from_settings(s),
        )


Handler = Callable[[str, asyncpg.Record, Deps, list[int]], Awaitable[None]]


async def _now(conn: asyncpg.Connection, ws: str) -> datetime:
    return (await workspace_time(conn, ws)).now


async def process(ws: str, outbox_id: int, deps: Deps) -> str:
    """Deliver one outbox row. Returns sent, retry, dead or skipped."""
    async with ws_tx(ws) as conn:
        row = await outbox.claim(conn, outbox_id, now=await _now(conn, ws), lease=LEASE)
    if row is None:
        return "skipped"

    t0 = time.perf_counter()
    follow_ups: list[int] = []
    error: str | None = None
    retry_after: timedelta | None = None
    dead = False
    try:
        await HANDLERS[row["kind"]](ws, row, deps, follow_ups)
    except RetryLater as exc:
        error, retry_after = str(exc), exc.retry_after
    except GiveUp as exc:
        error, dead = str(exc), True
    except Exception as exc:
        log.exception("outbox %s (%s) crashed", outbox_id, row["kind"])
        error = f"{type(exc).__name__}: {exc}"

    async with ws_tx(ws) as conn:
        now = await _now(conn, ws)
        if error is None:
            await outbox.mark_sent(conn, outbox_id, now)
            outcome = "sent"
        elif dead:
            await outbox.mark_dead(conn, outbox_id, error)
            outcome = "dead"
        else:
            outcome = await outbox.record_failure(
                conn, outbox_id, attempts=row["attempts"], error=error, now=now, retry_after=retry_after
            )
    await queue.publish(ws, follow_ups)

    detail = f" ({error})" if error else ""
    level = logging.WARNING if outcome == "dead" else logging.INFO
    log.log(level, "outbox %s %s attempt %d: %s%s", outbox_id, row["kind"], row["attempts"], outcome, detail)
    runtime.emit_metrics(
        "dispatcher",
        {
            "OutboxDeliveryMs": runtime.elapsed_ms(t0),
            "OutboxSent": int(outcome == "sent"),
            "OutboxDeadLettered": int(outcome == "dead"),
        },
        {"kind": row["kind"]},
    )
    return outcome


# ------------------------------------------------------------------ handlers


async def _slack(ws: str, row: asyncpg.Record, deps: Deps, follow_ups: list[int]) -> None:
    p = row["payload"]
    await deps.slack(p.get("channel") or DEFAULT_CHANNEL, p["message"])


async def _draft(ws: str, row: asyncpg.Record, deps: Deps, follow_ups: list[int]) -> None:
    """Draft with a Haiku-class model, then DM it (P1 fallback: the requester's alert channel)."""
    p = dict(row["payload"])
    async with ws_tx(ws) as conn:
        now = await _now(conn, ws)
        card = await deals.get_card(conn, p["deal_id"])
        me = await users.get(conn, p["user_id"])
        if card is None or me is None:
            raise GiveUp("the deal or the requester no longer exists")
        channel = (await prefs.get(conn, p["user_id"], ws))["alert_channel"]
        recent = await misc.timeline(conn, p["deal_id"], since=now - DRAFT_CONTEXT, limit=6)
        contacts = await misc.contacts(conn, card["account_id"])
    buyer = next((c["name"] for c in contacts if c["id"] == card["economic_buyer_id"]), None)

    draft = p.get("draft")
    if not draft:
        draft = await deps.draft(
            DraftRequest(
                account=card["account"],
                deal_name=card["name"],
                stage=card["stage"],
                sender=me["display_name"],
                intent=p["intent"],
                contact=buyer,
                activities=[(a["id"], a["summary"]) for a in recent],
            )
        )
    message = slack_blocks.draft_message(account=card["account"], draft=draft, requester=me["display_name"])
    try:
        await deps.slack(channel, message)
    except RetryLater:
        if not p.get("draft"):  # keep the draft, so the retry only redoes delivery
            async with ws_tx(ws) as conn:
                await outbox.set_payload(conn, row["id"], {**p, "draft": draft})
        raise


async def _triage(ws: str, row: asyncpg.Record, deps: Deps, follow_ups: list[int]) -> None:
    """Run the deep-triage agent for one job, then gate and store its output (DD §10.2)."""
    job_id = row["payload"]["job_id"]
    async with ws_tx(ws) as conn:
        now = await _now(conn, ws)
        job = await jobs.mark_running(conn, job_id, now)
        if job is not None and now - job["created_at"] > triage.HARD_TIMEOUT * 2:
            await triage.fail_job(conn, job_id, "started after the deadline", now, timed_out=True)
            job = None
    if job is None:
        return  # already finished: a duplicate message, or a retry after the deadline

    deal_id = job["deal_id"]
    try:
        token = await deps.mint_job_token(ws, job_id)
        raw = await deps.invoke_agent(
            {
                "job_id": job_id,
                "deal_id": deal_id,
                "workspace_id": ws,  # with user_ref, the AgentCore Memory actor (DD §11)
                "user_ref": job["user_id"],
                "mcp_token": token,
                "trace_id": job["trace_id"],
            },
            f"triage-{job_id}-{deal_id}",
        )
    except AgentTimeout as exc:
        await _fail_job(ws, job_id, str(exc), timed_out=True)
        return
    except GiveUp as exc:
        await _fail_job(ws, job_id, str(exc))
        raise

    async with ws_tx(ws) as conn:
        now = await _now(conn, ws)
        done = await triage.complete_job(
            conn, workspace_id=ws, job_id=job_id, raw=raw, now=now, outbox_ids=follow_ups
        )
        card = await deals.get_card(conn, deal_id) if done else None
    if done:
        account = card["account"] if card else deal_id
        await deps.remember(
            ws, job["user_id"], memory_text(account, deal_id, TriageOutput.model_validate(raw))
        )


async def _fail_job(ws: str, job_id: str, error: str, *, timed_out: bool = False) -> None:
    async with ws_tx(ws) as conn:
        await triage.fail_job(conn, job_id, error, await _now(conn, ws), timed_out=timed_out)


def memory_text(account: str, deal_id: str, out: TriageOutput) -> str:
    """E.g. "Triage for Kestrel Biotech (dl_…): drivers R,I,S; recommended P01, P04." (DD §11)."""
    drivers = ",".join(dict.fromkeys(d.factor for d in out.risk_drivers))
    plays = ", ".join(a.play_id for a in out.recommended_actions) or "no actions"
    return f"Triage for {account} ({deal_id}): drivers {drivers}; recommended {plays}."


HANDLERS: dict[str, Handler] = {
    "slack_message": _slack,
    "slack_dm": _slack,  # P1: no bot token yet, so DMs go to the alert channel
    "draft_followup": _draft,
    "triage_job": _triage,
}


# ------------------------------------------------------------------ entry points

_deps: Deps | None = None


def handler(event: dict[str, Any], _ctx: Any) -> dict[str, Any]:
    """SQS batch handler with partial-batch failure reporting."""
    return runtime.run_in_lambda(handle_batch(event))


async def handle_batch(event: dict[str, Any], deps: Deps | None = None) -> dict[str, Any]:
    global _deps
    await runtime.ensure_pool()
    if deps is None:
        deps = _deps = _deps or Deps.from_settings()
    failures: list[dict[str, str]] = []
    for record in event.get("Records", []):
        try:
            msg = orjson.loads(record["body"])
            ws, outbox_id = str(msg["ws"]), int(msg["outbox_id"])
        except (orjson.JSONDecodeError, KeyError, TypeError, ValueError):
            log.error("dropping malformed message %s", record.get("messageId"))
            continue
        try:
            await process(ws, outbox_id, deps)
        except Exception:
            log.exception("message %s failed; SQS will redeliver it", record.get("messageId"))
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}


async def drain(deps: Deps, *, limit: int = 100) -> Counter[str]:
    """One pass over every workspace's due rows, delivered concurrently. Local and tests."""
    outcomes: Counter[str] = Counter()
    for ws in await runtime.workspace_ids():
        async with ws_tx(ws) as conn:
            ids = await outbox.due_ids(conn, await _now(conn, ws), limit)
        outcomes.update(await asyncio.gather(*(process(ws, oid, deps) for oid in ids)))
    return outcomes


async def poll(deps: Deps, *, every_s: float = 2.0, max_concurrent: int = 3) -> None:
    """Local stand-in for SQS: deliver due rows as they appear, at most three at a time."""
    gate = asyncio.Semaphore(max_concurrent)
    in_flight: set[int] = set()
    tasks: set[asyncio.Task[None]] = set()

    async def deliver(ws: str, oid: int) -> None:
        try:
            async with gate:
                await process(ws, oid, deps)
        except Exception:
            log.exception("outbox %s failed", oid)
        finally:
            in_flight.discard(oid)

    while True:
        try:
            for ws in await runtime.workspace_ids():
                async with ws_tx(ws) as conn:
                    due = await outbox.due_ids(conn, await _now(conn, ws))
                for oid in due:
                    if oid not in in_flight:
                        in_flight.add(oid)
                        task = asyncio.create_task(deliver(ws, oid))
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)
        except Exception:
            log.exception("outbox poll failed; retrying")
        await asyncio.sleep(every_s)


def main(argv: list[str] | None = None) -> int:
    runtime.configure_logging()
    ap = argparse.ArgumentParser(
        description="Deliver outbox rows by polling the table (local stand-in for SQS)."
    )
    ap.add_argument("--once", action="store_true", help="deliver what is due now, then exit")
    ap.add_argument("--every", type=float, default=2.0, help="seconds between polls")
    opts = ap.parse_args(argv)

    async def go() -> None:
        await runtime.ensure_pool(max_size=4)
        deps = Deps.from_settings()
        if opts.once:
            print(dict(await drain(deps)))
        else:
            await poll(deps, every_s=opts.every)

    asyncio.run(go())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
