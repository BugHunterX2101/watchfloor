"""Offline conversation engine: a keyword router, for local and test runs without a model id.

It is not a language model and never pretends to be: the API labels every turn
it answers with ``"engine": "offline"``. It exists so the harness, the OAuth flow
and the real MCP tools can be exercised end to end on a laptop. Replies are the
tools' own ``summary`` fields, which are written to be spoken (DD §6.3).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from .mcp_http import McpHttp

STANDING = re.compile(r"standing orders?|my (rules|alerts)|what am i watching", re.IGNORECASE)
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


async def offline_turn(mcp: McpHttp, message: str, history: Sequence[tuple[str, str]] = ()) -> str:
    if STANDING.search(message):
        return (await mcp.call_tool("list_standing_orders", {})).summary
    if not (TRIAGE.search(message) or EXPLAIN.search(message) or RESULT.search(message)):
        return (await mcp.call_tool("get_risk_briefing", {})).summary

    query = deal_words(message)
    if len(query) < 2:
        query = recent_deal(history)
    if len(query) < 2:
        return "Which deal do you mean?"
    found = await mcp.call_tool("lookup_deal", {"name_query": query})
    candidates = (found.structured or {}).get("candidates") or []
    if not found.ok or not candidates:
        return found.summary or f"I couldn't find a deal called {query}."
    deal_id = candidates[0]["deal_id"]
    if RESULT.search(message):
        call = await mcp.call_tool("get_triage_result", {"deal_id": deal_id})
    elif TRIAGE.search(message):
        call = await mcp.call_tool("start_deep_triage", {"deal_id": deal_id})
    else:
        call = await mcp.call_tool("explain_deal_risk", {"deal_id": deal_id})
    return call.summary
