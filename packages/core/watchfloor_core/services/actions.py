"""Propose → confirm action engine (DD §8).

``propose`` has no business effect: it stores a pending proposal and returns a
preview plus a single-use token. ``confirm`` claims the token atomically, runs
the executor's local writes, and enqueues notification intent in the outbox,
all in the caller's transaction. No network call happens inside confirm.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from .. import tokens
from ..errors import ErrorCode, WatchfloorError
from ..models import tools as m
from ..repos import deals, misc, outbox, prefs, proposals, users
from ..speech import band_label, date_display, money_display, short_date
from . import scoring, slack_blocks
from .context import ServiceContext
from .read import current_score, open_card

UPDATE_FIELDS = "update_fields"  # internal action type, raised by log_deal_update (DD §6.4)
_MENTIONS = re.compile(r"<[@!#][^>]*>|@(here|channel|everyone)\b", re.IGNORECASE)


def clean_message(text: str) -> str:
    return " ".join(_MENTIONS.sub("", text).split())[:300]


async def resolve_teammate(ctx: ServiceContext, name: str) -> Any:
    rows = await users.find_by_name(ctx.conn, name)
    if not rows:
        raise WatchfloorError(
            ErrorCode.UNKNOWN_ASSIGNEE,
            f"I couldn't find a teammate named {name}.",
            recovery="Try their full name as it appears in your workspace.",
        )
    if len(rows) > 1:
        names = [r["display_name"] for r in rows[:3]]
        raise WatchfloorError(
            ErrorCode.AMBIGUOUS_ASSIGNEE,
            f"More than one teammate matches {name}: {' or '.join(names)}.",
            recovery="Which one do you mean?",
            suggestions=[{"user_id": r["id"], "name": r["display_name"]} for r in rows[:3]],
        )
    return rows[0]


def _future_date(ctx: ServiceContext, d: date | None, what: str) -> None:
    if d is not None and d < ctx.today:
        raise WatchfloorError(
            ErrorCode.INVALID_DATE,
            f"The {what} {date_display(d)} is in the past.",
            recovery="Give me a date from today onward.",
        )


async def propose(ctx: ServiceContext, args: m.ProposeIn) -> m.ProposalOut:
    card = await open_card(ctx, args.deal_id)
    pr = await prefs.get(ctx.conn, ctx.user_id, ctx.ws)
    channel = pr["alert_channel"]
    account = card["account"]
    t = args.action_type

    if t == "create_task":
        _future_date(ctx, args.due_date, "due date")
        who = await resolve_teammate(ctx, args.assignee_name)
        params: dict[str, Any] = {
            "assignee_id": who["id"],
            "title": args.title,
            "due_date": args.due_date.isoformat() if args.due_date else None,
            "channel": channel,
        }
        preview: dict[str, Any] = {
            "action_type": t,
            "account": account,
            "assignee": who["display_name"],
            "title": args.title,
            "side_effects": ["task_created", f"slack_post:{channel}"],
        }
        due = ""
        if args.due_date:
            preview["due_date"] = args.due_date.isoformat()
            preview["due_display"] = date_display(args.due_date)
            due = f", due {date_display(args.due_date)}"
        summary = (
            f"Ready to create a task for {who['display_name']}: “{args.title}”{due}, "
            f"and post it to {channel}. Should I go ahead?"
        )
    elif t == "reassign_owner":
        who = await resolve_teammate(ctx, args.new_owner_name)
        if who["id"] == card["owner_id"]:
            raise WatchfloorError(
                ErrorCode.INVALID_ACTION,
                f"{who['display_name']} already owns {account}.",
                recovery="Name a different teammate.",
            )
        # The owner the preview showed: confirm refuses if the deal changed hands since (_unchanged).
        params = {"new_owner_id": who["id"], "channel": channel, "from_owner_id": card["owner_id"]}
        preview = {
            "action_type": t,
            "account": account,
            "from_owner": card["owner"],
            "to_owner": who["display_name"],
            "side_effects": ["owner_changed", f"slack_post:{channel}"],
        }
        summary = (
            f"Ready to move {account} from {card['owner']} to {who['display_name']} "
            f"and post the change to {channel}. Should I go ahead?"
        )
    elif t == "notify_channel":
        message = clean_message(args.message)
        if len(message) < 3:
            raise WatchfloorError(
                ErrorCode.INVALID_ACTION, "That message is empty.", recovery="What should I post?"
            )
        params = {"message": message, "channel": channel}
        preview = {
            "action_type": t,
            "account": account,
            "channel": channel,
            "message": message,
            "side_effects": [f"slack_post:{channel}"],
        }
        summary = f"Ready to post to {channel} about {account}: “{message}”. Should I go ahead?"
    else:  # draft_followup
        params = {"intent": args.intent}
        preview = {
            "action_type": t,
            "account": account,
            "intent": args.intent,
            "side_effects": ["draft_dm"],
        }
        summary = (
            f"Ready to draft a follow-up email for {account} to {args.intent.rstrip('.')}. "
            "I'll send the draft to you in Slack; nothing goes to the customer. Should I go ahead?"
        )
    return await _store(ctx, args.deal_id, t, params, preview, summary)


async def propose_field_update(
    ctx: ServiceContext, deal_id: str, card: Any, changes: dict[str, Any], lead: str
) -> m.ProposalOut:
    """Close-date and amount changes from log_deal_update need a yes (PRD J5)."""
    params: dict[str, Any] = {}
    preview: dict[str, Any] = {"action_type": UPDATE_FIELDS, "account": card["account"], "changes": []}
    phrases = []
    if "close_date" in changes:
        new = changes["close_date"]
        params["close_date"] = new.isoformat()
        params["from_close_date"] = card["close_date"].isoformat()
        preview["changes"].append(
            {
                "field": "close_date",
                "from": card["close_date"].isoformat(),
                "to": new.isoformat(),
                "to_display": date_display(new),
            }
        )
        phrases.append(f"moving the close date from {short_date(card['close_date'])} to {date_display(new)}")
    if "amount_cents" in changes:
        new_amt = changes["amount_cents"]
        params["amount_cents"] = new_amt
        params["from_amount_cents"] = card["amount_cents"]
        preview["changes"].append(
            {"field": "amount", "from": money_display(card["amount_cents"]), "to": money_display(new_amt)}
        )
        phrases.append(
            f"changing the amount from {money_display(card['amount_cents'])} to {money_display(new_amt)}"
        )
    preview["side_effects"] = ["deal_updated", "risk_recomputed"]
    ask = " and ".join(phrases)
    summary = f"{lead} {ask[0].upper()}{ask[1:]} needs your OK. Should I go ahead?"
    return await _store(ctx, deal_id, UPDATE_FIELDS, params, preview, summary)


async def _store(
    ctx: ServiceContext,
    deal_id: str,
    action_type: str,
    params: dict[str, Any],
    preview: dict[str, Any],
    summary: str,
) -> m.ProposalOut:
    await proposals.supersede_pending(ctx.conn, user_id=ctx.user_id, deal_id=deal_id, action_type=action_type)
    token, token_hash = tokens.new_confirmation_token()
    expires = ctx.now + timedelta(seconds=tokens.TOKEN_TTL_SECONDS)
    bound = {"deal_id": deal_id, "action_type": action_type, "params": params}
    pid = await proposals.create(
        ctx.conn,
        workspace_id=ctx.ws,
        user_id=ctx.user_id,
        deal_id=deal_id,
        action_type=action_type,
        params=params,
        params_hash=tokens.params_hash(bound),
        token_hash=token_hash,
        preview=preview,
        expires_at=expires,
        at=ctx.now,
    )
    return m.ProposalOut(
        summary=summary,
        requires_confirmation=True,
        proposal_id=pid,
        confirmation_token=token,
        expires_at=ctx.local_iso(expires),
        preview=preview,
    )


async def confirm(ctx: ServiceContext, args: m.ConfirmIn) -> m.ConfirmOut:
    th = tokens.hash_token(args.confirmation_token)
    row = await proposals.claim(ctx.conn, token_hash=th, user_id=ctx.user_id, now=ctx.now)
    if row is None:
        return await _explain_unclaimed(ctx, th)

    bound = {"deal_id": row["deal_id"], "action_type": row["action_type"], "params": row["params"]}
    if tokens.params_hash(bound) != bytes(row["params_hash"]):
        await _mark_failed(ctx, row["id"])
        raise _mismatch()

    try:
        async with ctx.conn.transaction():  # savepoint: an executor failure keeps the claim, marked failed
            result, side_effects = await EXECUTORS[row["action_type"]](ctx, row)
    except WatchfloorError:
        await _mark_failed(ctx, row["id"])
        raise
    except Exception as exc:
        await _mark_failed(ctx, row["id"])
        raise WatchfloorError(
            ErrorCode.TEMPORARILY_UNAVAILABLE,
            "That action couldn't be completed, and nothing was changed.",
            recovery="Ask me to propose it again in a moment.",
        ) from exc

    audit_id = await misc.record_audit(
        ctx.conn,
        workspace_id=ctx.ws,
        user_id=ctx.user_id,
        tool=f"action:{row['action_type']}",
        args_hash=bytes(row["params_hash"]),
        outcome="executed",
        error_code=None,
        latency_ms=None,
        trace_id=ctx.trace_id,
        at=ctx.now,
    )
    stored = {
        "summary": _done_summary(row["action_type"], row["preview"], result),
        "action_type": row["action_type"],
        "result": result,
        "audit_id": f"aud_{audit_id}",
        "side_effects": side_effects,
    }
    await proposals.set_result(ctx.conn, row["id"], stored)
    return m.ConfirmOut(status="executed", **stored)


def _mismatch() -> WatchfloorError:
    return WatchfloorError(
        ErrorCode.TOKEN_MISMATCH,
        "That confirmation doesn't match a pending action of yours.",
        recovery="Ask me to propose the action again.",
    )


async def _explain_unclaimed(ctx: ServiceContext, token_hash: bytes) -> m.ConfirmOut:
    row = await proposals.get_by_token_hash(ctx.conn, token_hash)
    if row is None or row["user_id"] != ctx.user_id:
        raise _mismatch()
    if row["status"] == "executed" and row["result"]:
        return m.ConfirmOut(status="already_executed", **row["result"])
    if row["status"] == "superseded":
        raise WatchfloorError(
            ErrorCode.TOKEN_EXPIRED,
            "That preview was replaced by a newer one.",
            recovery="Confirm the latest preview, or ask me to propose it again.",
        )
    if row["status"] in ("pending", "expired"):
        raise WatchfloorError(
            ErrorCode.TOKEN_EXPIRED,
            "That confirmation expired. Previews last five minutes.",
            recovery="Ask me to propose the action again.",
        )
    raise WatchfloorError(
        ErrorCode.TOKEN_ALREADY_USED,
        "That action was already attempted and didn't complete.",
        recovery="Ask me to propose it again.",
    )


async def _mark_failed(ctx: ServiceContext, proposal_id: str) -> None:
    await proposals.mark_failed(ctx.conn, proposal_id)


def _done_summary(action_type: str, preview: dict[str, Any], result: dict[str, Any]) -> str:
    account = preview.get("account", "the deal")
    if action_type == "create_task":
        return f"Done. {preview['assignee']} has the task “{preview['title']}”, and the team channel will see it."
    if action_type == "reassign_owner":
        return f"Done. {preview['to_owner']} now owns {account}."
    if action_type == "notify_channel":
        return f"Done. Posting to {preview['channel']} about {account}."
    if action_type == "draft_followup":
        return f"Done. I'm drafting the follow-up for {account}; it will arrive in your Slack shortly."
    score = result.get("score_after")
    tail = f" Risk is now {score}, {band_label(result['band_after'])}." if score is not None else ""
    return f"Done. {account} is updated.{tail}"


# ------------------------------------------------------------------ executors


async def _enqueue_slack(
    ctx: ServiceContext, proposal_id: str, channel: str, message: dict[str, Any]
) -> None:
    oid = await outbox.enqueue(
        ctx.conn,
        workspace_id=ctx.ws,
        kind="slack_message",
        payload={"channel": channel, "message": message},
        dedupe_key=f"pr:{proposal_id}",
        at=ctx.now,
    )
    if oid is not None:
        ctx.outbox_ids.append(oid)


async def _requester(ctx: ServiceContext) -> str:
    me = await users.get(ctx.conn, ctx.user_id)
    return me["display_name"] if me else "a teammate"


async def _exec_create_task(ctx: ServiceContext, row: Any) -> tuple[dict[str, Any], list[str]]:
    p, pv = row["params"], row["preview"]
    assignee = await users.get(ctx.conn, p["assignee_id"])
    if assignee is None:
        raise WatchfloorError(ErrorCode.UNKNOWN_ASSIGNEE, "That teammate is no longer in the workspace.")
    due = date.fromisoformat(p["due_date"]) if p.get("due_date") else None
    tid = await misc.create_task(
        ctx.conn,
        workspace_id=ctx.ws,
        deal_id=row["deal_id"],
        assignee_id=assignee["id"],
        title=p["title"],
        due_date=due,
        created_by=ctx.user_id,
        source_proposal_id=row["id"],
        at=ctx.now,
    )
    mention = f"<@{assignee['slack_user_id']}>" if assignee["slack_user_id"] else None
    msg = slack_blocks.task_created(
        assignee=assignee["display_name"],
        mention=mention,
        title=p["title"],
        account=pv["account"],
        due_display=pv.get("due_display"),
        requester=await _requester(ctx),
    )
    await _enqueue_slack(ctx, row["id"], p["channel"], msg)
    return {"task_id": tid, "assignee": assignee["display_name"]}, [
        "task_created",
        f"slack_post:{p['channel']}",
    ]


def _changed_since_preview(account: str, what: str) -> WatchfloorError:
    return WatchfloorError(
        ErrorCode.INVALID_ACTION,
        f"{account}'s {what} changed after that preview, so nothing was changed.",
        recovery="Ask me to propose it again with the current details.",
    )


async def _exec_reassign(ctx: ServiceContext, row: Any) -> tuple[dict[str, Any], list[str]]:
    p, pv = row["params"], row["preview"]
    locked = await deals.lock(ctx.conn, row["deal_id"])
    if locked is None or locked["stage"] in ("won", "lost"):
        raise WatchfloorError(ErrorCode.INVALID_ACTION, "That deal is closed, so its owner can't change.")
    new_owner = await users.get(ctx.conn, p["new_owner_id"])
    if new_owner is None:
        raise WatchfloorError(ErrorCode.UNKNOWN_ASSIGNEE, "That teammate is no longer in the workspace.")
    card = await deals.get_card(ctx.conn, row["deal_id"])
    # Approval covers the change the preview showed, not whatever the deal looks like now. Proposals
    # stored before from_owner_id existed skip the check.
    if p.get("from_owner_id") and card["owner_id"] != p["from_owner_id"]:
        raise _changed_since_preview(pv["account"], "owner")
    await deals.apply_fields(
        ctx.conn, row["deal_id"], {"owner_id": p["new_owner_id"]}, changed_by=ctx.user_id, at=ctx.now
    )
    await scoring.recompute(
        ctx.conn, workspace_id=ctx.ws, now=ctx.now, today=ctx.today, deal_ids=[row["deal_id"]]
    )
    msg = slack_blocks.owner_reassigned(
        account=pv["account"],
        deal_name=card["name"],
        old_owner=card["owner"],
        new_owner=new_owner["display_name"],
        requester=await _requester(ctx),
    )
    await _enqueue_slack(ctx, row["id"], p["channel"], msg)
    return {"new_owner": new_owner["display_name"]}, ["owner_changed", f"slack_post:{p['channel']}"]


async def _exec_notify(ctx: ServiceContext, row: Any) -> tuple[dict[str, Any], list[str]]:
    p, pv = row["params"], row["preview"]
    rs = await current_score(ctx, row["deal_id"])
    msg = slack_blocks.team_note(
        account=pv["account"],
        score=rs["score"],
        band=rs["band"],
        message=p["message"],
        requester=await _requester(ctx),
    )
    await _enqueue_slack(ctx, row["id"], p["channel"], msg)
    return {"channel": p["channel"]}, [f"slack_post:{p['channel']}"]


async def _exec_draft(ctx: ServiceContext, row: Any) -> tuple[dict[str, Any], list[str]]:
    p, pv = row["params"], row["preview"]
    oid = await outbox.enqueue(
        ctx.conn,
        workspace_id=ctx.ws,
        kind="draft_followup",
        payload={
            "proposal_id": row["id"],
            "deal_id": row["deal_id"],
            "user_id": ctx.user_id,
            "account": pv["account"],
            "intent": p["intent"],
        },
        dedupe_key=f"pr:{row['id']}",
        at=ctx.now,
    )
    if oid is not None:
        ctx.outbox_ids.append(oid)
    return {"draft": "queued"}, ["draft_dm"]


async def _exec_update_fields(ctx: ServiceContext, row: Any) -> tuple[dict[str, Any], list[str]]:
    p = row["params"]
    locked = await deals.lock(ctx.conn, row["deal_id"])
    if locked is None or locked["stage"] in ("won", "lost"):
        raise WatchfloorError(ErrorCode.INVALID_ACTION, "That deal is closed, so it can't be updated.")
    card = await deals.get_card(ctx.conn, row["deal_id"])
    if "from_close_date" in p and card["close_date"].isoformat() != p["from_close_date"]:
        raise _changed_since_preview(row["preview"]["account"], "close date")
    if "from_amount_cents" in p and card["amount_cents"] != p["from_amount_cents"]:
        raise _changed_since_preview(row["preview"]["account"], "amount")
    changes: dict[str, Any] = {}
    if "close_date" in p:
        changes["close_date"] = date.fromisoformat(p["close_date"])
    if "amount_cents" in p:
        changes["amount_cents"] = int(p["amount_cents"])
    await deals.apply_fields(ctx.conn, row["deal_id"], changes, changed_by=ctx.user_id, at=ctx.now)
    out = await scoring.recompute(
        ctx.conn, workspace_id=ctx.ws, now=ctx.now, today=ctx.today, deal_ids=[row["deal_id"]]
    )
    ch = out[row["deal_id"]]
    return (
        {"fields": sorted(changes), "score_after": ch.score_after, "band_after": ch.band_after},
        ["deal_updated", "risk_recomputed"],
    )


EXECUTORS = {
    "create_task": _exec_create_task,
    "reassign_owner": _exec_reassign,
    "notify_channel": _exec_notify,
    "draft_followup": _exec_draft,
    UPDATE_FIELDS: _exec_update_fields,
}
