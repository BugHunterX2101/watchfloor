"""Standing orders (DD §9): deterministic descriptions, CRUD, and watcher evaluation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg

from ..errors import ErrorCode, WatchfloorError
from ..models import tools as m
from ..repos import deals, outbox, prefs, rules, scores
from ..speech import health, join_words, money_display, plural, stage_label
from . import slack_blocks
from .context import ServiceContext

MAX_ACTIVE_RULES = 10
SEGMENT_WORDS = {"smb": "SMB", "mid": "mid-market", "enterprise": "enterprise"}


def describe_rule(conditions: dict[str, Any]) -> str:
    """Plain-language rule text, generated without an LLM and read back to the user.

    >>> describe_rule({"min_amount_cents": 5_000_000, "quiet_days": 5})
    'Flag deals of $50K or more with no reply for 5 days.'

    "Or more", not "over": the match is at or above the amount (``rules.matches``), so a deal of exactly
    $50,000, a common round figure, fires, and the read-back must say so.
    """
    subject = []
    if conditions.get("segment"):
        subject.append(SEGMENT_WORDS[conditions["segment"]])
    if conditions.get("stages"):
        subject.append(" or ".join(stage_label(s) for s in conditions["stages"]))
    subject.append("deals")
    if conditions.get("min_amount_cents") is not None:
        subject.append(f"of {money_display(conditions['min_amount_cents'])} or more")
    clauses = []
    if conditions.get("min_score") is not None:
        # Stored and matched as RIGS risk; said as health (ADR-020): risk 70 or higher is health 30 or lower.
        clauses.append(f"health of {health(conditions['min_score'])} or lower")
    if conditions.get("quiet_days") is not None:
        clauses.append(f"no reply for {plural(conditions['quiet_days'], 'day')}")
    text = "Flag " + " ".join(subject)
    if clauses:
        text += " with " + join_words(clauses)
    return text + "."


def conditions_from(args: m.SetRuleIn) -> dict[str, Any]:
    return {
        "min_amount_cents": args.min_amount_usd * 100 if args.min_amount_usd is not None else None,
        "quiet_days": args.quiet_days,
        "stages": list(args.stages) if args.stages else None,
        "min_score": args.min_score,
        "segment": args.segment,
    }


async def create_rule(ctx: ServiceContext, args: m.SetRuleIn) -> m.SetRuleOut:
    channel = args.channel or (await prefs.get(ctx.conn, ctx.user_id, ctx.ws))["alert_channel"]
    conditions = conditions_from(args)
    rule = {"version": 1, "conditions": conditions, "channel": channel}
    # The same order twice (a repeated request, a replayed turn) is one standing order, not two alerts.
    same = await rules.find_active(ctx.conn, ctx.user_id, rule)
    if same is not None:
        return m.SetRuleOut(
            summary=f"You already have that standing order: {same['description']} Nothing changed.",
            rule_id=same["id"],
            description=same["description"],
            channel=channel,
        )
    if await rules.count_active(ctx.conn, ctx.user_id) >= MAX_ACTIVE_RULES:
        raise WatchfloorError(
            ErrorCode.RULE_LIMIT_REACHED,
            f"You already have {MAX_ACTIVE_RULES} standing orders, the maximum.",
            recovery="Remove one first; ask me what I'm watching for to hear them.",
        )
    description = describe_rule(conditions)
    rid = await rules.create(
        ctx.conn, workspace_id=ctx.ws, user_id=ctx.user_id, rule=rule, description=description, at=ctx.now
    )
    return m.SetRuleOut(
        summary=(
            f"Saved. I'll {description[0].lower()}{description[1:-1]}, checking every 15 minutes. "
            f"Matches go to {channel} and lead your next briefing."
        ),
        rule_id=rid,
        description=description,
        channel=channel,
    )


async def list_rules(ctx: ServiceContext) -> m.ListRulesOut:
    rows = await rules.list_active(ctx.conn, ctx.user_id)
    out = [
        m.RuleOut(
            rule_id=r["id"],
            description=r["description"],
            created_at=ctx.local_iso(r["created_at"]),
            last_triggered_at=ctx.local_iso(r["last_triggered_at"]) if r["last_triggered_at"] else None,
        )
        for r in rows
    ]
    if not out:
        summary = (
            "You have no standing orders. Ask me to keep watching for something, like big deals going quiet."
        )
    elif len(out) == 1:
        summary = f"You have one standing order: {out[0].description}"
    else:
        listed = " ".join(f"{n}. {r.description}" for n, r in enumerate(out, start=1))
        summary = f"You have {len(out)} standing orders. {listed}"
    return m.ListRulesOut(summary=summary, rules=out)


async def remove_rule(ctx: ServiceContext, args: m.RemoveRuleIn) -> m.RemoveRuleOut:
    row = await rules.deactivate(ctx.conn, ctx.user_id, args.rule_id, ctx.now)
    if row is None:
        raise WatchfloorError(
            ErrorCode.RULE_NOT_FOUND,
            "I couldn't find that standing order.",
            recovery="Ask me what I'm watching for to hear your active ones.",
        )
    return m.RemoveRuleOut(
        summary=f"Removed. I'll stop watching for this: {row['description']}",
        removed=True,
        description=row["description"],
    )


async def evaluate_rules(conn: asyncpg.Connection, *, workspace_id: str, now: datetime) -> int:
    """Watcher steps 2-3 (DD §9.2): one alert per (rule, deal) per 24 h, delivered via the outbox."""
    queued = 0
    for rule in await rules.all_active(conn):
        spec = rule["rule"]
        matched = await rules.matches(conn, rule_id=rule["id"], conditions=spec["conditions"], now=now)
        for deal_id in matched:
            card = await deals.get_card(conn, deal_id)
            rs = await scores.get(conn, deal_id)
            if card is None or rs is None:
                continue
            await rules.record_alert(
                conn, workspace_id=workspace_id, rule_id=rule["id"], deal_id=deal_id, at=now
            )
            msg = slack_blocks.risk_alert(
                account=card["account"],
                deal_name=card["name"],
                score=rs["score"],
                band=rs["band"],
                amount_display=money_display(card["amount_cents"]),
                stage=card["stage"],
                owner=card["owner"],
                reasons=[r["text"] for r in rs["reasons"]],
                rule_description=rule["description"],
            )
            oid = await outbox.enqueue(
                conn,
                workspace_id=workspace_id,
                kind="slack_message",
                payload={"channel": spec.get("channel", "#deals-at-risk"), "message": msg},
                dedupe_key=f"{rule['id']}:{deal_id}:{now:%Y%m%d%H}",
                at=now,
            )
            if oid is not None:
                queued += 1
    return queued
