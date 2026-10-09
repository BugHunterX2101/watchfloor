"""Voice CRM updates (PRD J5) and briefing preferences.

Notes and next steps apply immediately and the deal is rescored in the same
transaction. Close-date and amount changes come back as a proposal that needs
a yes, because they move forecasts.
"""

from __future__ import annotations

from typing import Any

from ..errors import ErrorCode, WatchfloorError
from ..models import tools as m
from ..repos import deals, misc, prefs
from ..speech import band_label, date_display, join_words
from . import actions, scoring
from .context import ServiceContext
from .read import current_score, open_card

SEGMENT_WORDS = {"smb": "SMB", "mid": "mid-market", "enterprise": "enterprise"}


async def log_update(ctx: ServiceContext, args: m.LogUpdateIn) -> m.LogUpdateOut:
    card = await open_card(ctx, args.deal_id)
    for d, what in ((args.next_step_date, "next step date"), (args.close_date, "close date")):
        if d is not None and d < ctx.today:
            raise WatchfloorError(
                ErrorCode.INVALID_DATE,
                f"The {what} {date_display(d)} is in the past.",
                recovery="Give me a date from today onward.",
            )
    before = await current_score(ctx, args.deal_id)
    applied: list[str] = []

    note_parts = [args.note] if args.note else []
    if args.stakeholders_mentioned:
        note_parts.append("Stakeholders mentioned: " + ", ".join(args.stakeholders_mentioned) + ".")
    if note_parts:
        await misc.insert_activity(
            ctx.conn,
            workspace_id=ctx.ws,
            deal_id=args.deal_id,
            type_="note",
            direction="internal",
            occurred_at=ctx.now,
            summary="Voice update",
            body=" ".join(note_parts),
        )
        applied.append("note")

    now_fields: dict[str, Any] = {}
    if args.next_step:
        now_fields["next_step"] = args.next_step
    if args.next_step_date:
        now_fields["next_step_date"] = args.next_step_date
    if now_fields:
        await deals.apply_fields(ctx.conn, args.deal_id, now_fields, changed_by=ctx.user_id, at=ctx.now)
        applied.extend(now_fields)

    changes = await scoring.recompute(
        ctx.conn, workspace_id=ctx.ws, now=ctx.now, today=ctx.today, deal_ids=[args.deal_id]
    )
    after = changes[args.deal_id]

    said = {"note": "the note", "next_step": "the next step", "next_step_date": "its date"}
    lead = f"Logged {join_words([said[f] for f in applied])} for {card['account']}." if applied else ""
    if after.score_after != before["score"]:
        lead += f" Risk moved from {before['score']} to {after.score_after}, {band_label(after.band_after)}."
    else:
        lead += f" Risk is unchanged at {after.score_after}."
    lead = lead.strip()

    pending_fields: dict[str, Any] = {}
    if args.close_date and args.close_date != card["close_date"]:
        pending_fields["close_date"] = args.close_date
    if args.amount_usd is not None and args.amount_usd * 100 != card["amount_cents"]:
        pending_fields["amount_cents"] = args.amount_usd * 100

    pending = None
    summary = lead
    if pending_fields:
        pending = await actions.propose_field_update(ctx, args.deal_id, card, pending_fields, lead)
        summary = pending.summary
    return m.LogUpdateOut(
        summary=summary,
        applied_fields=applied,
        score_before=before["score"],
        score_after=after.score_after,
        band_after=after.band_after,  # type: ignore[arg-type]
        pending_proposal=pending,
    )


def _prefs_block(row: Any) -> m.PreferencesBlock:
    return m.PreferencesBlock(
        briefing_limit=row["briefing_limit"],
        include_amounts=row["include_amounts"],
        focus_segment=row["focus_segment"],
        alert_channel=row["alert_channel"],
    )


async def update_preferences(ctx: ServiceContext, args: m.PrefsIn) -> m.PrefsOut:
    changes: dict[str, Any] = {}
    phrases: list[str] = []
    if args.briefing_limit is not None:
        changes["briefing_limit"] = args.briefing_limit
        phrases.append(f"briefings include up to {args.briefing_limit} deals")
    if args.include_amounts is not None:
        changes["include_amounts"] = args.include_amounts
        phrases.append("I'll say deal amounts" if args.include_amounts else "I'll leave out deal amounts")
    if args.focus_segment is not None:
        seg = None if args.focus_segment == "all" else args.focus_segment
        changes["focus_segment"] = seg
        phrases.append(
            f"briefings focus on {SEGMENT_WORDS[seg]} deals" if seg else "briefings cover every segment"
        )
    if args.alert_channel is not None:
        changes["alert_channel"] = args.alert_channel
        phrases.append(f"alerts go to {args.alert_channel}")
    row = await prefs.update(ctx.conn, ctx.user_id, ctx.ws, changes, ctx.now)
    return m.PrefsOut(summary=f"Done. From now on, {join_words(phrases)}.", preferences=_prefs_block(row))
