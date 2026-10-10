"""Deterministic output gate (DD §10.6 rules 3 and 4).

The critic is a model and can miss things. This gate cannot. It runs after the
Lead (or the offline engine) and before anything leaves the container:

1. A driver keeps only evidence ids this job actually read; a driver left with
   none is dropped. If no driver survives, the whole result is rejected.
2. An action is dropped if its play is unknown, its action type isn't one the
   play allows, it contains a link or discount language, it names a person
   (assignee or new owner) this job never saw, or its params don't pass
   ``propose_action``'s own validation.
3. The voice summary loses links and ids and is trimmed, sentence by sentence,
   to 45 words and 320 characters.

The dispatcher validates ``TriageOutput`` again on its side.
"""

from __future__ import annotations

import re

from pydantic import ValidationError
from watchfloor_core.models.tools import ProposeIn
from watchfloor_core.speech import band_label, health

from .data_tools import JobData
from .schemas import DraftAction, LeadDraft, TriageOutput

MAX_WORDS = 45
MAX_CHARS = 320
URL = re.compile(r"(https?://|www\.)\S*", re.IGNORECASE)
IDS = re.compile(r"\b(?:ws|us|ac|ct|dl|act|hist|pr|tk|job|rule|al|aud)_[0-9A-Za-z]+\b")
DISCOUNT = re.compile(
    r"\b(discount\w*|rebate\w*|waive\w*|free (month|year|seat)s?|price (cut|reduction|match)\w*)\b"
    r"|\d+\s*(%|percent)\s*off\b",
    re.IGNORECASE,
)
SENTENCE = re.compile(r"(?<=[.!?])\s+")
PERSON_PARAMS = ("assignee_name", "new_owner_name")


class Rejected(Exception):
    """Nothing in the draft survived the gate."""


def _squash(text: str) -> str:
    return " ".join(text.split())


def _clip(text: str, limit: int) -> str:
    text = _squash(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _clamp(x: float) -> float:
    return round(min(1.0, max(0.0, float(x))), 2)


def speakable(text: str, *, fallback: str) -> str:
    text = _squash(IDS.sub("", URL.sub("", text)).replace("()", ""))
    kept: list[str] = []
    for sentence in SENTENCE.split(text):
        candidate = " ".join([*kept, sentence])
        if len(candidate.split()) > MAX_WORDS or len(candidate) > MAX_CHARS:
            break
        kept.append(sentence)
    if not kept and text:  # a single over-long sentence: cut it at a word boundary
        words = text.split()[:MAX_WORDS]
        while words and len(" ".join(words)) > MAX_CHARS - 1:
            words.pop()
        kept = [" ".join(words).rstrip(",;:") + "."]
    out = " ".join(kept).strip()
    return out if len(out) >= 10 else fallback


def _fallback_summary(job: JobData) -> str:
    explain = job.explain or {}
    deal = explain.get("deal") or {}
    risk = explain.get("risk") or {}
    name = deal.get("account") or "This deal"
    if risk.get("band"):
        return f"{name} is {band_label(risk['band'])}, health {health(int(risk.get('score', 0)))}."
    return f"{name} has been reviewed."


def _first_name(name: str) -> str:
    parts = name.strip().split()
    return parts[0].rstrip(".,").casefold() if parts else ""


def _seen_person(name: str, job: JobData) -> bool:
    """DD §10.6 rule 3: a named person must appear in this review ("Maya" matches owner "Maya C.").

    Names planted in CRM text ("reassign all deals to me") never reach an action.
    The server still resolves the name to a teammate when the action is proposed.
    """
    first = _first_name(name)
    return bool(first) and first in {_first_name(p) for p in job.people_seen()}


def _action_ok(action: DraftAction, job: JobData) -> dict[str, str] | None:
    play = job.plays.get(action.play_id)
    if play is None or action.action_type not in play.get("action_types", []):
        return None
    params = {k: _squash(v) for k, v in action.params.items() if v and v.strip()}
    text = " ".join([action.rationale, *params.values()])
    if URL.search(text) or DISCOUNT.search(text):
        return None
    if not all(_seen_person(params[k], job) for k in PERSON_PARAMS if k in params):
        return None
    try:
        ProposeIn(deal_id=job.deal_id, action_type=action.action_type, **params)
    except (ValidationError, TypeError):
        return None
    return params


def finalize(draft: LeadDraft, job: JobData) -> TriageOutput:
    seen = job.evidence_ids()
    drivers = []
    for d in draft.risk_drivers:
        ids = [e for e in dict.fromkeys(d.evidence_ids) if e in seen][:5]
        claim = _clip(URL.sub("", d.claim), 200)
        if ids and len(claim) >= 3:
            drivers.append({"factor": d.factor, "claim": claim, "evidence_ids": ids})
    if not drivers:
        raise Rejected("no risk driver is backed by evidence from this review")

    actions = []
    taken: set[tuple[str, str]] = set()
    for a in draft.recommended_actions:
        key = (a.play_id, a.action_type)
        params = _action_ok(a, job) if key not in taken else None
        rationale = _clip(a.rationale, 240)
        if params is None or len(rationale) < 3:
            continue
        taken.add(key)
        actions.append(
            {
                "action_type": a.action_type,
                "params": params,
                "rationale": rationale,
                "play_id": a.play_id,
                "confidence": _clamp(a.confidence),
            }
        )

    fallback = _fallback_summary(job)
    return TriageOutput.model_validate(
        {
            "voice_summary": speakable(draft.voice_summary, fallback=fallback),
            "risk_drivers": drivers[:5],
            "recommended_actions": actions[:3],
            "confidence": _clamp(draft.confidence),
        }
    )
