"""Offline conversation engine: a keyword router, for local and test runs without a model id.

It is not a language model and never pretends to be: the API labels every turn
it answers with ``"engine": "offline"``. It exists so the harness, the OAuth flow
and the real MCP tools can be exercised end to end on a laptop. Replies are the
tools' own ``summary`` fields, which are written to be spoken (DD §6.3). It never
proposes a change, but a "yes" confirms a preview made in an earlier turn, and
"undo" reverses the action just confirmed.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

from .mcp_http import McpHttp

if TYPE_CHECKING:
    from .agent import Pending
    from .control import Screen

# The whole message is a plain yes ("Yes, go ahead."), so "yes, but change the title" is not one.
YES = re.compile(
    r"\s*(?:yes|yeah|yep|sure|ok|okay|go ahead|do it|confirm)"
    r"(?:[\s,.!]+(?:yes|please|go ahead|do it|thanks|thank you))*[\s,.!]*",
    re.IGNORECASE,
)
UNDO = re.compile(r"\b(undo|take (it|that) back|reverse (it|that))\b", re.IGNORECASE)
STANDING = re.compile(r"standing orders?|my (rules|alerts)|what am i watching", re.IGNORECASE)
# "Where do I start?": the briefing's first deal, which the server ranks by band and deal value.
PRIORITY = re.compile(
    r"\b(what should i (do|tackle|handle|look at) first|where (do|should) i start|most urgent|"
    r"biggest (risk|problem)|top (deal|risk|priority)|first priority)\b",
    re.IGNORECASE,
)
RESULT = re.compile(r"\b(results?|finished|came back|outcome)\b", re.IGNORECASE)
TRIAGE = re.compile(r"\b(deep|triage|review|dig into|look into)\b", re.IGNORECASE)
EXPLAIN = re.compile(r"\b(why|explain|what'?s wrong|how is|how's|tell me about)\b", re.IGNORECASE)
STOP = frozenset(
    """a an the is are was be at on in of for to with about me my our please can you could would
    why what whats what's wrong how hows how's doing explain tell deal deals risk risky at-risk
    deep triage review run start do dig into look results result finished came back outcome
    did go going show give status on it that this""".split()  # noqa: SIM905 - easier to read and extend
)
WORD = re.compile(r"[A-Za-z0-9&][A-Za-z0-9&'.-]*")
# "the second one", "the top deal", "number three": a place on the board the user is looking at.
PLACE = re.compile(
    r"\b(?:the\s+)?(first|second|third|fourth|fifth|top|last)\s+(?:one|deal|account)\b"
    r"|\b(?:deal\s+)?number\s+(one|two|three|four|five|[1-5])\b",
    re.IGNORECASE,
)
PLACES = {"first": 1, "top": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "last": -1,
          "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}  # fmt: skip


def screen_deal(message: str, screen: Screen | None) -> str | None:
    """The deal id a place on the board names ("the second one"), when the message names one."""
    m = PLACE.search(message)
    if not m or screen is None or not screen.board:
        return None
    word = (m.group(1) or m.group(2)).lower()
    n = PLACES.get(word) or int(word)
    deal = screen.board[-1] if n == -1 else (screen.board[n - 1] if n <= len(screen.board) else None)
    return deal.id if deal else None


def deal_words(message: str) -> str:
    """The words left once intent words are removed: the deal or company name, hopefully."""
    words = [w.removesuffix("'s").strip(".'") for w in WORD.findall(message)]
    return " ".join(w for w in words if w and w.lower() not in STOP)[:80]


def recent_deal(history: Sequence[tuple[str, str]]) -> str:
    """The deal the user last named, for a follow-up like "Dig into it." (the board suggests it)."""
    for role, text in reversed(history):
        if role == "user" and (TRIAGE.search(text) or EXPLAIN.search(text) or RESULT.search(text)):
            words = deal_words(text)
            if len(words) >= 2:
                return words
    return ""


async def offline_turn(
    mcp: McpHttp,
    message: str,
    history: Sequence[tuple[str, str]] = (),
    pending: Pending | None = None,
    screen: Screen | None = None,
) -> str:
    """``pending``: the preview waiting for a yes, or the action just confirmed (agent.Pending).

    ``screen``: what the page shows. "This deal" means the open deal sheet and "the second one" the
    second deal on the board, so neither needs a name (the page's ids, never a guess).
    """
    if pending and pending.state == "preview" and YES.fullmatch(message):
        return (await mcp.call_tool("confirm_action", {"confirmation_token": pending.token})).summary
    if pending and pending.state == "confirmed" and UNDO.search(message):
        return (await mcp.call_tool("undo_action", {"confirmation_token": pending.token})).summary
    if STANDING.search(message):
        return (await mcp.call_tool("list_standing_orders", {})).summary
    if PRIORITY.search(message):
        return (await mcp.call_tool("get_risk_briefing", {"limit": 1})).summary
    if not (TRIAGE.search(message) or EXPLAIN.search(message) or RESULT.search(message)):
        return (await mcp.call_tool("get_risk_briefing", {})).summary

    deal_id = screen_deal(message, screen)
    if deal_id is None and PLACE.search(message):
        # "The third one" with nothing there: say so, rather than look up a company called "third one".
        return (
            "There isn't a deal in that place on the board."
            if screen is not None and screen.board
            else "I can't tell which deal that is. Open the pipeline, or say the deal's name."
        )
    query = "" if deal_id else deal_words(message)
    if not deal_id and len(query) < 2 and screen is not None and screen.open_deal:
        deal_id = screen.open_deal  # "dig into this" with a deal sheet open
    if not deal_id and len(query) < 2:
        query = recent_deal(history)
    if not deal_id and len(query) < 2:
        return "Which deal do you mean?"
    if not deal_id:
        found = await mcp.call_tool("lookup_deal", {"name_query": query})
        candidates = (found.structured or {}).get("candidates") or []
        if not found.ok or not candidates:
            return found.summary or f"I couldn't find a deal called {query}."
        if len(candidates) > 1:  # no clear winner: the server's summary asks which; never review a guess
            return found.summary
        deal_id = candidates[0]["deal_id"]
    if RESULT.search(message):
        call = await mcp.call_tool("get_triage_result", {"deal_id": deal_id})
    elif TRIAGE.search(message):
        call = await mcp.call_tool("start_deep_triage", {"deal_id": deal_id})
    else:
        call = await mcp.call_tool("explain_deal_risk", {"deal_id": deal_id})
    return call.summary
