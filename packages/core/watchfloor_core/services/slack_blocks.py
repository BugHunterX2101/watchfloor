"""Slack Block Kit builders (DD §16). Pure functions; user-supplied text is escaped.

Messages are rendered when the outbox row is written, so the dispatcher only
delivers bytes and every message is reproducible from the database.
"""

from __future__ import annotations

from typing import Any

from ..speech import band_label, stage_label


def escape(text: str) -> str:
    """Escape Slack control characters and neutralise mention syntax in user text."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("@", "@​")


def _header(text: str) -> dict[str, Any]:
    return {"type": "header", "text": {"type": "plain_text", "text": text[:150], "emoji": False}}


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:3000]}}


def _context(text: str) -> dict[str, Any]:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text[:300]}]}


def risk_alert(
    *,
    account: str,
    deal_name: str,
    score: int,
    band: str,
    amount_display: str,
    stage: str,
    owner: str,
    reasons: list[str],
    rule_description: str | None,
) -> dict[str, Any]:
    label = band_label(band)
    bullets = "\n".join(f"• {escape(r)}" for r in reasons[:3]) or "• No specific reasons recorded."
    ctx = f"Standing order: _{escape(rule_description)}_ · Watchfloor" if rule_description else "Watchfloor"
    return {
        "text": f"{account} is {label} ({score})",
        "blocks": [
            _header(f"{label} · {account} ({score})"),
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Deal*\n{escape(deal_name)}"},
                    {"type": "mrkdwn", "text": f"*Amount*\n{amount_display}"},
                    {"type": "mrkdwn", "text": f"*Stage*\n{stage_label(stage)}"},
                    {"type": "mrkdwn", "text": f"*Owner*\n{escape(owner)}"},
                ],
            },
            _section(bullets),
            _context(ctx),
        ],
    }


def task_created(
    *, assignee: str, mention: str | None, title: str, account: str, due_display: str | None, requester: str
) -> dict[str, Any]:
    who = mention or escape(assignee)
    due = f" · due {due_display}" if due_display else ""
    return {
        "text": f"Task for {assignee}: {title}",
        "blocks": [
            _section(f"*Task for {who}:* {escape(title)}\n{escape(account)}{due}"),
            _context(f"Requested by {escape(requester)} by voice · confirmed in Watchfloor"),
        ],
    }


def owner_reassigned(
    *, account: str, deal_name: str, old_owner: str, new_owner: str, requester: str
) -> dict[str, Any]:
    return {
        "text": f"{account}: owner changed to {new_owner}",
        "blocks": [
            _section(
                f"*{escape(account)} · {escape(deal_name)}*\n"
                f"Owner changed from {escape(old_owner)} to {escape(new_owner)}."
            ),
            _context(f"Requested by {escape(requester)} · confirmed in Watchfloor"),
        ],
    }


def team_note(*, account: str, score: int, band: str, message: str, requester: str) -> dict[str, Any]:
    return {
        "text": f"{account}: {message}",
        "blocks": [
            _section(f"*{escape(account)}* · {band_label(band)} ({score})\n{escape(message)}"),
            _context(f"From {escape(requester)} via Watchfloor"),
        ],
    }


def triage_plan(
    *,
    account: str,
    score: int,
    band: str,
    voice_summary: str,
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    steps = (
        "\n".join(
            f"{n}. {escape(a['rationale'])} _(play {a['play_id']}, confidence {round(100 * a['confidence'])}%)_"
            for n, a in enumerate(actions[:3], start=1)
        )
        or "No actions recommended."
    )
    return {
        "text": f"Deep review: {account}",
        "blocks": [
            _header(f"Deep review · {account} ({score}, {band_label(band)})"),
            _section(escape(voice_summary)),
            _section(f"*Recommended next steps*\n{steps}"),
            _context("Say “Alexa, have Maya…” to act. Nothing happens without your confirmation."),
        ],
    }


def draft_message(*, account: str, draft: str, requester: str) -> dict[str, Any]:
    return {
        "text": f"DRAFT follow-up for {account}",
        "blocks": [
            _header(f"DRAFT · Follow-up for {account}"),
            _section(f"```{escape(draft)[:2800]}```"),
            _context(f"Drafted for {escape(requester)}. Not sent to anyone outside your team."),
        ],
    }
