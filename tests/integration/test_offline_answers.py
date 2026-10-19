"""The harness's offline router, answering from the real MCP server over the seeded database.

The unit tests use a fake server; here every answer is checked against what the database holds, so
a change in the tools' output or in the router cannot drift apart unnoticed. Needs the seeded DB.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from watchfloor_core.db import ws_tx
from watchfloor_core.speech import date_display, money_display, short_name

from tests.conftest import KESTREL, WS

# harness_api is its own uv project (not installed in the root env); its modules only need httpx.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "harness" / "api"))
from harness_api.mcp_http import McpHttp  # noqa: E402
from harness_api.offline import Focus, offline_answer  # noqa: E402

pytestmark = pytest.mark.usefixtures("seeded")

OPEN = "d.stage NOT IN ('won', 'lost')"


class Talk:
    """One conversation: history and focus carried from turn to turn, as the harness does."""

    def __init__(self, mcp: McpHttp) -> None:
        self.mcp = mcp
        self.history: list[tuple[str, str]] = []
        self.focus: Focus | None = None

    async def say(self, message: str) -> str:
        before = len(self.mcp.calls)
        reply, self.focus = await offline_answer(self.mcp, message, self.history, focus=self.focus)
        self.history += [("user", message), ("assistant", reply)]
        self.tools = [c.tool for c in self.mcp.calls[before:]]
        return reply


@pytest.fixture
async def talk(app: Any, user_token: str) -> AsyncIterator[Talk]:
    mcp = McpHttp(url="http://localhost/mcp", token=user_token, transport=httpx.ASGITransport(app=app))
    yield Talk(mcp)
    await mcp.aclose()


async def _band_totals(band: str) -> tuple[int, int]:
    async with ws_tx(WS) as conn:
        row = await conn.fetchrow(
            f"SELECT count(*) AS n, COALESCE(sum(d.amount_cents), 0) AS cents FROM risk_scores r "  # noqa: S608
            f"JOIN deals d ON d.id = r.deal_id WHERE r.band = $1 AND {OPEN}",
            band,
        )
    return int(row["n"]), int(row["cents"])


async def _deal(deal_id: str) -> dict[str, Any]:
    async with ws_tx(WS) as conn:
        row = await conn.fetchrow(
            "SELECT d.close_date, d.next_step, d.next_step_date, d.amount_cents, a.name AS account, "
            "u.display_name AS owner FROM deals d JOIN accounts a ON a.id = d.account_id "
            "JOIN users u ON u.id = d.owner_id WHERE d.id = $1",
            deal_id,
        )
    return dict(row)


async def test_the_totals_and_counts_are_the_databases(talk: Talk) -> None:
    n_risk, cents_risk = await _band_totals("at_risk")
    n_watch, cents_watch = await _band_totals("watch")
    reply = await talk.say("How much is at risk in total?")
    assert reply.startswith(f"{money_display(cents_risk)} is at risk, across {n_risk} deal")
    assert talk.tools == ["get_risk_briefing"]
    both = await talk.say("What's my total exposure?")
    assert f"{money_display(cents_watch)} is on watch, across {n_watch} deal" in both
    assert both.startswith(f"{money_display(cents_risk)} is at risk")
    on_watch = await talk.say("How much is on watch?")
    assert on_watch.startswith(f"{money_display(cents_watch)} is on watch, across {n_watch} deal")
    count = await talk.say("How many deals are at risk?")
    assert str(n_risk) in count and "get_risk_briefing" in talk.tools


async def test_one_deals_facts_are_the_databases(talk: Talk) -> None:
    db = await _deal(KESTREL)
    assert await talk.say("Who owns Kestrel?") == f"{short_name(db['owner'])} owns {db['account']}."
    assert await talk.say("And when does it close?") == (
        f"{db['account']} is expected to close on {date_display(db['close_date'])}."
    )
    nxt = await talk.say("What's the next step?")
    assert str(db["next_step"]).rstrip(".") in nxt
    assert (await talk.say("How much is it worth?")) == (
        f"{db['account']} is worth {money_display(db['amount_cents'])}."
    )
    assert "lookup_deal" not in talk.tools  # "it" is the deal in focus, not a new lookup


async def test_the_riskiest_deal_is_the_briefings_first(talk: Talk) -> None:
    reply = await talk.say("Where do I start?")
    first = talk.mcp.calls[0].structured["deals"][0]  # type: ignore[index]
    assert reply.startswith(f"Start with {first['account']}")
    assert first["band"] == "at_risk"


async def test_a_name_that_matches_several_deals_is_asked_about(talk: Talk) -> None:
    async with ws_tx(WS) as conn:
        account = await conn.fetchval(
            f"SELECT a.name FROM accounts a JOIN deals d ON d.account_id = a.id WHERE {OPEN} "  # noqa: S608
            "GROUP BY a.name HAVING count(*) > 1 ORDER BY a.name LIMIT 1"
        )
    if account is None:
        pytest.skip("the seed has no account with two open deals")
    reply = await talk.say(f"How much is {account} worth?")
    assert reply.endswith("Which one do you mean?")
    assert "explain_deal_risk" not in talk.tools  # never answered about a guess


async def test_a_company_that_is_not_there_says_so(talk: Talk) -> None:
    reply = await talk.say("Tell me about Zzyzx Holdings")
    assert "Zzyzx" in reply and "explain_deal_risk" not in talk.tools


async def test_a_request_to_change_something_changes_nothing(talk: Talk) -> None:
    async with ws_tx(WS) as conn:
        before = await conn.fetchval("SELECT count(*) FROM action_proposals")
    reply = await talk.say("Reassign Kestrel to Maya")
    assert "nothing was changed" in reply.lower()
    async with ws_tx(WS) as conn:
        after = await conn.fetchval("SELECT count(*) FROM action_proposals")
    assert after == before
    assert not {"propose_action", "confirm_action", "create_task"} & set(talk.tools)
