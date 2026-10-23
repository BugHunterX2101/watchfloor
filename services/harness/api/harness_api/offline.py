"""Offline conversation engine: a deterministic router over the MCP tools, for turns no model answers.

It is not a language model and never pretends to be: the API labels every turn it answers with
``"engine": "offline"``. It answers when no model is configured, and when the model fails or runs
out of time (ADR-023). Every fact it says comes from a tool result in the same turn: a tool's own
spoken ``summary``, or a sentence built here from the structured fields (counts, totals, owners,
dates, health), never from a guess. A question the tools can't answer gets a plain "I can't" and
what it can do instead.

Without a model it answers:

- the briefing and its parts: the totals at risk and on watch, counts, last week's comparison, what's
  new, the top N, the biggest deal, one owner's deals, deals closing soon, for any segment;
- one deal, named, placed ("the second one") or followed up ("who owns it?"): why it's at risk, its
  owner, close date, next step, amount, stage, health and trend; a deep review and its result;
- standing orders (listed), a yes to a preview or a held write, and undo.

It never makes a change of its own: a request to change something gets "nothing was changed".

Follow-ups resolve through :class:`Focus`, what the conversation last talked about, built from tool
results (ids, never names) and kept between turns in a sealed cookie: "it" is the deal last
discussed, "the second one" the second deal last listed, and "and on watch?" asks the last
question again for the watch band.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from watchfloor_core.speech import date_display, join_words, money_display, plural

from .control import declines
from .mcp_http import McpHttp, ToolCall

if TYPE_CHECKING:
    from .agent import Pending
    from .control import Screen

# ---------------------------------------------------------------------------------------------------------
# What the conversation is about, between turns.

DEAL_ID = re.compile(r"^dl_[0-9A-Za-z]{1,40}$")
SEGMENTS = ("smb", "mid", "enterprise", "all")
DEAL_KINDS = ("owner", "stage", "amount", "close", "next", "health", "trend", "since")
AGG_KINDS = ("total", "count", "compare", "new", "list", "biggest", "closing", "briefing", "over", "ranking")
TURN_KINDS = frozenset(
    {"explain", "detail", "result", "triage", "priority", "owners", *DEAL_KINDS, *AGG_KINDS}
)


@dataclass
class Focus:
    """What the conversation last talked about: deal ids from tool results, never a guess.

    ``deal``: the deal last discussed ("it"). ``listed``: the deals last listed, in order ("the second
    one"). ``fresh``: the list came from the turn just before, so a place means it, not the board.
    ``kind``: what was last asked ("owner,close", "total"), for "and the second one?". ``segment``:
    the segment the user last asked for, kept until they ask for another.
    """

    deal: str | None = None
    listed: list[str] = field(default_factory=list)
    fresh: bool = False
    kind: str = ""
    segment: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"d": self.deal, "l": self.listed, "f": self.fresh, "k": self.kind, "g": self.segment}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Focus | None:
        """A sealed cookie is ours, but every value is still checked before it can reach a tool."""
        if not isinstance(data, dict):
            return None
        deal, listed, segment = data.get("d"), data.get("l"), data.get("g")
        return cls(
            deal=deal if isinstance(deal, str) and DEAL_ID.match(deal) else None,
            listed=[d for d in listed if isinstance(d, str) and DEAL_ID.match(d)][:5]
            if isinstance(listed, list)
            else [],
            fresh=data.get("f") is True,
            kind=",".join(k for k in str(data.get("k") or "").split(",") if k in TURN_KINDS),
            segment=segment if segment in SEGMENTS else None,
        )


def _ids(items: Any) -> list[str]:
    found = [d.get("deal_id") for d in items or [] if isinstance(d, dict)]
    return [i for i in found if isinstance(i, str) and DEAL_ID.match(i)][:5]


def next_focus(
    focus: Focus | None, calls: Sequence[ToolCall], kind: str = "", segment: str | None = None
) -> Focus:
    """The focus after this turn's tool calls, in order. ``kind`` and ``segment``: what was asked."""
    before = focus or Focus()
    out = replace(before, listed=list(before.listed), fresh=False, kind=kind)
    if segment is not None:
        out.segment = segment
    for c in calls:
        if not c.ok:
            if c.tool == "lookup_deal":  # "Did you mean ...": the suggestions are what a place now means
                ids = _ids(((c.structured or {}).get("error") or {}).get("suggestions"))
                if ids:
                    out.listed, out.fresh = ids, True
            continue
        data = c.structured or {}
        if c.tool == "get_risk_briefing":
            ids = _ids(data.get("deals"))
            if ids:
                out.listed, out.fresh, out.deal = ids, True, ids[0]
            if c.arguments.get("segment") in SEGMENTS:
                out.segment = c.arguments["segment"]
        elif c.tool == "lookup_deal":
            ids = _ids(data.get("candidates"))
            if len(ids) == 1:
                out.deal = ids[0]
            elif ids:
                out.listed, out.fresh = ids, True
        else:
            deal = c.arguments.get("deal_id") or data.get("deal_id")
            if isinstance(deal, str) and DEAL_ID.match(deal):
                out.deal = deal
    return out


# ---------------------------------------------------------------------------------------------------------
# Words. Each pattern is matched against the user's message, never against tool output.

# The whole message is a plain yes ("Yes, go ahead."), so "yes, but change the title" is not one.
YES = re.compile(
    r"\s*(?:yes|yeah|yep|sure|ok|okay|go ahead|do it|confirm)"
    r"(?:[\s,.!]+(?:yes|please|go ahead|do it|thanks|thank you))*[\s,.!]*",
    re.IGNORECASE,
)
YES_BUT = re.compile(r"\s*(?:yes|yeah|yep|sure|ok|okay)\b", re.IGNORECASE)
ACK = re.compile(r"\s*(?:got it|great|cool|alright|all right|fine|nice|understood)[\s,.!]*", re.IGNORECASE)
UNDO = re.compile(r"\b(undo|take (it|that) back|reverse (it|that))\b", re.IGNORECASE)
THANKS = re.compile(
    r"\s*(?:thanks|thank you|thx|cheers|bye|goodbye|good night|see you|talk later|that'?s (?:all|it|everything))"
    r"(?:[\s,.!]+(?:so much|a lot|very much|again|for (?:your )?help|bye|thanks|that'?s (?:all|it|everything)))*"
    r"[\s,.!]*",
    re.IGNORECASE,
)
GREETING = re.compile(
    r"\s*(hi|hello|hey|hiya|good (?:morning|afternoon|evening)|morning|evening)\b[\s,.!]*", re.I
)
SMALL_TALK = re.compile(r"\s*(how are you|how'?s it going|how are things)[\s?.!]*", re.IGNORECASE)
HELP = re.compile(
    r"\s*(help|help me|what can you do|what can i (ask|say)|what do you do|how does this work|"
    r"what are my options|what can you help (me )?with)[\s?.!]*",
    re.IGNORECASE,
)

# Changes. None is made here: each is said to need the model (or the CRM), and nothing changes.
WRITES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "rule_remove",
        re.compile(
            r"\b(stop watching|remove|delete|cancel|turn off|drop|get rid of)\b.*\b(rules?|standing orders?|alerts?)\b"
            r"|\bstop (alerting|notifying|pinging|warning|flagging)\b|\bno more (alerts|notifications)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "prefs",
        re.compile(
            r"\bonly (give|show|tell|brief) me (about )?\w+ deals?\b|\b(give|show) me (just |only )?\w+ deals? "
            r"(from now on|each time|every time|in (my|each|the) briefings?)\b|\b(don'?t|do not|stop|never) "
            r"(read(ing)?|say(ing)?|tell(ing)?|includ(e|ing)|mention(ing)?|giv(e|ing))( me)?( out)? (the |deal )?"
            r"amounts?\b|\b(leave|keep) (the |deal )?amounts? out\b|\bfocus (my )?(briefings? )?on (smb|mid|"
            r"enterprise|small)|\bchange my (briefing|segment|focus)\b|\bbriefing (limit|length|settings)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "rule_set",
        re.compile(
            r"\b(from now on|going forward|flag (any|all|every|deals?)|alert me|notify me|let me know (when|if)|"
            r"tell me (when|if|whenever)|ping me (when|if))\b"
            r"|^\s*(please\s+)?(watch (for|out for)|keep (an eye|watching))",
            re.IGNORECASE,
        ),
    ),
    (
        "stage",
        re.compile(
            r"\b(mark|move|set|change|switch|put)\b[^?]*\b(closed?[ -]?(won|lost)|won|lost|stage|discovery|demo|"
            r"proposal|negotiation|closing)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "task",
        re.compile(
            r"^\s*(?:please\s+)?(?:have\s+(?!(?:me|us|you|i|we|they|it|the|a|an|there|any|some|my)\b)[a-z]+\s+[a-z]+|(?:ask|tell|get)\s+"
            r"(?!me\b|us\b)[a-z']+\s+to\s+\w+)|\b(create|add|make|set up|assign|open) (a |an )?(task|to-?do|"
            r"reminder)\b|\bremind (me|\w+) to\b|\breassign\b|\bhand (it|this|that|\w+) (over )?to\b|\bnotify "
            r"(the )?(channel|team|slack)\b|\bpost (it |this )?(to|in|on) (slack|the channel)\b|\bbook (a |the )?"
            r"(call|meeting)\b"
            # "move the Harbor deal over to Maya", "give Kestrel to Daniel": a new owner (not a close date)
            r"|\b(move|transfer|give|switch)\b(?![^?]*\bclose\b)[^?]*\b(deal|account|it|this one|that one)\b"
            r"[^?]*\b(over )?to [a-z]+\b|\b(move|transfer|give)\b(?![^?]*\bclose\b)[^?]*\bover to\b"
            # "tell the team in slack that…", "let the channel know…"
            r"|\b(tell|let|message|ping|alert|update) (the |my )?(team|channel|everyone|room)\b"
            r"|\b(in|on|to) slack\b",
            re.IGNORECASE,
        ),
    ),
    (
        "draft",
        re.compile(
            r"\b(draft|write|compose)\b|\bsend\b.*\b(email|e-mail|follow[- ]?up|message|note|reply)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "update",
        re.compile(
            r"\bis now (worth )?\$?\d|\bis now (\w+ )?(thousand|million|k)\b|\bnow worth\b|\b(change|update|set) "
            r"the (amount|close date|next step)\b|\blog (an? )?(update|note|call|meeting)\b|\bpush(ed)? the close\b|"
            r"\bmove the close date\b|\bthe next step is\b|\bthey (agreed|signed|said)\b"
            # "push the Kestrel close date to the end of November", "change the Orbital amount to 65K"
            r"|\b(push|move|pull|bump|slip|delay|change|update|set)\b[^?]*\bclose date\b[^?]*\b(to|back|out|by)\b"
            r"|\b(change|update|set|bump|raise|lower|increase|decrease|drop)\b[^?]*\b(amount|value|deal size)\b"
            r"[^?]*\b(to|by)\b",
            re.IGNORECASE,
        ),
    ),
)
ASKING = re.compile(r"\s*(when|why|who|what|how|did|does|do|was|were|has|have|is|are)\b", re.IGNORECASE)
NEEDS_MODEL = {
    "rule_remove": "Removing a standing order",
    "prefs": "Changing your briefing settings",
    "rule_set": "Setting up a standing order",
    "task": "Creating tasks and reassigning deals",
    "draft": "Drafting a follow-up",
    "update": "Logging a deal update",
}

STANDING = re.compile(
    r"standing orders?|my (rules|alerts)|what am i watching|what are you watching( for)?", re.IGNORECASE
)
# "Where do I start?": the briefing's first deal, which the server ranks by band and deal value.
PRIORITY = re.compile(
    r"\b(riskiest|worst|weakest|lowest health|most at[- ]risk(?! (money|dollars|deals))|"
    r"most likely to (churn|slip|be lost|fall through|not close)|"
    r"what should i (do|tackle|handle|look at|work on) first|where (do|should) i (start|begin)|most urgent|"
    r"biggest (risk|problem|worry|fire)|top (deal|risk|priority)|first priority|what'?s (my )?(top|first) priority)\b",
    re.IGNORECASE,
)
FORECAST = re.compile(
    r"\b(going to|gonna|will|likely to|chance of|odds of|probability)\b[^?]*\b(close|win|sign|slip|happen|land)\b",
    re.IGNORECASE,
)
SUPERLATIVE = re.compile(r"\b(riskiest|worst|weakest|lowest health|most at[- ]risk|most likely)\b", re.I)
RESULT = re.compile(
    r"\b(results?|findings?|came back|outcome|(review|triage|it|that) (find|found|say|said|show|turn up|come up with)"
    r"|(review|triage) (done|ready|back|finished|over)|is it (done|finished|back|ready))\b",
    re.IGNORECASE,
)
TRIAGE = re.compile(
    r"\b(deep|triage|review|dig into|dig in|look into|investigate|analy[sz]e)\b", re.IGNORECASE
)
# Asking about one deal: these make a named deal a real lookup, not a guess from loose words.
ASK_DEAL = re.compile(
    r"\b(why|explain|what'?s wrong|what is wrong|what'?s going on with|in trouble|a problem|tell me (more )?about|"
    r"show me|look up|pull up|details|status of|how is|how'?s|how are|doing|at risk|risky|worried about|"
    r"concern(ed|s)?|what'?s (happening|up) with|what is (happening|up) with|news (on|about))\b",
    re.IGNORECASE,
)
DETAIL = re.compile(
    r"\b(tell me (more )?about|show me|look up|pull up|details|info(rmation)? on)\b", re.IGNORECASE
)
EVERYTHING = re.compile(
    r"\b(everything|all (of )?(it|the details?)|full (story|picture|details?))\b", re.IGNORECASE
)
KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "owner",
        re.compile(
            r"\b(who owns|who'?s the owner|who is the owner|owner|owned by|who'?s (on|handling|running|working on)|"
            r"who is (handling|running|working on|on)|who'?s responsible|which rep)\b",
            re.IGNORECASE,
        ),
    ),
    ("stage", re.compile(r"\b(stage|phase)\b", re.IGNORECASE)),
    ("amount", re.compile(r"\b(worth|how much|how big|deal size|size of|value|amount)\b", re.IGNORECASE)),
    (
        "close",
        re.compile(
            r"\b(when (does|will|do|is|are|should) .*\b(close|closing|decide|sign|signing)|close date|closing date|"
            r"expected (to )?close|decide|decision|deadline)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "next",
        re.compile(
            r"\b(next steps?|next action|what'?s next|what happens next|what should (i|we) do (next|about (it|this|"
            r"that)))\b",
            re.IGNORECASE,
        ),
    ),
    ("health", re.compile(r"\b(health|score)\b", re.IGNORECASE)),
    ("since", re.compile(r"\b(since when|how long (has|have|is|was)|for how long)\b", re.IGNORECASE)),
    (
        "trend",
        re.compile(
            r"\b(better or worse|worse or better|getting (better|worse)|improv\w*|worsen\w*|trend\w*|declin\w*|"
            r"last week|week ago|over time|changed)\b",
            re.IGNORECASE,
        ),
    ),
)

TOTAL = re.compile(
    r"\b(how much|total|altogether|dollars?|money|value|worth|exposure|at stake|on the line|in danger|exposed)\b",
    re.IGNORECASE,
)
# "deals over 50K", "under fifty thousand": a filter on amount, answered from the briefing's deals.
AMOUNT_OVER = re.compile(
    r"\b(?P<op>over|above|more than|bigger than|larger than|greater than|at least|exceeding|exceeds|under|below|"
    r"less than|smaller than)\s+\$?\s*(?P<n>\d[\d,]*(?:\.\d+)?|(?:a |one )?(?:ten|twenty|thirty|forty|fifty|sixty|"
    r"seventy|eighty|ninety|hundred)(?:[ -](?:five|ten))?)\s*(?P<unit>k|m|thousand|million|grand)?\b",
    re.IGNORECASE,
)
TENS = {"ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
        "eighty": 80, "ninety": 90, "hundred": 100}  # fmt: skip
WHY = re.compile(r"\b(why|explain|what'?s wrong|what is wrong|in trouble|what'?s going on)\b", re.IGNORECASE)
COUNT = re.compile(r"\b(how many|number of|count)\b", re.IGNORECASE)
COMPARE = re.compile(
    r"\b(last week|a week ago|week over week|than (before|last)|compared? (to|with)|healthier|"
    r"better or worse|worse or better|improv(ing|ed)|getting (better|worse))\b",
    re.IGNORECASE,
)
NEW = re.compile(
    r"\b(new|what changed|what'?s changed|since (yesterday|my last|the last|last time|this morning|then)|lately|"
    r"recently|latest|updates?)\b",
    re.IGNORECASE,
)
BIGGEST = re.compile(r"\b(biggest|largest|most valuable|highest[- ]value|top dollar)\b", re.IGNORECASE)
TOP_N = re.compile(r"\btop (\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b", re.IGNORECASE)
LIST = re.compile(
    r"\b(list|which deals|what deals|all (the |my )?deals|(at[- ]risk|risky|watch(ed)?) deals|deals (that are )?"
    r"(at risk|on watch)|show (me )?(the |all |my )?deals|name them|which ones|what are they)\b",
    re.IGNORECASE,
)
SOONEST = re.compile(r"\b(soonest|earliest|next to close|clos\w* first)\b", re.IGNORECASE)
CLOSING = re.compile(
    r"\bsoonest\b|\bearliest\b|\bnext to close\b|\bclos\w* first\b|"
    r"\b(clos(e|es|ing)|due|decid\w*|sign\w*)\b.*\b(this|next) (week|month|quarter)\b|\bclosing soon\b|"
    r"\bclose soon\b|\b(this|next) (week|month|quarter)\b.*\bclos",
    re.IGNORECASE,
)
CLOSE_WORD = re.compile(r"\b(clos(e|es|ing)|due|decid\w*|sign\w*)\b", re.IGNORECASE)
WINDOW = re.compile(r"\b(this|next) (week|month|quarter)\b", re.IGNORECASE)
MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)  # fmt: skip
# "before November", "by the end of November", "in December": a window named by a month.
MONTH_WINDOW = re.compile(
    r"\b(before|by|until|till|ahead of|in|during|end of)\s+(?:the\s+end\s+of\s+)?(?:the\s+)?"
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
    r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",
    re.IGNORECASE,
)
BRIEFING = re.compile(
    r"\b(attention|brief(ing)?|what'?s (going on|happening|up)|overview|summary|catch me up|on fire|fires?|"
    r"how('s| is| are) (my |the )?(pipeline|deals|things|everything)|status|what'?s at risk|anything at risk|"
    r"any risks?|what needs me|needs? me|what should i (be )?(watch\w*|look\w*|worr\w*)( out)?( for| at| about)?)\b",
    re.IGNORECASE,
)
SEGMENT = re.compile(
    r"\b(smb|small business(es)?|mid[- ]?market|midmarket|mid|enterprise|all segments|every segment|"
    r"whole pipeline|entire pipeline)\b",
    re.IGNORECASE,
)
SEGMENT_OF = {"smb": "smb", "small": "smb", "mid": "mid", "enterprise": "enterprise"}
SEGMENT_WORDS = {"smb": "SMB", "mid": "mid-market", "enterprise": "enterprise"}
ELLIPSIS = re.compile(r"\s*(and|what about|how about|same for|and what about|what of)\b", re.IGNORECASE)
GENERAL = re.compile(
    r"\bhow(?:'s| is| are| does| do)\s+(?:my |the )?(?:everything|things|it all|it looking|all looking)\b",
    re.I,
)
OVERVIEW = re.compile(
    r"\b(run ?down|damage|bleeding|burning|summari[sz]e|summary of|my day|worry|worried|the situation|"
    r"state of (things|play)|big picture)\b",
    re.IGNORECASE,
)
WHO_MOST = re.compile(
    r"\b(who|which (rep|owner|person|one))\b[^?]*\b(owns?|has|have|handles?|holds?)\b"
    r"[^?]*\b(most|biggest|largest|more)\b",
    re.IGNORECASE,
)
UNSUPPORTED: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(gone (quiet|dark|silent)|going (quiet|dark|silent)|stale|ghost\w*|gone cold)\b", re.I
        ),
        "I can't filter deals by how long they've been quiet. Ask about one deal and I'll say how long it's "
        "been since two-way contact.",
    ),
    (
        re.compile(
            r"\b(without|with no|missing|lacking|lacks?) (a |an )?(next step|close date|owner)\b", re.I
        ),
        "I can't scan every deal for gaps like that. Ask about one deal and I'll tell you what's on record.",
    ),
    (
        re.compile(r"\b(average|mean|median)\b", re.I),
        "I don't work out averages. I can give you the total at risk and on watch, and how many deals are in each.",
    ),
)
WATCH_BAND = re.compile(r"\b(on watch|watch ?list|watched|being watched)\b", re.IGNORECASE)
RISK_BAND = re.compile(r"\b(at[- ]risk)\b", re.IGNORECASE)

