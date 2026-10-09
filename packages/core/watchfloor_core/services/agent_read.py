"""Agent-only reads (DD §6.5) for deep-triage specialists.

A job-scoped token may read only the deal its job is about (and that deal's
account). Free-text bodies are wrapped in the untrusted envelope here, on the
server, so CRM text never reaches a model unwrapped.
"""

from __future__ import annotations

from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..envelope import wrap_untrusted
from ..errors import ErrorCode, WatchfloorError
from ..models import tools as m
from ..repos import deals, jobs, misc, proposals
from ..speech import money_display, plural, stage_label
from .context import ServiceContext

PLAYBOOK_PATH = Path(__file__).resolve().parent.parent / "config" / "playbook.yaml"


async def ensure_job_scope(
    ctx: ServiceContext, *, deal_id: str | None = None, account_id: str | None = None
) -> None:
    """Reject reads outside the job's deal. RLS already confines reads to the workspace."""
    job_id = ctx.principal.job_id
    if job_id is None:
        return
    job = await jobs.get(ctx.conn, job_id)
    card = await deals.get_card(ctx.conn, job["deal_id"]) if job else None
    allowed = card is not None and (
        (deal_id is None or deal_id == job["deal_id"])
        and (account_id is None or account_id == card["account_id"])
    )
    if not allowed:
        raise WatchfloorError(
            ErrorCode.DEAL_NOT_FOUND,
            "That record is outside this review.",
            recovery="Only the deal under review and its account can be read.",
        )


async def timeline(ctx: ServiceContext, args: m.TimelineIn) -> m.TimelineOut:
    await ensure_job_scope(ctx, deal_id=args.deal_id)
    rows = await misc.timeline(ctx.conn, args.deal_id, since=ctx.now - timedelta(days=args.days), limit=200)
    items = [
        m.TimelineItem(
            activity_id=r["id"],
            type=r["type"],
            direction=r["direction"],
            occurred_at=ctx.local_iso(r["occurred_at"]),
            summary=r["summary"],
            body=wrap_untrusted(r["body"], source="activity", ref_id=r["id"]) if r["body"] else None,
        )
        for r in rows
    ]
    return m.TimelineOut(
        summary=f"{plural(len(items), 'activity', 'activities')} in the last {args.days} days.",
        deal_id=args.deal_id,
        activities=items,
    )


async def history(ctx: ServiceContext, args: m.HistoryIn) -> m.HistoryOut:
    await ensure_job_scope(ctx, deal_id=args.deal_id)
    rows = await misc.history(ctx.conn, args.deal_id)
    return m.HistoryOut(
        summary=f"{plural(len(rows), 'field change')} recorded.",
        deal_id=args.deal_id,
        changes=[
            m.HistoryItem(
                history_id=r["id"],
                field=r["field"],
                old_value=r["old_value"],
                new_value=r["new_value"],
                changed_at=ctx.local_iso(r["changed_at"]),
            )
            for r in rows
        ],
    )


async def account_context(ctx: ServiceContext, args: m.AccountContextIn) -> m.AccountContextOut:
    await ensure_job_scope(ctx, account_id=args.account_id)
    acct = await misc.account(ctx.conn, args.account_id)
    if acct is None:
        raise WatchfloorError(ErrorCode.DEAL_NOT_FOUND, "I couldn't find that account.")
    contacts = await misc.contacts(ctx.conn, args.account_id)
    all_deals = await deals.account_deals(ctx.conn, args.account_id)
    open_deals = [d for d in all_deals if d["stage"] not in ("won", "lost")]
    won = sum(1 for d in all_deals if d["stage"] == "won")
    lost = sum(1 for d in all_deals if d["stage"] == "lost")
    return m.AccountContextOut(
        summary=(
            f"{acct['name']}: {plural(len(contacts), 'contact')}, {plural(len(open_deals), 'open deal')}, "
            f"{won} won and {lost} lost before."
        ),
        account_id=acct["id"],
        name=acct["name"],
        segment=acct["segment"],
        contacts=[
            m.ContactOut(
                contact_id=c["id"], name=c["name"], title=c["title"], is_economic_buyer=c["is_economic_buyer"]
            )
            for c in contacts
        ],
        open_deals=[
            m.OtherDeal(
                deal_id=d["id"],
                name=d["name"],
                stage=stage_label(d["stage"]),
                amount_display=money_display(d["amount_cents"]),
            )
            for d in open_deals
        ],
        won_count=won,
        lost_count=lost,
    )


async def prior_triage(ctx: ServiceContext, args: m.PriorTriageIn) -> m.PriorTriageOut:
    await ensure_job_scope(ctx, deal_id=args.deal_id)
    rows = [
        r for r in await jobs.prior(ctx.conn, args.deal_id, args.limit + 1) if r["id"] != ctx.principal.job_id
    ]
    outcomes = await proposals.outcomes_for_deal(ctx.conn, args.deal_id)
    outcome_text = [f"{o['action_type']}: {o['status']}" for o in outcomes]
    reviews = []
    for r in rows[: args.limit]:
        result: dict[str, Any] = r["result"] or {}
        reviews.append(
            m.PriorTriageItem(
                job_id=r["id"],
                finished_at=ctx.local_iso(r["finished_at"]) if r["finished_at"] else None,
                voice_summary=result.get("voice_summary"),
                recommended_play_ids=[a["play_id"] for a in result.get("recommended_actions", [])],
                outcomes=outcome_text,
            )
        )
    return m.PriorTriageOut(
        summary=f"{plural(len(reviews), 'earlier review')} for this deal.",
        deal_id=args.deal_id,
        reviews=reviews,
    )


@lru_cache(maxsize=1)
def _plays() -> tuple[m.Play, ...]:
    with PLAYBOOK_PATH.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    return tuple(m.Play(**p) for p in doc["plays"])


async def playbook(ctx: ServiceContext, args: m.PlaybookIn) -> m.PlaybookOut:
    plays = list(_plays())
    return m.PlaybookOut(summary=f"{len(plays)} plays.", plays=plays)
