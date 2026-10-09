"""Deep-triage jobs (DD §10.1): start with dedupe, read results, complete from the dispatcher."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import asyncpg
from pydantic import ValidationError

from ..errors import ErrorCode, WatchfloorError
from ..models import tools as m
from ..models.triage import TriageOutput
from ..repos import deals, jobs, misc, outbox, prefs, scores
from ..settings import get_settings
from . import slack_blocks
from .context import ServiceContext
from .read import open_card

DEDUPE_WINDOW = timedelta(minutes=10)
MAX_ACTIVE_JOBS = 3
ETA_SECONDS = 20
HARD_TIMEOUT = timedelta(seconds=90)


async def start(ctx: ServiceContext, args: m.StartTriageIn) -> m.StartTriageOut:
    card = await open_card(ctx, args.deal_id)
    if not get_settings().models_enabled:  # no agents here: say so now, not as a failed job later
        raise WatchfloorError(
            ErrorCode.TEMPORARILY_UNAVAILABLE,
            f"Deep reviews aren't available in this environment, so I can't review {card['account']} in depth.",
            recovery="Ask me why the deal is at risk for its scored reasons instead.",
        )
    await deals.lock(ctx.conn, args.deal_id)  # serialises concurrent starts for one deal
    existing = await jobs.find_reusable(ctx.conn, args.deal_id, ctx.now, DEDUPE_WINDOW)
    if existing is not None:
        done = existing["status"] == "done"
        return m.StartTriageOut(
            summary=(
                f"I already reviewed {card['account']} a few minutes ago. Ask me what it found."
                if done
                else f"A deep review of {card['account']} is already running. Ask me for the results shortly."
            ),
            job_id=existing["id"],
            status=existing["status"],
            eta_seconds=0 if done else ETA_SECONDS,
            deduplicated=True,
        )
    if await jobs.count_active(ctx.conn) >= MAX_ACTIVE_JOBS:
        raise WatchfloorError(
            ErrorCode.JOB_LIMIT_REACHED,
            "Three deep reviews are already running, the most I run at once.",
            recovery="Try again in about a minute.",
        )
    jid = await jobs.create(
        ctx.conn,
        workspace_id=ctx.ws,
        user_id=ctx.user_id,
        deal_id=args.deal_id,
        trace_id=ctx.trace_id,
        at=ctx.now,
    )
    oid = await outbox.enqueue(
        ctx.conn,
        workspace_id=ctx.ws,
        kind="triage_job",
        payload={"job_id": jid},
        dedupe_key=f"job:{jid}",
        at=ctx.now,
    )
    if oid is not None:
        ctx.outbox_ids.append(oid)
    return m.StartTriageOut(
        summary=(
            f"I've started a deep review of {card['account']}. It takes about {ETA_SECONDS} seconds, "
            "and the plan will also go to Slack."
        ),
        job_id=jid,
        status="queued",
        eta_seconds=ETA_SECONDS,
        deduplicated=False,
    )


async def get_result(ctx: ServiceContext, args: m.GetTriageIn) -> m.TriageResultOut:
    job = await (
        jobs.get(ctx.conn, args.job_id) if args.job_id else jobs.latest_for_deal(ctx.conn, args.deal_id)
    )
    if job is None or (args.job_id and args.deal_id and job["deal_id"] != args.deal_id):
        raise WatchfloorError(
            ErrorCode.JOB_NOT_FOUND,
            "I don't have a deep review for that deal yet.",
            recovery="Ask me to dig into it, and I'll start one.",
        )
    card = await deals.get_card(ctx.conn, job["deal_id"])
    account = card["account"] if card else "that deal"
    status = job["status"]
    base: dict[str, Any] = {"job_id": job["id"], "deal_id": job["deal_id"], "status": status}

    if status in ("queued", "running"):
        if ctx.now - job["created_at"] > HARD_TIMEOUT * 2:  # the sweeper will mark it; report it honestly now
            status = base["status"] = "timed_out"
        else:
            return m.TriageResultOut(
                summary=f"The review of {account} is still running. Give it a few more seconds.", **base
            )
    if status in ("failed", "timed_out"):
        return m.TriageResultOut(
            summary=f"The review of {account} didn't finish. Ask me to dig into it again.", **base
        )

    out = TriageOutput.model_validate(job["result"])
    return m.TriageResultOut(
        summary=out.voice_summary,
        voice_summary=out.voice_summary,
        drivers=[m.DriverOut(**d.model_dump()) for d in out.risk_drivers],
        recommended_actions=[m.RecommendedActionOut(**a.model_dump()) for a in out.recommended_actions],
        finished_at=ctx.local_iso(job["finished_at"]) if job["finished_at"] else None,
        **base,
    )


async def complete_job(
    conn: asyncpg.Connection,
    *,
    workspace_id: str,
    job_id: str,
    raw: Any,
    now: datetime,
    outbox_ids: list[int] | None = None,
) -> bool:
    """Schema gate, evidence check, store and Slack plan, atomically (DD §10.6). False when rejected.

    The Slack plan's outbox id is appended to ``outbox_ids`` for publishing after commit.
    """
    job = await jobs.get(conn, job_id)
    if job is None or job["status"] not in ("queued", "running"):
        return False
    try:
        out = TriageOutput.model_validate(raw)
    except ValidationError as exc:
        await jobs.fail(conn, job_id, f"output rejected: {exc.error_count()} validation errors", now)
        return False
    known = await misc.evidence_ids(conn, job["deal_id"])
    unsupported = out.evidence_ids() - known
    if unsupported:
        await jobs.fail(conn, job_id, f"output cites unknown evidence: {sorted(unsupported)[:3]}", now)
        return False
    if not await jobs.complete(conn, job_id, out.model_dump(mode="json"), now):
        return False

    card = await deals.get_card(conn, job["deal_id"])
    rs = await scores.get(conn, job["deal_id"])
    pr = await prefs.get(conn, job["user_id"], workspace_id)
    msg = slack_blocks.triage_plan(
        account=card["account"],
        score=rs["score"] if rs else 0,
        band=rs["band"] if rs else "healthy",
        voice_summary=out.voice_summary,
        actions=[a.model_dump() for a in out.recommended_actions],
    )
    oid = await outbox.enqueue(
        conn,
        workspace_id=workspace_id,
        kind="slack_message",
        payload={"channel": pr["alert_channel"], "message": msg},
        dedupe_key=f"jobplan:{job_id}",
        at=now,
    )
    if oid is not None and outbox_ids is not None:
        outbox_ids.append(oid)
    return True


async def fail_job(
    conn: asyncpg.Connection, job_id: str, error: str, now: datetime, *, timed_out: bool = False
) -> None:
    await jobs.fail(conn, job_id, error, now, timed_out=timed_out)