# "this deal" is the open sheet; "it" is the deal last discussed. "This month" is neither.
THIS = re.compile(
    r"\bthis( one| deal| account)?\b(?!\s+(week|month|quarter|year|morning|afternoon|evening))", re.I
)
PRONOUN = re.compile(
    r"\b(it|its|it's|that one|that deal|that account|they|them|their)\b|\bthat[?.!\s]*$", re.IGNORECASE
)
# "the second one", "the top deal", "number three": a place in the list the user just heard or sees.
PLACE = re.compile(
    r"\b(?:the\s+)?(first|second|third|fourth|fifth|top|last)\s+(?:one|deal|account)\b"
    r"|\b(?:deal\s+)?number\s+(one|two|three|four|five|[1-5])\b"
    r"|\bthe\s+(first|second|third|fourth|fifth|last)\s*[?.!]*\s*$",
    re.IGNORECASE,
)
PLACES = {"first": 1, "top": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "last": -1,
          "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}  # fmt: skip
NUMBERS = {**PLACES, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
ORDINALS = ("First", "Second", "Third", "Fourth", "Fifth")

# "Maya's deals", "which deals does Daniel own": an owner, never looked up as a company.
OWNER = re.compile(
    r"\b(?:which|what) (?:deals|ones|accounts) (?:does|do|is|are) (?P<a>[a-z]+)\b[^?]*?\b(?:own|owns|have|has|"
    r"handle|handles|run|runs|working on)\b"
    r"|\b(?P<b>[a-z]+)['’]s (?:deals|accounts|pipeline|book)\b"
    r"|\b(?:deals|accounts) (?:owned by|of|for|belonging to|that belong to|run by|handled by) (?P<c>[a-z]+)\b"
    r"|\b(?:what|which) does (?P<d>[a-z]+) (?:own|have|handle)\b"
    r"|\bwhat(?:'s| is) (?P<e>[a-z]+) working on\b"
    r"|\bhow (?:much|many)\b[^?]*?\bdoes (?P<f>[a-z]+) (?:have|own|hold|handle)\b",
    re.IGNORECASE,
)

STOP = frozenset(
    """riskiest worst weakest lowest highest average mean median day without overdue quiet summarize summarise
    summary rundown damage bleeding burning worry soonest earliest shape churn likely gone going
    long happening news
    a an the is are was were be been am at on in of for to with about from by as into me my mine our ours i
    we you your yours please can could would will should shall may might must do does did done doing have has had
    get got give gives show tell let lets let's want need needs needed
    why what whats what's which who whos who's whom whose when where how hows how's
    wrong explain deal deals account accounts risk risks risky at-risk trouble problem problems issue issues
    concern concerns concerned worried worry
    deep triage review reviews reviewing run start dig look looking investigate analyse analyze results result
    finished came back outcome find found findings finding say said turn up ready over
    go going status it its it's this that these those they them their there here one ones
    and or but so just only also too then than now right today yesterday tomorrow week weeks month months
    quarter year morning afternoon evening tonight soon
    much many total totals altogether money dollars dollar value worth amount size big bigger biggest large larger
    largest most more less exposure stake
    new news changed change changes update updates latest lately recently since last ago before
    owner owners owns own owned rep handling handle handles running runs working responsible belong belongs
    close closes closing closed decide decision decides date dates sign signing deadline due expected
    next step steps action actions plan happens happen
    stage stages phase health healthy healthier score scores trend trending better worse improving improve
    improved getting declining decline worsening time
    list top first second third fourth fifth last number line lines
    segment segments all every everything whole smb mid mid-market midmarket market enterprise small business
    businesses pipeline pipelines briefing brief attention anything something nothing fire fires
    watch watching watched watchlist
    help hi hello hey thanks thank cheers bye good great ok okay yes no sure uh um er like
    details detail info information pull open lookup know more name names
    over above under below least exceeding exceeds greater smaller thousand million grand hundred k
    any some anyone versus vs against compared compare looked seen checked heard
    two three four five six seven eight nine ten eleven twelve dozen couple few several many
    currently still really actually anyway else""".split()  # noqa: SIM905 - easier to read and extend
)
MONEY = re.compile(r"^\$?\d[\d,.]*[kKmM]?$|^(ten|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)$")
WORD = re.compile(r"[A-Za-z0-9&][A-Za-z0-9&'’.-]*")

FALLBACK = (
    "Sorry, I didn't follow that. You can ask what needs your attention, how much is at risk in total, "
    "how this week compares with last week, or about one deal: why it's at risk, who owns it, when it "
    "closes and what the next step is."
)
HELP_TEXT = (
    "Right now I'm answering straight from your deal data, without the assistant model. You can ask what "
    "needs your attention, how much is at risk or on watch, how many deals are at risk, how this week "
    "compares with last week, what's new, or for the top five. About one deal, ask why it's at risk, who "
    "owns it, when it closes, what the next step is, what it's worth, its stage or its trend, or ask me to "
    "dig into it for a deep review. I can also list your standing orders. Making changes, like tasks, "
    "follow-up drafts or new standing orders, needs the assistant model."
)
WHICH = "Which deal do you mean?"


def _place(message: str) -> int | None:
    m = PLACE.search(message)
    if not m:
        return None
    word = (m.group(1) or m.group(2) or m.group(3)).lower()
    return PLACES.get(word) or int(word)


def _at_place(message: str, ids: Sequence[str]) -> str | None:
    n = _place(message)
    if n is None or not ids:
        return None
    if n == -1:
        return ids[-1]
    return ids[n - 1] if n <= len(ids) else None


def screen_deal(message: str, screen: Screen | None) -> str | None:
    """The deal id a place on the board names ("the second one"), when the message names one."""
    return _at_place(message, [d.id for d in screen.board] if screen is not None else [])


def deal_words(message: str) -> str:
    """The words left once intent words are removed: the deal or company name, hopefully.

    "and" (or "&") stays only between two kept words, so "Harbor and Pine" survives but "who owns it,
    and when" doesn't leave an "and" to look up.
    """
    words = [w.removesuffix("'s").removesuffix("’s").strip(".'’") for w in WORD.findall(message)]
    keep = [bool(w) and w.lower() not in STOP and not MONEY.match(w) and len(w) > 1 for w in words]
    out = []
    for i, w in enumerate(words):
        if w.lower() in ("and", "&"):
            if 0 < i < len(words) - 1 and keep[i - 1] and keep[i + 1]:
                out.append(w)
        elif keep[i]:
            out.append(w)
    return " ".join(out)[:80]


def _without_owner(message: str) -> tuple[str | None, str]:
    """The owner a message names ("Maya's deals") and the message without that phrase."""
    for m in OWNER.finditer(message):
        name = next((g for g in m.groups() if g), None)
        if name and name.lower() not in STOP:
            return name.title(), (message[: m.start()] + " " + message[m.end() :]).strip()
    return None, message


def recent_deal(history: Sequence[tuple[str, str]]) -> str:
    """The deal the user last named, for a follow-up like "Dig into it." when no focus was kept."""
    for role, text in reversed(history):
        if role == "user" and (TRIAGE.search(text) or ASK_DEAL.search(text) or RESULT.search(text)):
            words = deal_words(_without_owner(text)[1])
            if len(words) >= 2:
                return words
    return ""


def _kinds(message: str) -> list[str]:
    return [k for k, pattern in KINDS if pattern.search(message)]


def _segment(message: str) -> str | None:
    m = SEGMENT.search(message)
    if not m:
        return None
    word = m.group(1).lower().replace("-", "").replace(" ", "")
    return next((seg for prefix, seg in SEGMENT_OF.items() if word.startswith(prefix)), "all")


def _band(message: str) -> str | None:
    watch, risk = bool(WATCH_BAND.search(message)), bool(RISK_BAND.search(message))
    return "watch" if watch and not risk else "at_risk" if risk and not watch else None


def _top_n(message: str) -> int:
    m = TOP_N.search(message)
    return int(m.group(1)) if m and m.group(1).isdigit() else NUMBERS.get(m.group(1).lower(), 5) if m else 5


def _aggregate(message: str) -> str | None:
    """The question about the whole pipeline (or a segment of it), if this is one."""
    if CLOSING.search(message) or (CLOSE_WORD.search(message) and MONTH_WINDOW.search(message)):
        return "closing"
    if COMPARE.search(message):
        return "compare"
    if AMOUNT_OVER.search(message):
        return "over"
    if COUNT.search(message):
        return "count"
    if BIGGEST.search(message):
        return "biggest"
    if TOP_N.search(message) or LIST.search(message):
        return "list"
    if TOTAL.search(message):
        return "total"
    if NEW.search(message):
        return "new"
    return None


# ---------------------------------------------------------------------------------------------------------
# Sentences from tool data.

BAND_WORDS = {"at_risk": "at risk", "watch": "on watch", "healthy": "healthy"}


def _is(n: int) -> str:
    return "is" if n == 1 else "are"


def _seg_words(data: dict[str, Any]) -> str:
    seg = data.get("segment")
    return f" in {SEGMENT_WORDS[seg]}" if seg in SEGMENT_WORDS else ""


def _day(iso: Any) -> str | None:
    try:
        return date_display(date.fromisoformat(str(iso)[:10]))
    except ValueError:
        return None


def _deal_line(d: dict[str, Any]) -> str:
    amount = f", {d['amount_display']}" if d.get("amount_display") else ""
    band = BAND_WORDS.get(d.get("band", ""), "flagged")
    return f"{d.get('account') or d.get('name')}{amount}, {band}, health {d.get('health')}"


def _counts(data: dict[str, Any], seg: str) -> str:
    at, attention = int(data.get("at_risk_count") or 0), int(data.get("attention_count") or 0)
    watch = attention - at
    if attention == 0:
        return f"Nothing{seg} is at risk or on watch right now."
    if at == 0:
        return f"No deals{seg} are at risk, and {plural(watch, 'deal')} {_is(watch)} on watch."
    more = f", and {watch} more {_is(watch)} on watch" if watch else ", and none are on watch"
    return f"{plural(at, 'deal')}{seg} {_is(at)} at risk{more}."


def say_total(data: dict[str, Any], band: str | None) -> str:
    seg = _seg_words(data)
    at, attention = int(data.get("at_risk_count") or 0), int(data.get("attention_count") or 0)
    watch = attention - at
    risk_amt, watch_amt = data.get("at_risk_amount_display"), data.get("watch_amount_display")
    if attention == 0:
        return f"Nothing{seg} is at risk or on watch, so nothing is at stake right now."
    if (at and not risk_amt) or (watch and not watch_amt):
        return f"Your briefing settings leave amounts out, so I can't total them. {_counts(data, seg)}"
    if band == "watch":
        if not watch:
            return f"No deals{seg} are on watch right now."
        return f"{watch_amt} is on watch{seg}, across {plural(watch, 'deal')}."
    if at == 0:
        return (
            f"No deals{seg} are at risk right now. {watch_amt} is on watch, across {plural(watch, 'deal')}."
        )
    out = f"{risk_amt} is at risk{seg}, across {plural(at, 'deal')}."
    if band is None and watch:
        out += f" Another {watch_amt} is on watch, across {plural(watch, 'deal')}."
    return out


def say_count(data: dict[str, Any], band: str | None) -> str:
    seg = _seg_words(data)
    at, attention = int(data.get("at_risk_count") or 0), int(data.get("attention_count") or 0)
    watch = attention - at
    if band == "watch":
        worth = (
            f", worth {data['watch_amount_display']} in total"
            if watch and data.get("watch_amount_display")
            else ""
        )
        return f"{plural(watch, 'deal')}{seg} {_is(watch)} on watch{worth}."
    if band == "at_risk":
        return f"{plural(at, 'deal')}{seg} {_is(at)} at risk."
    return _counts(data, seg)


def say_compare(data: dict[str, Any]) -> str:
    seg = data.get("segment")
    lead = f"In {SEGMENT_WORDS[seg]}: " if seg in SEGMENT_WORDS else ""
    week = data.get("week_ago") or {}
    if not week.get("comparison"):
        return f"I don't have a snapshot from a week ago{_seg_words(data)} to compare with yet."
    return lead + week["comparison"]


def say_new(data: dict[str, Any]) -> str:
    seg = _seg_words(data)
    n = int(data.get("new_count") or 0)
    deals = [d for d in data.get("deals") or [] if isinstance(d, dict)]
    rules = [r for r in data.get("triggered_rules") or [] if isinstance(r, dict)]
    if n == 0 and not rules:
        return f"Nothing new{seg} since your last briefing. {_counts(data, seg)}"
    parts = []
    if n:
        named = [str(d.get("account")) for d in deals if d.get("new_since_last_briefing")]
        rest = n - len(named)
        names = f": {join_words(named + ([f'{rest} more'] if rest > 0 else []))}" if named else ""
        parts.append(f"{plural(n, 'deal')}{seg} {_is(n)} new on the list since your last briefing{names}.")
    else:
        parts.append(f"No deals{seg} are new on the list since your last briefing.")
    by_id = {d.get("deal_id"): d.get("account") for d in deals}
    for r in rules[:2]:
        named = [str(by_id[i]) for i in r.get("deal_ids") or [] if by_id.get(i)]
        who = join_words(named[:2]) if named else plural(len(r.get("deal_ids") or []), "deal")
        parts.append(f"Your standing order “{r.get('description')}” flagged {who}.")
    return " ".join(parts)


def say_list(data: dict[str, Any], band: str | None, asked: int) -> str:
    seg = _seg_words(data)
    deals = [d for d in data.get("deals") or [] if isinstance(d, dict)]
    at, attention = int(data.get("at_risk_count") or 0), int(data.get("attention_count") or 0)
    total = attention
    if band:
        deals = [d for d in deals if d.get("band") == band]
        total = at if band == "at_risk" else attention - at
    seen = len(deals)  # of the band's deals, the ones the briefing can show
    deals = deals[:asked]
    if not deals:
        return f"Nothing{seg} is {BAND_WORDS[band] if band else 'at risk or on watch'} right now."
    lines = " ".join(f"{ORDINALS[i]}, {_deal_line(d)}." for i, d in enumerate(deals))
    if band:
        head = f"{plural(total, 'deal')}{seg} {_is(total)} {BAND_WORDS[band]}."
    elif len(deals) < total:
        head = f"The top {len(deals)} of the {total} deals{seg} that need attention:"
    else:
        head = f"{plural(total, 'deal')}{seg} need{'s' if total == 1 else ''} attention."
    out = f"{head} {lines}"
    if seen < total and asked > seen:  # more were asked for than the briefing can show
        rest = total - seen
        out += (
            f" I can only see the top five deals that need attention, so {rest} more {_is(rest)} not shown."
        )
    return out


def say_biggest(data: dict[str, Any], band: str | None) -> str:
    seg = _seg_words(data)
    deals = [d for d in data.get("deals") or [] if isinstance(d, dict)]
    at, attention = int(data.get("at_risk_count") or 0), int(data.get("attention_count") or 0)
    pool = [d for d in deals if d.get("band") == band] if band else deals
    total = (at if band == "at_risk" else attention - at) if band else attention
    if not pool:
        return f"Nothing{seg} is {BAND_WORDS[band] if band else 'at risk or on watch'} right now."
    if any(not isinstance(d.get("amount_cents"), int) for d in pool):
        return "Your briefing settings leave amounts out, so I can't tell which deal is biggest."
    best = max(pool, key=lambda d: d["amount_cents"])
    what = " ".join(w for w in (BAND_WORDS[band] if band else "", seg.strip()) if w)
    status = BAND_WORDS.get(best.get("band", ""), "flagged")
    line = f"{best.get('account')}, at {best.get('amount_display')}: {status}, health {best.get('health')}."
    if len(pool) >= total:
        return f"The biggest deal{' ' + what if what else ''} is {line}"
    return (
        f"Of the {len(deals)} riskiest deals{seg}, the biggest{' ' + BAND_WORDS[band] if band else ''} one is "
        f"{line} I can see only the top five, so {total - len(pool)} more {_is(total - len(pool))} not in "
        "that list."
    )


def say_owner(data: dict[str, Any], owner: str) -> str:
    seg = _seg_words(data)
    deals = [d for d in data.get("deals") or [] if isinstance(d, dict)]
    attention = int(data.get("attention_count") or 0)
    if attention == 0:
        return f"Nothing{seg} is at risk or on watch right now, so none of {owner}'s deals need attention."
    mine = _owned(data, owner)
    scope = (
        f" I can see only the top {len(deals)} of the {attention} deals that need attention."
        if len(deals) < attention
        else ""
    )
    if not mine:
        return f"None of the {len(deals)} riskiest deals{seg} belong to {owner}.{scope}"
    items = [f"{d.get('account')} ({_deal_line(d).split(', ', 1)[1]})" for d in mine]
    return (
        f"Among the {len(deals)} riskiest deals{seg}, {mine[0].get('owner')} owns {join_words(items)}.{scope}"
    )


def say_ranking(data: dict[str, Any], band: str | None) -> str:
    """Whose deals hold the most money, among the deals the briefing lists (a total from their cents)."""
    seg = _seg_words(data)
    deals = [
        d for d in data.get("deals") or [] if isinstance(d, dict) and isinstance(d.get("amount_cents"), int)
    ]
    attention = int(data.get("attention_count") or 0)
    if attention == 0:
        return f"Nothing{seg} is at risk or on watch right now."
    pool = [d for d in deals if band is None or d.get("band") == band]
    if not pool:
        return "I can't see any amounts to add up. They may be switched off in your briefing settings."
    by: dict[str, list[dict[str, Any]]] = {}
    for d in pool:
        by.setdefault(str(d.get("owner") or "Nobody"), []).append(d)
    totals = sorted(
        ((sum(x["amount_cents"] for x in ds), o, ds) for o, ds in by.items()), key=lambda t: -t[0]
    )
    top, owner, ds = totals[0]
    scope = (
        f" I can see only the top {len(deals)} of the {attention} deals that need attention."
        if len(deals) < attention
        else ""
    )
    label = BAND_WORDS.get(band or "", "at risk or on watch")
    names = join_words([str(x.get("account")) for x in ds])
    if len(totals) > 1 and totals[1][0] == top:
        return f"{owner} and {totals[1][1]} are level on {label}, at {money_display(top)} each.{scope}"
    return (
        f"Among the deals I can see{seg}, {owner} has the most {label}: {money_display(top)} "
        f"across {plural(len(ds), 'deal')} ({names}).{scope}"
    )


def parse_amount(message: str) -> tuple[str, int] | None:
    """("over" | "under" | "at least", cents) from "over 50K", "under fifty thousand", else None."""
    m = AMOUNT_OVER.search(message)
    if not m:
        return None
    raw = m.group("n").lower().replace(",", "")
    try:
        n = float(raw)
    except ValueError:
        n = float(sum(TENS[w] for w in re.split(r"[ -]", raw) if w in TENS))
    unit = (m.group("unit") or "").lower()
    if unit in ("k", "thousand", "grand"):
        n *= 1_000
    elif unit in ("m", "million"):
        n *= 1_000_000
    elif (
        n < 1_000 and not unit and not raw[0].isdigit()
    ):  # "over fifty": thousands, as deals are talked about
        n *= 1_000
    word = m.group("op").lower()
    if word == "at least":
        op = "at least"
    elif word in ("under", "below", "less than", "smaller than"):
        op = "under"
    else:
        op = "over"
    return (op, int(n * 100)) if n > 0 else None


def say_over(data: dict[str, Any], band: str | None, op: str, cents: int) -> str:
    seg = _seg_words(data)
    deals = [d for d in data.get("deals") or [] if isinstance(d, dict)]
    at, attention = int(data.get("at_risk_count") or 0), int(data.get("attention_count") or 0)
    if attention == 0:
        return f"Nothing{seg} is at risk or on watch right now."
    if any(not isinstance(d.get("amount_cents"), int) for d in deals):
        return "Your briefing settings leave amounts out, so I can't filter deals by amount."
    pool = [d for d in deals if d.get("band") == band] if band else deals
    total = (at if band == "at_risk" else attention - at) if band else attention
    state = BAND_WORDS[band] if band else "at risk or on watch"
    keep = {"over": lambda c: c > cents, "under": lambda c: c < cents, "at least": lambda c: c >= cents}[op]
    hit = [d for d in pool if keep(d["amount_cents"])]
    label = f"{op} {money_display(cents)}"
    scope = (
        f" I can see only the top {len(deals)} of the {attention} deals that need attention."
        if len(pool) < total
        else ""
    )
    if not hit:
        return (
            f"None of the {plural(len(pool), 'deal')}{seg} that {_is(len(pool))} {state} is {label}.{scope}"
        )
    items = [f"{d.get('account')}, {d.get('amount_display')}" for d in hit]
    return (
        f"Of the {plural(len(pool), 'deal')}{seg} that {_is(len(pool))} {state}, {len(hit)} "
        f"{_is(len(hit))} {label}: {join_words(items)}.{scope}"
    )


def _owned(data: dict[str, Any], owner: str) -> list[dict[str, Any]]:
    deals = [d for d in data.get("deals") or [] if isinstance(d, dict)]
    return [d for d in deals if str(d.get("owner") or "").split(" ")[0].lower() == owner.lower()]


def _window(word: str, unit: str, today: date) -> tuple[date, date]:
    if unit == "week":
        start = today - timedelta(days=today.weekday()) + timedelta(days=7 if word == "next" else 0)
        return start, start + timedelta(days=6)
    if unit == "month":
        first = today.replace(day=1)
        if word == "next":
            first = (first + timedelta(days=32)).replace(day=1)
        return first, (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    q = (today.month - 1) // 3 + (1 if word == "next" else 0)
    year, q = today.year + q // 4, q % 4
    first = date(year, 3 * q + 1, 1)
    return first, date(year + (q == 3), (3 * q + 3) % 12 + 1, 1) - timedelta(days=1)


def _month_window(message: str, today: date) -> tuple[date, date, str] | None:
    """The window a named month gives, at its next occurrence (this month counts): "before November" runs
    from today to the day before it starts; "by" or "until" to its last day; "in" or "during" is the month."""
    m = MONTH_WINDOW.search(message)
    if m is None:
        return None
    word = m.group(1).lower()
    month = next(i for i, name in enumerate(MONTHS, 1) if name.startswith(m.group(2).lower()[:3]))
    year = today.year + (month < today.month)
    first = date(year, month, 1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    name = f"{MONTHS[month - 1].capitalize()}{'' if year == today.year else f' {year}'}"
    if word in ("before", "ahead of"):
        return today, first - timedelta(days=1), f"before {name}"
    if word in ("in", "during"):
        return max(first, today), last, f"in {name}"
    return today, last, f"by the end of {name}"


def say_closing(data: dict[str, Any], cards: list[dict[str, Any]], message: str) -> str:
    seg = _seg_words(data)
    attention = int(data.get("attention_count") or 0)
    if attention == 0:
        return f"Nothing{seg} is at risk or on watch right now."
    try:
        today = datetime.fromisoformat(str(data.get("generated_at"))).date()
    except ValueError:
        return "I can't tell today's date from the briefing, so I can't say what closes when."
    m = WINDOW.search(message)
    word, unit = (m.group(1).lower(), m.group(2).lower()) if m else ("this", "month")
    if SOONEST.search(message) and not m:
        firsts = sorted(
            ((date.fromisoformat(str(c["close_date"])), c) for c in cards if _day(c.get("close_date"))),
            key=lambda x: x[0],
        )
        if firsts:
            d, c = firsts[0]
            more = (
                f" I checked the top {len(cards)} of the {attention} deals that need attention."
                if len(cards) < attention
                else ""
            )
            who = c.get("account")
            head = f"Among the {len(cards)} riskiest deals{seg}, the soonest to close is {who}"
            return f"{head}, on {date_display(d)}.{more}"
    named = None if m else _month_window(message, today)
    start, end = (named[0], named[1]) if named else _window(word, unit, today)
    words = named[2] if named else f"{word} {unit}"
    dated: list[tuple[date, dict[str, Any]]] = []
    for c in cards:
        try:
            dated.append((date.fromisoformat(str(c.get("close_date"))), c))
        except ValueError:
            continue
    dated.sort(key=lambda x: x[0])
    scope = (
        f" I checked the top {len(cards)} of the {attention} deals that need attention."
        if len(cards) < attention
        else ""
    )
    inside = [(d, c) for d, c in dated if start <= d <= end]
    if inside:
        items = [f"{c.get('account')}, on {date_display(d)}" for d, c in inside]
        verb = "closes" if len(inside) == 1 else "close"
        return (
            f"Among the {len(cards)} riskiest deals{seg}, {plural(len(inside), 'deal')} {verb} {words}: "
            f"{join_words(items)}.{scope}"
        )
    if not dated:
        return f"I couldn't read the close dates of the riskiest deals{seg}."
    d, c = dated[0]
    return (
        f"None of the {len(cards)} riskiest deals{seg} close {words}. The soonest is {c.get('account')}, "
        f"on {date_display(d)}.{scope}"
    )


def say_deal(kinds: Sequence[str], data: dict[str, Any]) -> str:
    """Answers about one deal from explain_deal_risk's structured result; the deal is named once."""
    deal, risk = data.get("deal") or {}, data.get("risk") or {}
    name = str(deal.get("account") or deal.get("name") or "That deal")
    out: list[str] = []
    for k in DEAL_KINDS:
        if k not in kinds:
            continue
        first = not out
        subject = name if first else "It"
        if k == "owner":
            out.append(f"{deal.get('owner') or 'Nobody'} owns {name if first else 'it'}.")
        elif k == "stage":
            out.append(f"{subject} is in the {deal.get('stage')} stage.")
        elif k == "amount":
            amount = deal.get("amount_display")
            out.append(f"{subject} is worth {amount}." if amount else f"I don't have an amount for {name}.")
        elif k == "close":
            day = deal.get("close_date_display") or _day(deal.get("close_date"))
            out.append(
                f"{subject} is expected to close on {day}." if day else f"{name} has no close date on record."
            )
        elif k == "next":
            step = str(deal.get("next_step") or "").strip().rstrip(".")
            due = _day(deal.get("next_step_date"))
            whose = f" for {name}" if first else ""
            if step:
                out.append(f"The next step on record{whose} is: {step}" + (f", due {due}." if due else "."))
            else:
                out.append(f"There's no next step on record{whose}.")
        elif k == "health":
            band = BAND_WORDS.get(str(risk.get("band")), "")
            owner_of = f"{name}'s" if first else "Its"
            out.append(f"{owner_of} health is {risk.get('health')}" + (f", {band}." if band else "."))
        elif k == "trend":
            out.append(_trend(subject, data))
        elif k == "since":
            band = BAND_WORDS.get(str(risk.get("band")), "")
            since = _day(risk.get("band_since"))
            if band and since:
                out.append(f"{subject} has been {band} since {since}.")
            else:
                out.append(
                    f"I don't have a date for when {name if first else 'it'} reached its current band."
                )
    return " ".join(out) or str(data.get("summary") or "")


def _trend(subject: str, data: dict[str, Any]) -> str:
    """Health two weeks ago against now, from explain_deal_risk's 14 daily risk scores (health = 100 - score)."""
    risk = data.get("risk") or {}
    scores = [
        s for s in data.get("trend_14d") or [] if isinstance(s, (int, float)) and not isinstance(s, bool)
    ]
    band = BAND_WORDS.get(str(risk.get("band")), "")
    since = _day(risk.get("band_since"))
    tail = f" It has been {band} since {since}." if band and since else ""
    if len(scores) < 2:
        who = subject if subject != "It" else "it"
        return f"I don't have enough history yet to say which way {who} is heading.{tail}"
    then, now = 100 - round(scores[0]), 100 - round(scores[-1])
    ago = f"{len(scores) - 1} days ago" if len(scores) == 14 else "at the start of what I have"
    if now < then:
        return f"{subject} is getting worse: health {then} {ago}, {now} now.{tail}"
    if now > then:
        return f"{subject} is getting better: health {then} {ago}, {now} now.{tail}"
    return f"{subject} is holding steady at health {now}, the same as {ago}.{tail}"


def say_detail(data: dict[str, Any]) -> str:
    deal = data.get("deal") or {}
    amount = str(deal.get("amount_display") or "")
    article = "an" if re.match(r"\$(?:8\d*|11|18)(?!\d)", amount) else "a"
    kind = f"{article} {amount} deal" if amount else "a deal"
    if deal.get("stage"):
        kind += f" in {deal['stage']}"
    facts = [kind]
    if deal.get("owner"):
        facts.append(f"owned by {deal['owner']}")
    if deal.get("close_date_display"):
        facts.append(f"expected to close on {deal['close_date_display']}")
    return f"{data.get('summary', '')} It's {', '.join(facts)}.".strip()


def say_priority(top: dict[str, Any], data: dict[str, Any], superlative: bool = False) -> str:
    deal, risk = data.get("deal") or {}, data.get("risk") or {}
    name = deal.get("account") or top.get("account")
    amount = f", {deal['amount_display']}" if deal.get("amount_display") else ""
    owner = f", owned by {deal['owner']}" if deal.get("owner") else ""
    band = BAND_WORDS.get(str(risk.get("band") or top.get("band")), "flagged")
    reasons = " ".join(str(r.get("text")) for r in (data.get("reasons") or [])[:2] if isinstance(r, dict))
    step = str(deal.get("next_step") or "").strip().rstrip(".")
    due = _day(deal.get("next_step_date"))
    nxt = f"The next step on record is: {step}" + (f", due {due}." if due else ".") if step else ""
    lead = "The riskiest deal is" if superlative else "Start with"
    head = f"{lead} {name}{amount}{owner}: it's {band}, health {risk.get('health', top.get('health'))}."
    return " ".join(p for p in (head, reasons, nxt) if p)


def _lookup_failed(call: ToolCall, query: str) -> str:
    err = (call.structured or {}).get("error") or {}
    names = [
        str(s.get("name")) for s in err.get("suggestions") or [] if isinstance(s, dict) and s.get("name")
    ]
    message = err.get("message") or call.summary or f"I couldn't find a deal called {query}."
    return f"{message} Did you mean {join_words(names[:3])}?" if names else (call.summary or message)


# ---------------------------------------------------------------------------------------------------------
# The turn.


@dataclass
class _Turn:
    mcp: McpHttp
    message: str
    history: Sequence[tuple[str, str]]
    screen: Screen | None
    focus: Focus
    kind: str = ""
    segment: str | None = None
    reset_segment: bool = False  # a fresh "what needs my attention" starts from the user's own focus again
    owner_hit: bool = True
    explained: dict[str, Any] = field(default_factory=dict)  # deal_id → explain_deal_risk's result

    async def briefing(
        self, *, limit: int | None = 5, segment: str | None = None, keep: bool = True
    ) -> ToolCall:
        args: dict[str, Any] = {}
        if limit:
            args["limit"] = limit
        if seg := segment or (self.focus.segment if keep else None):
            args["segment"] = seg
        return await self.mcp.call_tool("get_risk_briefing", args)

    async def lookup(
        self, query: str, owner: str | None = None, *, strict: bool = False
    ) -> str | ToolCall | None:
        """A deal id, or the call to say (several matches, or none), or None for a strict miss."""
        args = {"name_query": query} | ({"owner_name": owner} if owner else {})
        found = await self.mcp.call_tool("lookup_deal", args)
        cands = [c for c in (found.structured or {}).get("candidates") or [] if isinstance(c, dict)]
        if strict:  # a name inside another question: only a near-exact match counts
            sure = [c for c in cands if float(c.get("match_score") or 0) >= 0.9]
            if not sure:
                return None
            return str(sure[0]["deal_id"]) if len(sure) == 1 and len(cands) == 1 else found
        if not found.ok or len(cands) != 1:  # none, or no clear winner: never answer about a guess
            return found
        return str(cands[0]["deal_id"])

    def placed(self) -> str | None:
        """The deal at a place ("the second one"): the list just heard, else the board, else the last list."""
        board = [d.id for d in self.screen.board] if self.screen is not None else []
        if self.focus.fresh and self.focus.listed:
            return _at_place(self.message, self.focus.listed)
        return _at_place(self.message, board or self.focus.listed)

    def referred(self) -> str | None:
        """The deal "this" or "it" means: the open sheet for "this", the deal last discussed for "it"."""
        open_deal = self.screen.open_deal if self.screen is not None else None
        if THIS.search(self.message) and open_deal:
            return open_deal
        return self.focus.deal or open_deal

    async def about_deal(self, deal_id: str, kinds: Sequence[str]) -> str:
        msg = self.message
        if RESULT.search(msg):
            self.kind = "result"
            return (await self.mcp.call_tool("get_triage_result", {"deal_id": deal_id})).summary
        if TRIAGE.search(msg) and not kinds:
            self.kind = "triage"
            return (await self.mcp.call_tool("start_deep_triage", {"deal_id": deal_id})).summary
        call = await self.mcp.call_tool("explain_deal_risk", {"deal_id": deal_id})
        data = call.structured or {}
        if call.ok:
            self.explained[deal_id] = data
        self.kind = "explain"
        if not call.ok or not data.get("deal"):
            return call.summary
        if kinds:
            self.kind = ",".join(kinds)
            facts = say_deal(kinds, data)
            return f"{call.summary} {facts}" if WHY.search(msg) else facts
        if EVERYTHING.search(msg):
            self.kind = "detail"
            return f"{say_detail(data)} {say_deal(['next', 'trend'], data)}"
        if DETAIL.search(msg):
            self.kind = "detail"
            return say_detail(data)
        return call.summary

    async def close_in_window(self, deal_id: str) -> str:
        """ "When does Harbor close this month?", "Does Harbor close before November?": the close date,
        and whether it falls in that window."""
        answer = await self.about_deal(deal_id, ["close"])
        m = WINDOW.search(self.message)
        if not m and not MONTH_WINDOW.search(self.message):
            return answer
        briefing = await self.briefing(limit=1, keep=False)
        try:
            today = datetime.fromisoformat(str((briefing.structured or {}).get("generated_at"))).date()
            closes = date.fromisoformat(
                str((self.explained.get(deal_id, {}).get("deal") or {}).get("close_date"))
            )
        except ValueError:
            return answer
        if m:
            start, end = _window(m.group(1).lower(), m.group(2).lower(), today)
            words = f"{m.group(1).lower()} {m.group(2).lower()}"
            return f"{answer} That is {'within' if start <= closes <= end else 'not in'} {words}."
        named = _month_window(self.message, today)
        if named is None:
            return answer
        start, end, words = named
        return f"{answer} So it {'closes' if start <= closes <= end else 'does not close'} {words}."

    async def aggregate(self, kind: str, *, owner: str | None = None, segment: str | None = None) -> str:
        msg, band = self.message, _band(self.message)
        self.kind = kind
        if segment:
            self.segment = segment
        if kind == "briefing":
            self.reset_segment = not segment
            call = await self.briefing(limit=None, segment=segment, keep=False)
            return ("Across all segments: " if call.ok and segment == "all" else "") + call.summary
        if kind == "list":
            asked = _top_n(msg)
            # A band is picked out of the top five: "the top 3 at-risk deals" need not be the top 3 overall.
            call = await self.briefing(limit=5 if band else max(1, min(asked, 5)), segment=segment)
            return say_list(call.structured or {}, band, asked) if call.ok else call.summary
        call = await self.briefing(segment=segment)
        data = call.structured or {}
        if not call.ok:
            return call.summary
        if kind == "total":
            return say_total(data, band)
        if kind == "count":
            return say_count(data, band)
        if kind == "compare":
            return say_compare(data)
        if kind == "new":
            return say_new(data)
        if kind == "biggest":
            return say_biggest(data, band)
        if kind == "owners" and owner:
            self.owner_hit = bool(_owned(data, owner))
            return say_owner(data, owner)
        if kind == "ranking":
            return say_ranking(data, band)
        if kind == "over":
            amount = parse_amount(msg)
            return say_over(data, band, *amount) if amount else call.summary
        if kind == "closing":
            cards = []
            for d in data.get("deals") or []:
                if isinstance(d, dict) and d.get("deal_id"):
                    card = await self.mcp.call_tool("explain_deal_risk", {"deal_id": d["deal_id"]})
                    if card.ok and (card.structured or {}).get("deal"):
                        cards.append(card.structured["deal"])  # type: ignore[index]
            return say_closing(data, cards, msg)
        return call.summary

    async def priority(self, superlative: bool = False) -> str:
        self.kind = "priority"
        self.reset_segment = True
        call = await self.briefing(limit=5, keep=False)
        deals = [d for d in (call.structured or {}).get("deals") or [] if isinstance(d, dict)]
        if not call.ok or not deals:
            return call.summary
        explained = await self.mcp.call_tool("explain_deal_risk", {"deal_id": deals[0]["deal_id"]})
        if not explained.ok or not (explained.structured or {}).get("deal"):
            return call.summary
        return say_priority(deals[0], explained.structured or {}, superlative)


async def offline_answer(
    mcp: McpHttp,
    message: str,
    history: Sequence[tuple[str, str]] = (),
    pending: Pending | None = None,
    screen: Screen | None = None,
    focus: Focus | None = None,
) -> tuple[str, Focus]:
    """The reply, and the focus for the next turn (from this turn's tool results).

    ``pending``: the preview waiting for a yes, a held write, or the action just confirmed (agent.Pending).
    ``screen``: what the page shows: "this deal" is the open sheet and "the second one" the second deal
    on the board, unless a list was just read out. ``focus``: what the conversation was about; ignored
    when there is no history (a new conversation).
    """
    start = len(mcp.calls)
    turn = _Turn(mcp, message, history, screen, (focus if history else None) or Focus())
    reply = await _route(turn, pending)
    after = next_focus(turn.focus, mcp.calls[start:], turn.kind, turn.segment)
    if turn.reset_segment and turn.segment is None:
        after.segment = None
    return reply, after


async def offline_turn(
    mcp: McpHttp,
    message: str,
    history: Sequence[tuple[str, str]] = (),
    pending: Pending | None = None,
    screen: Screen | None = None,
    focus: Focus | None = None,
) -> str:
    return (await offline_answer(mcp, message, history, pending, screen, focus))[0]


async def _route(turn: _Turn, pending: Pending | None) -> str:
    mcp, raw = turn.mcp, turn.message
    greeting = ""
    g = GREETING.match(raw)
    if g and not YES.fullmatch(raw):
        greeting = f"{g.group(1).capitalize()}. "
        turn.message = raw[g.end() :].strip()
    msg = turn.message

    # A yes, a no or an undo, about what waits from an earlier turn.
    if pending and pending.state == "ask" and YES.fullmatch(msg):
        return (await mcp.call_tool(pending.tool, dict(pending.args))).summary
    if pending and pending.state == "preview" and YES.fullmatch(msg):
        return (await mcp.call_tool("confirm_action", {"confirmation_token": pending.token})).summary
    if pending and pending.state == "confirmed" and UNDO.search(msg):
        return (await mcp.call_tool("undo_action", {"confirmation_token": pending.token})).summary
    if pending and pending.state in ("preview", "ask") and YES_BUT.match(msg) and not YES.fullmatch(msg):
        return (
            "I can't change the details without the assistant model, which isn't available right now. "
            "Say yes to go ahead exactly as it is, or no to drop it."
        )
    if UNDO.search(msg):
        return "There's nothing I can undo right now. A change can be undone for 30 seconds after you confirm it."
    if YES.fullmatch(msg):
        return "There's nothing waiting for a yes right now."
    if ACK.fullmatch(msg):
        return "Okay."
    if declines(msg):
        if pending and pending.state == "confirmed":
            return "That's already done. Say undo to reverse it."
        return "Okay, I've dropped it. Nothing was changed." if pending else "Okay, nothing was changed."
    if THANKS.fullmatch(msg):
        return "You're welcome."
    if HELP.fullmatch(msg):
        return HELP_TEXT
    if SMALL_TALK.fullmatch(msg) or (greeting and not msg):
        return greeting + await turn.aggregate("briefing")

    for kind, pattern in WRITES:
        if kind == "update" and ASKING.match(msg):
            continue  # "when did they push the close date?" asks about an update; it doesn't make one
        if pattern.search(msg):
            if kind == "stage":
                return (
                    "Watchfloor doesn't change a deal's stage: that's done in your CRM, and the next sync "
                    "picks it up. Nothing was changed."
                )
            return (
                f"{NEEDS_MODEL[kind]} needs the assistant model, which isn't available right now, so "
                "nothing was changed. I can still answer questions about your deals."
            )
    if STANDING.search(msg):
        return greeting + (await mcp.call_tool("list_standing_orders", {})).summary
    if WHO_MOST.search(msg):
        return greeting + await turn.aggregate("ranking")
    if PRIORITY.search(msg):
        return greeting + await turn.priority(bool(SUPERLATIVE.search(msg)))
    reply = await _ask(turn)
    if FORECAST.search(msg):  # the engine reports where a deal stands; it doesn't predict
        reply = f"I can't forecast whether a deal will close, but here is where it stands. {reply}"
    return greeting + reply


async def _ask(turn: _Turn) -> str:
    msg = turn.message
    owner, rest = _without_owner(msg)
    agg = _aggregate(rest)
    # "Does Harbor close before November?": the month names the window, not the deal.
    name = deal_words(MONTH_WINDOW.sub(" ", rest) if agg == "closing" else rest)
    kinds = _kinds(rest)
    seg = _segment(rest)
    elliptical = bool(ELLIPSIS.match(msg))
    asks = bool(ASK_DEAL.search(rest) or RESULT.search(rest) or TRIAGE.search(rest) or kinds)
    last_kinds = [k for k in turn.focus.kind.split(",") if k in DEAL_KINDS]

    async def named(query: str, use: Sequence[str], who: str | None = None) -> str:
        found = await turn.lookup(query, who)
        if isinstance(found, ToolCall):
            return found.summary if found.ok else _lookup_failed(found, query)
        return await turn.about_deal(found, use) if found else WHICH

    # Questions this engine can't answer from a briefing: say so, never answer a different question.
    if not name:
        for pattern, text in UNSUPPORTED:
            if pattern.search(msg):
                return text

    # "The second one": a place in the list just heard, on the board, or last listed.
    if _place(msg) is not None:
        deal_id = turn.placed()
        if deal_id is None:
            board = turn.screen.board if turn.screen is not None else []
            if board:
                return "There isn't a deal in that place on the board."
            if turn.focus.listed:
                return "There isn't a deal in that place in the list."
            return "I can't tell which deal that is. Open the pipeline, or say the deal's name."
        return await turn.about_deal(deal_id, kinds or ([] if asks else last_kinds))

    # "It", "this deal": a deal the user doesn't name.
    if (PRONOUN.search(msg) or THIS.search(msg)) and not name and (asks or elliptical):
        deal_id = turn.referred()
        if deal_id is None and (query := recent_deal(turn.history)):
            return await named(query, kinds)
        if deal_id is None:
            return WHICH if asks or kinds else FALLBACK
        return await turn.about_deal(deal_id, kinds)

    # A deal named in words: looked up for real when the question is about a deal, strictly otherwise.
    # A question about the whole pipeline with a stray word in it ("versus a week ago") is not a lookup.
    # A name in it counts only when it matches a deal exactly (the agg branch below).
    pipeline_first = bool(agg) and not owner
    if name and (owner or asks or elliptical) and not pipeline_first:
        return await named(name, kinds or ([] if asks else last_kinds), owner)
    if owner and not name:
        text = await turn.aggregate("owners", owner=owner, segment=seg)
        if not turn.owner_hit:  # "Kestrel's deals": no rep of that name has one, so is it a company?
            found = await turn.lookup(owner, strict=True)
            if isinstance(found, ToolCall):
                return found.summary
            if found is not None:
                return await turn.about_deal(found, kinds)
        return text
    if agg:
        if name:
            found = await turn.lookup(name, strict=True)
            if isinstance(found, ToolCall):
                return found.summary
            if found is not None:
                if agg == "closing" and isinstance(found, str):
                    return await turn.close_in_window(found)
                return await turn.about_deal(
                    found, kinds or {"compare": ["trend"], "total": ["amount"]}.get(agg, [])
                )
        return await turn.aggregate(agg, segment=seg)
    if (GENERAL.search(msg) or OVERVIEW.search(msg)) and not name:
        return await turn.aggregate("briefing")
    if seg:
        again = turn.focus.kind if elliptical and turn.focus.kind in AGG_KINDS else "briefing"
        return await turn.aggregate(again, segment=seg)
    if asks and not name:
        # "What's the next step?", "Why?", "Dig in": the deal this conversation is about, if there is one.
        deal_id = turn.focus.deal or (turn.screen.open_deal if turn.screen is not None else None)
        if deal_id is None and (query := recent_deal(turn.history)):
            return await named(query, kinds)
        return await turn.about_deal(deal_id, kinds) if deal_id else WHICH
    if BRIEFING.search(msg):
        return await turn.aggregate("briefing")
    if name:
        found = await turn.lookup(name, strict=True)
        if isinstance(found, ToolCall):
            return found.summary
        if found is not None:
            return await turn.about_deal(found, last_kinds if elliptical else [])
    if elliptical and _band(msg):
        again = turn.focus.kind if turn.focus.kind in ("total", "count", "list", "biggest") else "count"
        return await turn.aggregate(again)
    return FALLBACK
