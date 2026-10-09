"""Deterministic triage for local and test runs without Bedrock.

It reads the same MCP tools the specialists would and maps the scored reasons
onto the playbook by trigger code, so the whole loop (dispatcher → agent → MCP
→ gate → stored result) runs on a laptop. It is never a silent fallback: the
runner only picks it when ``WF_ENV`` is ``local`` or ``test`` and no model ids
are set, and every result says ``"engine": "offline"``.
"""

from __future__ import annotations

from typing import Any

from watchfloor_core.speech import band_label

from .data_tools import JobData
from .schemas import DraftAction, DraftDriver, LeadDraft

HIGH_VALUE_CENTS = 5_000_000  # P03: At Risk and $50K or more
CONFIDENCE = 0.6
CONVERSATION = {"email", "call", "meeting"}


def triggers(job: JobData) -> dict[str, str]:
    """Trigger code → the reason text that raised it (DD §10.8)."""
    explain = job.explain or {}
    found: dict[str, str] = {}
    for r in [*explain.get("reasons", []), *explain.get("other_findings", [])]:
        found.setdefault(r["code"], r["text"])
    deal = explain.get("deal") or {}
    risk = explain.get("risk") or {}
    if risk.get("band") == "at_risk" and (deal.get("amount_cents") or 0) >= HIGH_VALUE_CENTS:
        found["AT_RISK_HIGH_VALUE"] = f"At Risk with {deal.get('amount_display', 'a large amount')} open"
    for a in job.activities:
        if "security review" in (a.get("summary") or "").lower():
            found["SECURITY_REVIEW"] = a["summary"]
            break
    return found


def _params(action_type: str, play: dict[str, Any], deal: dict[str, Any]) -> dict[str, str] | None:
    account = deal.get("account") or "this account"
    if action_type == "create_task":
        owner = (deal.get("owner") or "").split()
        if not owner:
            return None
        return {"assignee_name": owner[0], "title": f"{play['name']} for {account}"[:120]}
    if action_type == "draft_followup":
        return {"intent": play["guidance"][:200]}
    if action_type == "notify_channel":
        return {"message": f"{account}: {play['name']}. {play['guidance']}"[:300]}
    return None  # reassign_owner needs a teammate choice only a person can make


def _engagement_driver(job: JobData) -> DraftDriver | None:
    """The latest buyer conversation, cited as the Lead would when no reason has evidence.

    A healthy deal's reasons are often all qualification gaps, which no activity can cite, and a
    result needs at least one evidenced driver (DD §10.6). The claim is built from the activity's
    type and date only: its text is untrusted CRM content.
    """
    for a in job.activities:  # newest first
        if a.get("direction") == "inbound" and a.get("type") in CONVERSATION:
            claim = f"Latest buyer conversation: an inbound {a['type']} on {a['occurred_at'][:10]}."
            return DraftDriver(factor="R", claim=claim, evidence_ids=[a["activity_id"]])
    return None


def offline_triage(job: JobData) -> LeadDraft:
    if not job.activities:
        job.read("get_deal_timeline")
    explain = job.explain or {}
    deal = explain.get("deal") or {}
    risk = explain.get("risk") or {}

    drivers = [
        DraftDriver(factor=r["factor"], claim=r["text"], evidence_ids=r["evidence_ids"][:5])
        for r in explain.get("reasons", [])
        if r.get("evidence_ids")
    ][:3]
    fallback = None if drivers else _engagement_driver(job)

    fired = triggers(job)
    actions: list[DraftAction] = []
    for play in job.plays.values():
        matched = [t for t in play["triggers"] if t in fired]
        if not matched:
            continue
        for action_type in play["action_types"]:
            params = _params(action_type, play, deal)
            if params is not None:
                actions.append(
                    DraftAction(
                        action_type=action_type,
                        params=params,
                        rationale=f"{play['name']}: {fired[matched[0]]}",
                        play_id=play["id"],
                        confidence=CONFIDENCE,
                    )
                )
                break
        if len(actions) == 3:
            break

    account = deal.get("account") or "This deal"
    parts = [f"{account} is {band_label(risk.get('band', 'watch'))} at {risk.get('score', 0)}."]
    if drivers:
        parts.append(drivers[0].claim.rstrip(".") + ".")
    if actions:
        name = job.plays[actions[0].play_id]["name"]
        parts.append(f"Suggested next step: {name[0].lower()}{name[1:]}.")
    return LeadDraft(
        voice_summary=" ".join(parts),
        risk_drivers=drivers or ([fallback] if fallback else []),
        recommended_actions=actions,
        confidence=CONFIDENCE,
    )
