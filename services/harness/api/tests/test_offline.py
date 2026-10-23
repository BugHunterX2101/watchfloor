"""The offline engine answers from tool data only: each reply below is built from a tool result, and
the figures are the seeded demo pipeline's (checked against the database, not against the engine).

The fake MCP server here is a small, independent model of get_risk_briefing, explain_deal_risk and
lookup_deal over seven open deals: it totals, ranks and filters the way the real tools do, so a wrong
total or a wrong owner in a reply shows up as a difference from these numbers.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from harness_api.agent import Pending
from harness_api.control import BoardDeal, Screen
from harness_api.mcp_http import McpHttp
from harness_api.offline import Focus, deal_words, offline_answer
from watchfloor_core.speech import money_display

KESTREL, HARBOR, ORBITAL, VALEWOOD, NORTHWIND, JUNIPER, NORTHWIND2 = (
    "dl_KESTREL",
    "dl_HARBOR",
    "dl_ORBITAL",
    "dl_VALEWOOD",
    "dl_NORTHWIND",
    "dl_JUNIPER",
    "dl_NORTHWIND2",
)


@dataclass
class Deal:
    id: str
    account: str
    name: str
    owner: str  # as the tools say it: "Daniel R."
    stage: str
    cents: int
    close: str  # ISO
    close_display: str
    band: str
    health: int
    segment: str
    next_step: str = ""
    next_step_date: str = ""
    band_since: str = "2026-10-15T15:54:10+00:00"
    trend: list[int] = field(default_factory=lambda: [100] * 14)  # risk scores, today last


# Ranked as the server ranks them: at risk first, then by score x amount.
DEALS = [
    Deal(KESTREL, "Kestrel Biotech", "Kestrel Biotech · Platform expansion", "Daniel R.", "Proposal",
         8_400_000, "2026-12-01", "Tuesday, December 1", "at_risk", 29, "mid",
         "Send revised proposal with security addendum", "2026-10-23", "2026-10-17T15:54:10+00:00",
         [23, 23, 35, 35, 36, 47, 50, 52, 55, 56, 59, 66, 69, 71]),
    Deal(HARBOR, "Harbor & Pine Logistics", "Harbor & Pine Logistics · Fleet rollout", "Maya C.", "Negotiation",
         5_200_000, "2026-11-10", "Tuesday, November 10", "at_risk", 34, "mid"),
    Deal(ORBITAL, "Orbital Dental Group", "Orbital Dental Group · Pilot", "Daniel R.", "Demo",
         3_100_000, "2026-11-23", "Monday, November 23", "watch", 42, "smb", trend=[58] * 14),
    Deal(VALEWOOD, "Valewood Analytics", "Valewood Analytics · Renewal", "Maya C.", "Proposal",
         2_400_000, "2026-12-09", "Wednesday, December 9", "watch", 37, "smb"),
    Deal(NORTHWIND, "Northwind Freight", "Northwind Freight · Multi-year agreement", "Marcus B.", "Closing",
         2_300_000, "2026-12-02", "Wednesday, December 2", "watch", 40, "smb"),
    Deal(JUNIPER, "Juniper Learning", "Juniper Learning · Add-on", "Priya N.", "Proposal",
         3_200_000, "2026-11-20", "Friday, November 20", "watch", 59, "smb", trend=[60] * 13 + [41]),
    Deal(NORTHWIND2, "Northwind Freight", "Northwind Freight · Site expansion", "Marcus B.", "Proposal",
         2_100_000, "2027-01-04", "Monday, January 4", "watch", 45, "smb"),
]  # fmt: skip
NOW = "2026-10-19T16:00:00+00:00"
COMPARISON = (
    "Mixed: more deals are at risk, but fewer need attention overall. At risk: 2 now, 1 a week ago (up 1). "
    "At risk or on watch: 7 now, 20 a week ago (down 13)."
)
TRIAGE_RUNNING = "The review of Kestrel Biotech is still running. Give it a few more seconds."


def _by(name: str) -> list[Deal]:
    q = name.lower().replace(" and ", " & ")
    return [d for d in DEALS if all(w in (d.account + " " + d.name).lower() for w in q.split())]


@dataclass
class PipelineMcp:
    """Answers tools/call over the seven deals above; records every call."""

    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    amounts: bool = True  # the include_amounts preference
    new: int = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        rpc = json.loads(request.content)
        name, args = rpc["params"]["name"], rpc["params"].get("arguments", {})
        self.calls.append((name, args))
        data = getattr(self, f"_{name}")(args)
        result: dict[str, Any] = {"content": [{"type": "text", "text": "ok"}], "structuredContent": data}
        if "error" in data:
            result["isError"] = True
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": rpc["id"], "result": result})

    def _get_risk_briefing(self, args: dict[str, Any]) -> dict[str, Any]:
        seg = args.get("segment")
        seg = None if seg == "all" else seg
        pool = [d for d in DEALS if seg is None or d.segment == seg]
        at = [d for d in pool if d.band == "at_risk"]
        watch = [d for d in pool if d.band == "watch"]
        words = {"smb": "SMB", "mid": "mid-market", "enterprise": "enterprise"}
        where = f" in {words[seg]}" if seg else ""
        if not pool:
            summary = f"Nothing{where} is at risk or on watch right now."
        else:
            summary = (
                f"{len(at)} deals{where} are at risk"
                + (f" and {len(watch)} more are on watch" if watch else "")
                + "."
            )
        out: dict[str, Any] = {
            "summary": summary,
            "all_clear": not pool,
            "generated_at": NOW,
            "new_count": self.new,
            "attention_count": len(pool),
            "at_risk_count": len(at),
            "triggered_rules": [],
            "deals": [
                {
                    "deal_id": d.id,
                    "name": d.name,
                    "account": d.account,
                    "stage": d.stage,
                    "owner": d.owner,
                    "health": d.health,
                    "band": d.band,
                    "top_reason": "x",
                    "new_since_last_briefing": bool(self.new) and d.id == KESTREL,
                    **(
                        {"amount_cents": d.cents, "amount_display": money_display(d.cents)}
                        if self.amounts
                        else {}
                    ),
                }
                for d in pool[: args.get("limit") or 3]
            ],  # fmt: skip
            "at_risk_amount_display": money_display(sum(d.cents for d in at))
            if at and self.amounts
            else None,
            "watch_amount_display": money_display(sum(d.cents for d in watch))
            if watch and self.amounts
            else None,
            "week_ago": {"comparison": COMPARISON} if seg is None else None,
        }
        if seg:
            out["segment"] = seg
        return out

    def _lookup_deal(self, args: dict[str, Any]) -> dict[str, Any]:
        found = _by(args["name_query"])
        if args.get("owner_name"):
            found = [d for d in found if d.owner.lower().startswith(args["owner_name"].lower())]
        if not found:
            message = f"I couldn't find an open deal called “{args['name_query']}”."
            return {
                "summary": f"{message} Try the company name as it appears in your CRM.",
                "error": {"code": "deal_not_found", "message": message, "suggestions": []},
            }
        cands = [
            {"deal_id": d.id, "name": d.name, "account": d.account, "owner": d.owner, "match_score": 1.0,
             "amount_display": money_display(d.cents)}
            for d in found
        ]  # fmt: skip
        if len(found) == 1:
            summary = f"Found {found[0].name}."
        else:
            summary = f"I found {len(found)} matching deals: {'; '.join(d.name for d in found)}. Which one do you mean?"
        return {"summary": summary, "candidates": cands}

    def _explain_deal_risk(self, args: dict[str, Any]) -> dict[str, Any]:
        d = next(d for d in DEALS if d.id == args["deal_id"])
        band = {"at_risk": "At Risk", "watch": "Watch"}[d.band]
        card = {
            "deal_id": d.id, "name": d.name, "account": d.account, "stage": d.stage, "owner": d.owner,
            "amount_cents": d.cents, "amount_display": money_display(d.cents), "close_date": d.close,
            "close_date_display": d.close_display,
        }  # fmt: skip
        if d.next_step:
            card |= {"next_step": d.next_step, "next_step_date": d.next_step_date}
        return {
            "summary": f"{d.account} is {band}, health {d.health}. Reason one. Reason two.",
            "deal": card,
            "risk": {"health": d.health, "band": d.band, "band_since": d.band_since},
            "reasons": [{"text": "Reason one."}, {"text": "Reason two."}],
            "trend_14d": d.trend,
        }

    def _start_deep_triage(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"summary": "I've started a deep review.", "job_id": "job_1", "deal_id": args["deal_id"]}

    def _get_triage_result(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"summary": TRIAGE_RUNNING, "status": "running", "deal_id": args["deal_id"]}

    def _list_standing_orders(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"summary": "You have no standing orders.", "rules": []}

    def _confirm_action(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"summary": "Done.", "undo_until": NOW}

    def _undo_action(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"summary": "Undone."}

    def _set_standing_order(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"summary": "Saved."}

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]


class Talk:
    """A conversation: history and focus carry from turn to turn, as the harness keeps them."""

    def __init__(self, *, amounts: bool = True, new: int = 0) -> None:
        self.mcp = PipelineMcp(amounts=amounts, new=new)
        self.history: list[tuple[str, str]] = []
        self.focus: Focus | None = None
        self.last_calls: list[tuple[str, dict[str, Any]]] = []

    async def say(self, message: str, *, screen: Screen | None = None, pending: Pending | None = None) -> str:
        before = len(self.mcp.calls)
        client = McpHttp("http://mcp.test/mcp", "token", transport=httpx.MockTransport(self.mcp))
        try:
            reply, self.focus = await offline_answer(
                client, message, self.history, pending, screen, self.focus
            )
        finally:
            await client.aclose()
        self.history += [("user", message), ("assistant", reply)]
        self.last_calls = self.mcp.calls[before:]
        return reply


# ---------------------------------------------------------------------------------------------------------
# The pipeline as a whole.


async def test_totals_come_from_the_briefings_own_figures() -> None:
    t = Talk()
    assert await t.say("How much is at risk in total?") == "$136K is at risk, across 2 deals."
    assert await t.say("How much money is at risk right now?") == "$136K is at risk, across 2 deals."
    assert await t.say("And on watch?") == "$131K is on watch, across 5 deals."
    assert await t.say("What's my total exposure?") == (
        "$136K is at risk, across 2 deals. Another $131K is on watch, across 5 deals."
    )
    assert set(t.mcp.names()) == {"get_risk_briefing"}  # nothing else is needed, so nothing else is asked


async def test_counts() -> None:
    t = Talk()
    assert await t.say("How many deals are at risk?") == "2 deals are at risk."
    assert await t.say("How many are on watch?") == "5 deals are on watch, worth $131K in total."
    assert await t.say("How many deals need my attention?") == "2 deals are at risk, and 5 more are on watch."


async def test_last_week_is_the_servers_comparison_word_for_word() -> None:
    t = Talk()
    for q in (
        "Is my pipeline healthier than last week?",
        "Better or worse than last week?",
        "How are we doing versus a week ago?",
    ):
        assert await t.say(q) == COMPARISON, q
    assert t.mcp.names() == ["get_risk_briefing"] * 3  # "versus" was not looked up as a company
    assert await t.say("Is the enterprise pipeline healthier than last week?") == (
        "I don't have a snapshot from a week ago in enterprise to compare with yet."
    )


async def test_new_since_the_last_briefing() -> None:
    assert await Talk().say("Anything new since yesterday?") == (
        "Nothing new since your last briefing. 2 deals are at risk, and 5 more are on watch."
    )
    got = await Talk(new=1).say("What's new?")
    assert got == "1 deal is new on the list since your last briefing: Kestrel Biotech."


async def test_the_top_five_and_the_ones_beyond_it() -> None:
    t = Talk()
    assert await t.say("Give me the top five risks.") == (
        "The top 5 of the 7 deals that need attention: First, Kestrel Biotech, $84K, at risk, health 29. "
        "Second, Harbor & Pine Logistics, $52K, at risk, health 34. Third, Orbital Dental Group, $31K, on watch, "
        "health 42. Fourth, Valewood Analytics, $24K, on watch, health 37. Fifth, Northwind Freight, $23K, "
        "on watch, health 40."
    )
    assert t.last_calls == [("get_risk_briefing", {"limit": 5})]
    got = await t.say("List the top 3")
    assert (
        got.startswith("The top 3 of the 7 deals that need attention: First, Kestrel") and "Fourth" not in got
    )
    assert "top five deals" in await t.say("Give me the top ten")


async def test_lists_by_band() -> None:
    t = Talk()
    assert await t.say("List the at-risk deals") == (
        "2 deals are at risk. First, Kestrel Biotech, $84K, at risk, health 29. "
        "Second, Harbor & Pine Logistics, $52K, at risk, health 34."
    )
    got = await t.say("Which deals are on watch?")
    assert got.startswith("5 deals are on watch. First, Orbital Dental Group") and "Fourth" not in got
    assert (
        "2 more are not shown" in got
    )  # only three of the five on-watch deals are among the top five overall


async def test_the_top_few_of_a_band_come_from_the_band_not_the_top_few_overall() -> None:
    t = Talk()
    got = await t.say("List the top two deals on watch")
    assert got == (
        "5 deals are on watch. First, Orbital Dental Group, $31K, on watch, health 42. "
        "Second, Valewood Analytics, $24K, on watch, health 37."
    )  # the top two overall are both at risk; asking for two is not "more than I can see"
    assert t.last_calls == [("get_risk_briefing", {"limit": 5})]


async def test_the_biggest_deal_is_the_largest_amount_not_the_first_listed() -> None:
    t = Talk()
    assert await t.say("What's the biggest deal at risk?") == (
        "The biggest deal at risk is Kestrel Biotech, at $84K: at risk, health 29."
    )
    got = await t.say("What's my biggest deal?")  # across what the briefing can show: the top five of seven
    assert got.startswith("Of the 5 riskiest deals, the biggest one is Kestrel Biotech, at $84K")
    assert "2 more are not in that list" in got


async def test_a_reps_deals_say_how_many_of_the_deals_were_checked() -> None:
    t = Talk()
    assert await t.say("Which deals does Maya own?") == (
        "Among the 5 riskiest deals, Maya C. owns Harbor & Pine Logistics ($52K, at risk, health 34) and "
        "Valewood Analytics ($24K, on watch, health 37). I can see only the top 5 of the 7 deals that need attention."
    )
    got = await t.say(
        "What deals does Priya own?"
    )  # her deal is sixth: not among the five, and the reply says so
    assert got.startswith("None of the 5 riskiest deals belong to Priya.") and "top 5 of the 7" in got
    assert (await t.say("Maya's deals")).startswith("Among the 5 riskiest deals, Maya C. owns")


async def test_a_company_with_an_apostrophe_s_is_not_taken_for_a_rep() -> None:
    t = Talk()
    got = await t.say("Show me Kestrel's deals")
    assert (
        got.startswith("Kestrel Biotech is At Risk, health 29.")
        and t.last_calls[-1][0] == "explain_deal_risk"
    )


async def test_deals_closing_in_a_window() -> None:
    t = Talk()
    assert await t.say("Which deals are closing this month?") == (
        "None of the 5 riskiest deals close this month. The soonest is Harbor & Pine Logistics, on Tuesday, "
        "November 10. I checked the top 5 of the 7 deals that need attention."
    )
    assert await t.say("Anything closing next month?") == (
        "Among the 5 riskiest deals, 2 deals close next month: Harbor & Pine Logistics, on Tuesday, November 10 "
        "and Orbital Dental Group, on Monday, November 23. I checked the top 5 of the 7 deals that need attention."
    )
    got = await t.say("Which deals close this quarter?")
    assert "5 deals close this quarter" in got
    assert (
        got.index("November 10")
        < got.index("November 23")
        < got.index("December 1")
        < got.index("December 9")
    )


async def test_deals_closing_in_a_window_named_by_a_month() -> None:
    t = Talk()
    assert await t.say("What's closing before November?") == (
        "None of the 5 riskiest deals close before November. The soonest is Harbor & Pine Logistics, on "
        "Tuesday, November 10. I checked the top 5 of the 7 deals that need attention."
    )
    in_november = (
        "Harbor & Pine Logistics, on Tuesday, November 10 and Orbital Dental Group, on Monday, November 23."
    )
    assert in_november in await t.say("Anything closing in November?")
    got = await t.say("Which deals close by the end of December?")
    assert "close by the end of December" in got and "December 9" in got


async def test_a_filter_on_amount() -> None:
    t = Talk()
    assert await t.say("Which at-risk deals are over 50K?") == (
        "Of the 2 deals that are at risk, 2 are over $50K: Kestrel Biotech, $84K and Harbor & Pine Logistics, $52K."
    )
    got = await t.say("How many deals are under fifty thousand?")
    assert "3 are under $50K" in got and "Orbital Dental Group, $31K" in got and "top 5 of the 7" in got
    assert (await t.say("Any deals over a hundred thousand?")).startswith(
        "None of the 5 deals that are at risk or on watch is over $100K."
    )
    assert "lookup_deal" not in t.mcp.names()  # "over", "50K" and "Any" are not company names


async def test_a_segment_is_asked_for_and_then_left() -> None:
    t = Talk()
    assert (await t.say("Just the enterprise deals.")).startswith("Nothing in enterprise is at risk")
    assert t.last_calls == [("get_risk_briefing", {"segment": "enterprise"})]
    assert await t.say("What about mid-market?") == "2 deals in mid-market are at risk."
    # a follow-up stays in the segment, and says so
    assert (
        await t.say("And how much is at risk in total?") == "$136K is at risk in mid-market, across 2 deals."
    )
    # a fresh question about the day starts from the user's own focus again
    assert (await t.say("What needs my attention today?")).startswith("2 deals are at risk and 5 more")
    assert t.last_calls == [("get_risk_briefing", {})]
    assert (await t.say("Show all segments")).startswith("Across all segments: ")


async def test_with_amounts_switched_off_no_total_is_made_up() -> None:
    t = Talk(amounts=False)
    assert await t.say("How much is at risk in total?") == (
        "Your briefing settings leave amounts out, so I can't total them. "
        "2 deals are at risk, and 5 more are on watch."
    )
    assert "can't tell which deal is biggest" in await t.say("What's the biggest deal?")
    assert "can't filter deals by amount" in await t.say("Deals over 50K?")


async def test_where_to_start_names_the_top_deal_with_its_reasons_and_next_step() -> None:
    t = Talk()
    assert await t.say("What should I do first?") == (
        "Start with Kestrel Biotech, $84K, owned by Daniel R.: it's at risk, health 29. Reason one. Reason two. "
        "The next step on record is: Send revised proposal with security addendum, due Friday, October 23."
    )
    assert t.last_calls == [("get_risk_briefing", {"limit": 5}), ("explain_deal_risk", {"deal_id": KESTREL})]


# ---------------------------------------------------------------------------------------------------------
# One deal, and the follow-ups that don't name it.


async def test_questions_about_one_deal_are_answered_from_its_card() -> None:
    t = Talk()
    assert await t.say("Who owns Orbital Dental?") == "Daniel R. owns Orbital Dental Group."
    assert await t.say("When does Harbor and Pine close?") == (
        "Harbor & Pine Logistics is expected to close on Tuesday, November 10."
    )
    assert await t.say("What stage is Valewood in?") == "Valewood Analytics is in the Proposal stage."
    assert await t.say("How big is the Juniper deal?") == "Juniper Learning is worth $32K."
    assert await t.say("What's the health of Kestrel?") == "Kestrel Biotech's health is 29, at risk."
    assert await t.say("What's the next step with Kestrel?") == (
        "The next step on record for Kestrel Biotech is: Send revised proposal with security addendum, due "
        "Friday, October 23."
    )
    assert await t.say("What's the next step for Harbor?") == (
        "There's no next step on record for Harbor & Pine Logistics."
    )


async def test_a_trend_is_two_weeks_of_health_not_a_guess() -> None:
    t = Talk()
    assert await t.say("Is Kestrel getting better or worse?") == (
        "Kestrel Biotech is getting worse: health 77 13 days ago, 29 now. It has been at risk since Saturday, "
        "October 17."
    )
    assert await t.say("Is Juniper improving?") == (
        "Juniper Learning is getting better: health 40 13 days ago, 59 now. It has been on watch since "
        "Thursday, October 15."
    )
    assert await t.say("Is Orbital Dental getting better or worse?") == (
        "Orbital Dental Group is holding steady at health 42, the same as 13 days ago. It has been on watch "
        "since Thursday, October 15."
    )


async def test_follow_ups_use_the_deal_just_discussed() -> None:
    t = Talk()
    assert (await t.say("Why is Kestrel at risk?")).startswith("Kestrel Biotech is At Risk, health 29.")
    assert await t.say("Who owns it, and when do they need to decide?") == (
        "Daniel R. owns Kestrel Biotech. It is expected to close on Tuesday, December 1."
    )
    assert await t.say("How much is it worth?") == "Kestrel Biotech is worth $84K."
    # Another deal takes over "it", and "what about" asks the same thing of it.
    assert await t.say("What about Harbor?") == "Harbor & Pine Logistics is worth $52K."
    assert await t.say("And who owns it?") == "Maya C. owns Harbor & Pine Logistics."
    assert await t.say("Dig into it.") == "I've started a deep review."
    assert t.last_calls == [("start_deep_triage", {"deal_id": HARBOR})]
    assert await t.say("What did the review find?") == TRIAGE_RUNNING
    assert t.last_calls == [("get_triage_result", {"deal_id": HARBOR})]


async def test_places_mean_the_list_just_read_out() -> None:
    t = Talk()
    await t.say("Give me the top five risks.")
    assert await t.say("Who owns the third one?") == "Daniel R. owns Orbital Dental Group."
    assert await t.say("And the fourth one?") == "Maya C. owns Valewood Analytics."  # same question, new deal
    assert await t.say("Why is the last one in trouble?") == (
        "Northwind Freight is Watch, health 40. Reason one. Reason two."
    )
    t2 = Talk()  # "the top one" after "where do I start"
    await t2.say("What should I do first?")
    assert await t2.say("Why is the top one in trouble?") == (
        "Kestrel Biotech is At Risk, health 29. Reason one. Reason two."
    )
    assert await t2.say("And the second one?") == (
        "Harbor & Pine Logistics is At Risk, health 34. Reason one. Reason two."
    )


async def test_a_why_and_a_fact_in_one_question_get_both_answers() -> None:
    got = await Talk().say("Why is Kestrel at risk and who owns it?")
    assert (
        got
        == "Kestrel Biotech is At Risk, health 29. Reason one. Reason two. Daniel R. owns Kestrel Biotech."
    )


async def test_the_board_and_the_open_sheet_settle_this_and_the_second_one() -> None:
    board = [
        BoardDeal(id=KESTREL, name="Kestrel", band="at_risk", health=29),
        BoardDeal(id=HARBOR, name="Harbor", band="at_risk", health=34),
    ]
    screen = Screen(page="pipeline", dialog="deal", open_deal=HARBOR, board=board)
    t = Talk()
    assert await t.say("Who owns this one?", screen=screen) == "Maya C. owns Harbor & Pine Logistics."
    assert await t.say("When does the first one close?", screen=screen) == (
        "Kestrel Biotech is expected to close on Tuesday, December 1."
    )
    assert (
        await t.say("What about the third one?", screen=screen)
        == "There isn't a deal in that place on the board."
    )
    assert "lookup_deal" not in t.mcp.names()


async def test_a_new_conversation_forgets_the_deal_it_was_about() -> None:
    t = Talk()
    await t.say("Why is Kestrel at risk?")
    t.history = []  # the page started over
    assert await t.say("Who owns it?") == "Which deal do you mean?"


async def test_several_matches_are_asked_about_never_guessed() -> None:
    t = Talk()
    got = await t.say("Tell me about Northwind")
    assert "I found 2 matching deals" in got and got.endswith("Which one do you mean?")
    assert t.mcp.names() == ["lookup_deal"]  # nothing was explained
    assert await t.say("Dig into the first one") == "I've started a deep review."  # the list just offered
    assert t.last_calls == [("start_deep_triage", {"deal_id": NORTHWIND})]


async def test_a_deal_that_isnt_there_says_so() -> None:
    got = await Talk().say("Why is Zanzibar at risk?")
    assert got.startswith("I couldn't find an open deal called “Zanzibar”.")


# ---------------------------------------------------------------------------------------------------------
# What it won't do, and what it says instead.


@pytest.mark.parametrize(
    ("message", "said"),
    [
        ("Have Maya book a call with their CFO by Thursday.", "Creating tasks and reassigning deals needs"),
        (
            "From now on, flag any deal over 50K that goes five days without a reply.",
            "Setting up a standing order",
        ),
        ("Only give me two deals from now on.", "Changing your briefing settings needs"),
        ("Don't read out amounts.", "Changing your briefing settings needs"),
        ("Kestrel is now ninety thousand.", "Logging a deal update needs"),
        ("Draft a follow-up to Kestrel about the security questions.", "Drafting a follow-up needs"),
        ("Stop watching my big deals rule.", "Removing a standing order needs"),
        ("Mark Kestrel as closed won.", "Watchfloor doesn't change a deal's stage"),
        # Heard in a live battery (2026-10-22): each was answered as if it were a question, or not followed.
        ("push the Kestrel close date to the end of november", "Logging a deal update needs"),
        ("change the Orbital amount to sixty five thousand", "Logging a deal update needs"),
        ("move the Harbor deal over to Maya", "Creating tasks and reassigning deals needs"),
        ("tell the team in slack that Kestrel needs help", "Creating tasks and reassigning deals needs"),
        ("stop alerting me about anything under fifty k", "Removing a standing order needs"),
    ],
)
async def test_changes_are_refused_plainly_and_nothing_is_called(message: str, said: str) -> None:
    t = Talk()
    got = await t.say(message)
    assert said in got and "othing was changed" in got
    assert t.mcp.calls == []


@pytest.mark.parametrize(
    "message",
    [
        "What should I watch for?",
        "Have you looked at Kestrel?",
        "Get Orbital Dental details",
        "Is the stage for Kestrel Proposal?",
        "When did they push the close date?",
        "What's the amount on Orbital?",
        "Who owns the Harbor deal?",
        "What's closing before November?",
    ],
)
async def test_questions_that_look_like_requests_are_still_questions(message: str) -> None:
    got = await Talk().say(message)
    assert "needs the assistant model" not in got and "Watchfloor doesn't change" not in got


async def test_help_thanks_greetings_and_small_talk() -> None:
    t = Talk()
    assert "without the assistant model" in await t.say("What can you do?")
    assert await t.say("Help") == await t.say("what can I ask")
    assert await t.say("Thanks, that's all") == "You're welcome."
    assert (await t.say("Good morning")).startswith("Good morning. 2 deals are at risk")
    assert (await t.say("Hi, why is Kestrel at risk?")).startswith("Hi. Kestrel Biotech is At Risk")
    assert await t.say("Thanks, now who owns Harbor?") == "Maya C. owns Harbor & Pine Logistics."


async def test_nonsense_is_not_looked_up_as_a_company() -> None:
    for q in ("uh the the blue one over there", "what is the weather", "asdf qwer"):
        t = Talk()
        assert (await t.say(q)).startswith("Sorry, I didn't follow that."), q
        assert t.mcp.names() in ([], ["lookup_deal"]), (
            q
        )  # at most one strict look, never an answer about a guess


async def test_common_words_are_never_a_company_name() -> None:
    """The fuzzy matcher scores "morning", "find" and "worth" against real deals; they must not be sent."""
    t = Talk()
    for q in ("What did you find?", "Good morning", "Is it worth it?", "Anything on fire?", "Dig into it."):
        await t.say(q)
    assert [a for n, a in t.mcp.calls if n == "lookup_deal"] == []


# ---------------------------------------------------------------------------------------------------------
# Yes, no, undo.


def _pending(state: str, **kw: Any) -> Pending:
    return Pending("tok_1", "A change.", state, time.time() + 60, **kw)  # type: ignore[arg-type]


async def test_a_yes_confirms_only_what_is_waiting() -> None:
    t = Talk()
    assert await t.say("Yes, please.", pending=_pending("preview")) == "Done."
    assert t.last_calls == [("confirm_action", {"confirmation_token": "tok_1"})]
    assert await t.say("Yes") == "There's nothing waiting for a yes right now."
    held = _pending("ask", tool="set_standing_order", args={"min_score": 65})
    assert await t.say("Yes.", pending=held) == "Saved."
    assert t.last_calls == [("set_standing_order", {"min_score": 65})]
    assert t.mcp.names().count("confirm_action") == 1


async def test_a_yes_with_a_change_of_mind_is_not_a_yes() -> None:
    t = Talk()
    got = await t.say("Yes, but make it Monday.", pending=_pending("preview"))
    assert "can't change the details" in got and t.mcp.calls == []


async def test_no_and_undo() -> None:
    t = Talk()
    assert await t.say("No, never mind.", pending=_pending("preview")) == (
        "Okay, I've dropped it. Nothing was changed."
    )
    assert await t.say("Cancel that.") == "Okay, nothing was changed."
    assert await t.say("Cancel that.", pending=_pending("confirmed")) == (
        "That's already done. Say undo to reverse it."
    )
    assert await t.say("Undo that.", pending=_pending("confirmed")) == "Undone."
    assert "nothing I can undo" in await t.say("Undo that.")
    assert t.mcp.names() == ["undo_action"]


# ---------------------------------------------------------------------------------------------------------
# The cookie's contents are checked, and the words are read the way the tests above assume.


def test_a_focus_from_a_cookie_is_checked_before_it_can_reach_a_tool() -> None:
    f = Focus.from_dict(
        {
            "d": "dl_OK1",
            "l": ["dl_A", "x; drop table", 7, "dl_B"],
            "f": "yes",
            "k": "owner,evil",
            "g": "elsewhere",
        }
    )
    assert f is not None
    assert (f.deal, f.listed, f.fresh, f.kind, f.segment) == (
        "dl_OK1",
        ["dl_A", "dl_B"],
        False,
        "owner",
        None,
    )
    assert Focus.from_dict(None) is None
    assert Focus.from_dict({"d": "../../etc"}) == Focus()
    whole = Focus(deal="dl_X", listed=["dl_X"], fresh=True, kind="total", segment="mid")
    assert Focus.from_dict(whole.to_dict()) == whole


def test_deal_words_keep_the_name_and_drop_the_question() -> None:
    assert deal_words("Why is Kestrel Biotech's deal at risk?") == "Kestrel Biotech"
    assert deal_words("How's Harbor and Pine doing?") == "Harbor and Pine"
    assert deal_words("Who owns it, and when do they need to decide?") == ""
    assert deal_words("deals over 50K") == ""
    assert deal_words("Show me Harbor & Pine") == "Harbor & Pine"


async def test_how_much_is_on_the_line_is_a_pipeline_question_not_a_company() -> None:
    t = Talk()
    assert await t.say("How much is on the line?") == (
        "$136K is at risk, across 2 deals. Another $131K is on watch, across 5 deals."
    )
    assert t.mcp.names() == ["get_risk_briefing"]


async def test_what_to_watch_for_and_how_is_everything_get_the_briefing() -> None:
    for q in ("What should I watch for?", "What should I look out for?", "How is everything?"):
        t = Talk()
        assert (await t.say(q)).startswith("2 deals are at risk and 5 more are on watch"), q
        assert t.mcp.names() == ["get_risk_briefing"], q


async def test_a_place_without_a_noun_is_still_a_place() -> None:
    t = Talk()
    await t.say("Give me the top five risks.")
    got = await t.say("And the third?")
    assert got.startswith("Orbital Dental Group is Watch")
    assert (await t.say("and the last")).startswith("Northwind Freight is Watch")
    assert (await t.say("What about the second?")).startswith("Harbor & Pine Logistics is At Risk")


async def test_everything_about_a_deal_adds_the_next_step_and_the_trend() -> None:
    got = await Talk().say("Tell me everything about Kestrel")
    assert "It's an $84K deal in Proposal, owned by Daniel R." in got
    assert "next step on record" in got and "health" in got.split("next step on record")[1]


async def test_a_deal_asked_about_with_a_window_says_whether_it_falls_in_it() -> None:
    t = Talk()
    assert await t.say("When does Harbor close this month?") == (
        "Harbor & Pine Logistics is expected to close on Tuesday, November 10. That is not in this month."
    )
    assert (await t.say("Does Harbor close next month?")).endswith("That is within next month.")
    assert [n for n, _ in t.last_calls].count("explain_deal_risk") == 1  # the card is read once


async def test_a_deal_asked_about_with_a_named_month_says_whether_it_falls_in_it() -> None:
    t = Talk()
    assert await t.say("Does Harbor close before November?") == (
        "Harbor & Pine Logistics is expected to close on Tuesday, November 10. So it does not close before "
        "November."
    )
    assert (await t.say("Does Harbor close in November?")).endswith("So it closes in November.")


async def test_odd_phrasings_get_the_briefing_not_a_company_lookup() -> None:
    for q in (
        "Give me a rundown of my pipeline",
        "What's the damage?",
        "Where are we bleeding?",
        "Anything I should worry about today?",
        "tell me about my day",
        "Can you summarize?",
    ):
        t = Talk()
        assert (await t.say(q)).startswith("2 deals are at risk and 5 more are on watch"), q
        assert t.mcp.names() == ["get_risk_briefing"], q


async def test_the_riskiest_deal_is_the_top_of_the_briefing() -> None:
    for q in (
        "Which account is in the worst shape?",
        "Show me the riskiest deal",
        "Which deal has the lowest health?",
    ):
        t = Talk()
        got = await t.say(q)
        assert got.startswith(
            "The riskiest deal is Kestrel Biotech, $84K, owned by Daniel R.: it's at risk, health 29."
        ), q
        assert "lookup_deal" not in t.mcp.names(), q


async def test_how_long_a_deal_has_been_in_its_band() -> None:
    t = Talk()
    assert await t.say("How long has Kestrel been at risk?") == (
        "Kestrel Biotech has been at risk since Saturday, October 17."
    )
    assert (await t.say("And Orbital?")).endswith("on watch since Thursday, October 15.")


async def test_the_soonest_closing_deal() -> None:
    got = await Talk().say("Which deal is closing soonest?")
    assert got.startswith(
        "Among the 5 riskiest deals, the soonest to close is Harbor & Pine Logistics, on Tuesday, November 10."
    )


async def test_who_holds_the_most_money_is_added_up_from_the_deals_seen() -> None:
    got = await Talk().say("Who owns the most at-risk money?")
    assert got.startswith(
        "Among the deals I can see, Daniel R. has the most at risk: $84K across 1 deal (Kestrel Biotech)."
    )
    got = await Talk().say("Which rep has the most?")
    assert "Daniel R. has the most at risk or on watch: $115K across 2 deals" in got


async def test_questions_it_cannot_answer_are_said_so_not_answered_with_something_else() -> None:
    for q, said in (
        ("Which deals have gone quiet?", "I can't filter deals by how long they've been quiet."),
        ("Any deal without a next step?", "I can't scan every deal for gaps like that."),
        ("What's the average deal size?", "I don't work out averages."),
    ):
        t = Talk()
        assert (await t.say(q)).startswith(said), q
        assert t.mcp.names() == [], q


async def test_a_forecast_question_says_it_does_not_forecast() -> None:
    got = await Talk().say("Is Kestrel going to close?")
    assert got.startswith(
        "I can't forecast whether a deal will close, but here is where it stands. Kestrel Biotech is At Risk"
    )


async def test_how_much_does_a_rep_have_is_about_the_rep() -> None:
    got = await Talk().say("How much does Daniel have at risk?")
    assert got.startswith("Among the 5 riskiest deals, Daniel R. owns Kestrel Biotech")


async def test_what_is_happening_with_a_company_is_a_lookup() -> None:
    got = await Talk().say("What's happening with Harbor?")
    assert got.startswith("Harbor & Pine Logistics is At Risk")
