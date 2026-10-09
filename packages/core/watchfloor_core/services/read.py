"""Read-path services: briefing, explanation and lookup (PRD J1, J2; DD §6.4).

Hot path: deterministic, precomputed scores only, a handful of indexed queries.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from ..errors import ErrorCode, WatchfloorError
from ..models import tools as m
from ..repos import deals, prefs, rules, scores
from ..rigs_params import load_params
from ..speech import band_label, date_display, money_display, plural, short_name, stage_label
from . import scoring
from .context import ServiceContext

SEGMENT_WORDS = {"smb": "SMB", "mid": "mid-market", "enterprise": "enterprise"}


def _first_sentence(text: str | None) -> str:
    if not text:
        return "Risk signals detected."
    return text.split("; ")[0].rstrip(".") + "."


async def build_briefing(ctx: ServiceContext, args: m.BriefingIn) -> m.BriefingOut:
    conn = ctx.conn
    pr = await prefs.get(conn, ctx.user_id, ctx.ws)
    last = await prefs.last_briefing_at(conn, ctx.user_id)
    limit = args.limit or pr["briefing_limit"]
    segment = None if args.segment == "all" else (args.segment or pr["focus_segment"])
    include_amounts = pr["include_amounts"]

    rows = await scores.ranked_risks(conn, limit=limit + 10, segment=segment, since=last)
    fired = await rules.fired_since(conn, ctx.user_id, last)
    counts = await scores.attention_counts(conn, segment=segment, since=last)
    await prefs.touch_last_briefing(conn, ctx.user_id, ctx.now)

    flagged = {d for f in fired for d in f["deal_ids"]}
    # Rule-triggered deals lead among equal bands (DD §9.3); the sort is stable otherwise.
    ranked = sorted(rows, key=lambda r: (r["band"] != "at_risk", r["deal_id"] not in flagged))[:limit]

    deals_out = []
    for r in ranked:
        item: dict[str, Any] = {
            "deal_id": r["deal_id"],
            "name": r["name"],
            "account": r["account"],
            "stage": stage_label(r["stage"]),
            "owner": short_name(r["owner"]),
            "score": r["score"],
            "band": r["band"],
            "top_reason": r["top_reason"] or "Risk signals detected.",
            "new_since_last_briefing": bool(r["new_since_last_briefing"]),
        }
        if include_amounts:
            item["amount_cents"] = r["amount_cents"]
            item["amount_display"] = money_display(r["amount_cents"])
        deals_out.append(m.BriefingDeal(**item))

    triggered = [
        m.TriggeredRule(rule_id=f["rule_id"], description=f["description"], deal_ids=list(f["deal_ids"]))
        for f in fired
    ]
    attention, at_risk, new_count = counts["attention"], counts["at_risk"], counts["new_count"]
    seg_words = f" in {SEGMENT_WORDS[segment]}" if segment else ""

    if attention == 0:
        summary = (
            f"Nothing{seg_words} is at risk or on watch right now. "
            f"{plural(len(triggered), 'standing order')} fired since your last briefing. "
            "The next check runs within 15 minutes."
        )
    else:
        watch = attention - at_risk
        parts = [
            f"{plural(at_risk, 'deal')}{seg_words} {'is' if at_risk == 1 else 'are'} at risk"
            + (f" and {watch} more {'is' if watch == 1 else 'are'} on watch" if watch else "")
            + (f"; {new_count} new since your last briefing." if new_count else ".")
        ]
        if triggered:
            names_by_id = {r["deal_id"]: r["account"] for r in rows}
            for t in triggered[:2]:
                named = [n for n in (names_by_id.get(d) for d in t.deal_ids) if n]
                who = " and ".join(named[:2]) if named else plural(len(t.deal_ids), "deal")
                parts.append(f"Your standing order “{t.description}” flagged {who}.")
        top = deals_out[0]
        amount = f", {top.amount_display}" if top.amount_display else ""
        parts.append(f"Top risk: {top.account}{amount}. {_first_sentence(top.top_reason)}")
        summary = " ".join(parts)

    return m.BriefingOut(
        summary=summary,
        all_clear=attention == 0,
        generated_at=ctx.local_iso(ctx.now),
        new_count=new_count,
        attention_count=attention,
        triggered_rules=triggered,
        deals=deals_out,
    )


async def open_card(ctx: ServiceContext, deal_id: str) -> Any:
    card = await deals.get_card(ctx.conn, deal_id)
    if card is None:
        raise WatchfloorError(
            ErrorCode.DEAL_NOT_FOUND,
            "I couldn't find that deal.",
            recovery="Ask me to look it up by company name.",
        )
    if card["stage"] in ("won", "lost"):
        raise WatchfloorError(
            ErrorCode.DEAL_NOT_FOUND,
            f"{card['account']} is already closed as {card['stage']}, so it has no live risk score.",
            recovery="Ask about an open deal instead.",
        )
    return card


def deal_card(card: Any, *, include_amounts: bool = True) -> m.DealCard:
    out: dict[str, Any] = {
        "deal_id": card["id"],
        "account_id": card["account_id"],
        "name": card["name"],
        "account": card["account"],
        "stage": stage_label(card["stage"]),
        "owner": short_name(card["owner"]),
        "close_date": card["close_date"].isoformat(),
        "close_date_display": date_display(card["close_date"]),
    }
    if include_amounts:
        out["amount_cents"] = card["amount_cents"]
        out["amount_display"] = money_display(card["amount_cents"])
    if card["next_step"]:
        out["next_step"] = card["next_step"]
    if card["next_step_date"]:
        out["next_step_date"] = card["next_step_date"].isoformat()
    return m.DealCard(**out)


async def current_score(ctx: ServiceContext, deal_id: str) -> Any:
    rs = await scores.get(ctx.conn, deal_id)
    if rs is None:  # a deal not yet scored is scored now (milliseconds)
        await scoring.recompute(
            ctx.conn, workspace_id=ctx.ws, now=ctx.now, today=ctx.today, deal_ids=[deal_id]
        )
        rs = await scores.get(ctx.conn, deal_id)
    return rs


async def explain_deal(ctx: ServiceContext, args: m.ExplainIn) -> m.ExplainOut:
    card = await open_card(ctx, args.deal_id)
    rs = await current_score(ctx, args.deal_id)
    p = load_params()
    reasons = rs["reasons"]
    top, rest = reasons[:3], reasons[3:]
    trend = await scores.trend(ctx.conn, args.deal_id, since_day=ctx.today - timedelta(days=13))
    if not trend or trend[-1] != rs["score"]:
        trend = (trend + [rs["score"]])[-14:]
    band = rs["band"]
    lead = f"{card['account']} is {band_label(band)} at {rs['score']}."
    detail = " ".join(r["text"] for r in top) if top else "No risk signals right now."
    return m.ExplainOut(
        summary=f"{lead} {detail}",
        deal=deal_card(card),
        risk=m.RiskBlock(
            score=rs["score"],
            band=band,
            band_since=ctx.local_iso(rs["band_since"]),
            components={"r": float(rs["r"]), "i": float(rs["i"]), "g": float(rs["g"]), "s": float(rs["s"])},
            weights={"r": p.weights.r, "i": p.weights.i, "g": p.weights.g, "s": p.weights.s},
            model_version=rs["model_version"],
            computed_at=ctx.local_iso(rs["computed_at"]),
        ),
        reasons=[
            m.ReasonOut(
                code=r["code"],
                factor=r["factor"],
                contribution=round(r["contribution"], 3),
                text=r["text"],
                evidence_ids=r["evidence_ids"],
            )
            for r in top
        ],
        other_findings=[m.FindingOut(code=r["code"], factor=r["factor"], text=r["text"]) for r in rest],
        trend_14d=trend,
    )


async def lookup_deal(ctx: ServiceContext, args: m.LookupIn) -> m.LookupOut:
    rows = await deals.find_by_name(ctx.conn, args.name_query, owner=args.owner_name, limit=3)
    if not rows:
        near = await deals.find_by_name(ctx.conn, args.name_query, owner=None, limit=3, threshold=0.15)
        raise WatchfloorError(
            ErrorCode.DEAL_NOT_FOUND,
            f"I couldn't find an open deal called “{args.name_query}”.",
            recovery="Did you mean one of these?"
            if near
            else "Try the company name as it appears in your CRM.",
            suggestions=[{"deal_id": r["deal_id"], "name": r["name"]} for r in near],
        )
    cands = [
        m.Candidate(
            deal_id=r["deal_id"],
            name=r["name"],
            account=r["account"],
            amount_display=money_display(r["amount_cents"]),
            owner=short_name(r["owner"]),
            stage=stage_label(r["stage"]),
            match_score=round(float(r["match_score"]), 2),
        )
        for r in rows
    ]
    if len(cands) == 1:
        c = cands[0]
        summary = f"Found {c.name}: {c.amount_display}, {c.stage}, owned by {c.owner}."
    else:
        options = "; ".join(f"{c.name} ({c.amount_display}, {c.owner})" for c in cands)
        summary = f"I found {len(cands)} matching deals: {options}. Which one do you mean?"
    return m.LookupOut(summary=summary, candidates=cands)
