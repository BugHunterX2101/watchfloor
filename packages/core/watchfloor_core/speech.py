"""Speakable formatting for money, dates and lists (DD §14.1, TD delta T3).

Every number or date we return has a ``*_display`` twin that reads well aloud
and on screen. Relative phrasing ("tomorrow") is left to Alexa+.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

STAGE_LABELS = {
    "discovery": "Discovery",
    "demo": "Demo",
    "proposal": "Proposal",
    "negotiation": "Negotiation",
    "closing": "Closing",
    "won": "Won",
    "lost": "Lost",
}

BAND_LABELS = {"at_risk": "At Risk", "watch": "Watch", "healthy": "Healthy"}


def money_display(cents: int) -> str:
    dollars = cents / 100
    if dollars >= 1_000_000:
        text = f"{dollars / 1_000_000:.1f}"
        return f"${text.removesuffix('.0')}M"
    if dollars >= 1_000:
        return f"${round(dollars / 1_000):,}K"
    return f"${dollars:,.0f}"


def date_display(d: date) -> str:
    return f"{d:%A}, {d:%B} {d.day}"


def short_date(d: date) -> str:
    return f"{d:%b} {d.day}"


def stage_label(stage: str) -> str:
    return STAGE_LABELS.get(stage, stage.title())


def band_label(band: str) -> str:
    return BAND_LABELS.get(band, band)


def health(risk_score: int) -> int:
    """The number people see and hear (ADR-020): deal health, 0–100, higher is healthier.

    RIGS still scores risk (higher is worse) and bands, rules and storage stay on that scale; health
    is its complement, as CRM deal-health scores read."""
    return 100 - risk_score


def times_word(n: int) -> str:
    return {1: "once", 2: "twice"}.get(n, f"{n} times")


def plural(n: int, word: str, plural_word: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural_word or word + 's')}"


def join_words(items: Sequence[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def short_name(display_name: str) -> str:
    """'Daniel Reyes' -> 'Daniel R.' (how teammates are shown on cards)."""
    parts = display_name.split()
    if len(parts) < 2:
        return display_name
    return f"{parts[0]} {parts[-1][0]}."
