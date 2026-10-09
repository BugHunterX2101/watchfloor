"""Hand-written demo deals with fixed ids (DD §13.5).

Each scenario is built to land on its documented RIGS score; the arithmetic is
spelled out next to each one and asserted by the seeder and the unit tests.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .model import Account, Activity, Contact, Deal, History, det_id

WORKSPACE_ID = "ws_lumenridge"
WORKSPACE_NAME = "Lumen Ridge Software"
TIMEZONE = "America/Los_Angeles"

PRIYA = "us_01J9PRIYA000000000000000"
MARCUS = "us_01J9MARCUS00000000000000"
MAYA = "us_01J9MAYA0000000000000000"
DANIEL = "us_01J9DANIEL00000000000000"


@dataclass(frozen=True)
class SeedUser:
    id: str
    display_name: str
    email: str
    role: str
    slack_user_id: str | None


USERS = (
    SeedUser(PRIYA, "Priya Nair", "priya@lumenridge.example", "owner", None),
    SeedUser(MARCUS, "Marcus Bell", "marcus@lumenridge.example", "manager", None),
    SeedUser(MAYA, "Maya Chen", "maya@lumenridge.example", "rep", None),
    SeedUser(DANIEL, "Daniel Reyes", "daniel@lumenridge.example", "rep", None),
)

KESTREL = "dl_01J9KESTREL0000000000000"
HARBOR = "dl_01J9HARBORPINE00000000000"
ORBITAL = "dl_01J9ORBITAL0000000000000"
NORTHGATE = "dl_01J9NORTHGATE00000000000"

EXPECTED = {
    KESTREL: (71, "at_risk"),
    HARBOR: (66, "at_risk"),
    ORBITAL: (58, "watch"),
    NORTHGATE: (18, "healthy"),
}
DEMO_BOARD = (KESTREL, HARBOR, ORBITAL)


def _ago(now: datetime, days: int, hours: int = 0) -> datetime:
    return now - timedelta(days=days, hours=hours)


def build(now: datetime, today: date, rng: random.Random) -> list[Deal]:
    def act(
        t: str, d: str, at: datetime, summary: str, body: str | None = None, contact: str | None = None
    ) -> Activity:
        return Activity(det_id("act", rng, at), t, d, at, summary, body, contact)

    def push(old: date, new: date, at: datetime, by: str) -> History:
        return History(det_id("hist", rng, at), "close_date", old.isoformat(), new.isoformat(), at, by)

    deals: list[Deal] = []

    # Kestrel Biotech: R (16-6)/12=0.833 -> .250 · I 1-1.2/4=0.70 -> .175 · G 2 gaps -> .125
    # S 2 pushes (+34 d, kicker) 0.80 -> .160 · total .710 -> 71 At Risk (DD §7.5)
    acct = Account("ac_01J9KESTREL0000000000000", "Kestrel Biotech", "mid", DANIEL)
    ortiz = Contact("ct_01J9KESTRELORTIZ000000000", "Jamie Ortiz", "Director of Lab Operations")
    acct.contacts = [
        ortiz,
        Contact("ct_01J9KESTRELPATEL000000000", "Ravi Patel", "Research Informatics Lead"),
    ]
    c0 = today + timedelta(days=9)
    c1, c2 = c0 + timedelta(days=17), c0 + timedelta(days=34)
    d = Deal(
        KESTREL,
        acct,
        "Kestrel Biotech · Platform expansion",
        8_400_000,
        "proposal",
        c2,
        DANIEL,
        "Send revised proposal with security addendum",
        today + timedelta(days=4),
        None,
        None,
        _ago(now, 70),
    )
    d.activities = [
        act("email", "inbound", _ago(now, 31), "Asked for pricing for 3 additional labs", contact=ortiz.id),
        act("meeting", "outbound", _ago(now, 25), "Platform demo for lab leads", contact=ortiz.id),
        act("doc_view", "inbound", _ago(now, 20, 4), "Proposal viewed"),
        act(
            "email", "inbound", _ago(now, 16, 2), "Re: Proposal: will review with the team", contact=ortiz.id
        ),
        act("email", "outbound", _ago(now, 12), "Follow-up on proposal questions"),
        act("email", "outbound", _ago(now, 7), "Checking in on timeline"),
        act("email", "outbound", _ago(now, 3), "Offered a call to walk through the security addendum"),
        *[act("doc_view", "inbound", _ago(now, k, 3), "Proposal viewed") for k in (13, 11, 9, 6, 4, 2)],
    ]
    d.history = [push(c0, c1, _ago(now, 20), DANIEL), push(c1, c2, _ago(now, 8), DANIEL)]
    deals.append(d)

    # Harbor & Pine: R (11-4)/8=0.875 -> .2625 · I 1-1/5=0.80 -> .200 · G 2 gaps -> .125
    # S 1 push (+10 d) 0.35 -> .070 · total .6575 -> 66 At Risk
    acct = Account("ac_01J9HARBORPINE00000000000", "Harbor & Pine Logistics", "mid", MAYA)
    cfo = Contact("ct_01J9HARBORCFO000000000000", "Dana Whitfield", "CFO", True)
    ops = Contact("ct_01J9HARBOROPS000000000000", "Luis Moreno", "VP Fleet Operations")
    acct.contacts = [cfo, ops]
    c0 = today + timedelta(days=12)
    d = Deal(
        HARBOR,
        acct,
        "Harbor & Pine Logistics · Fleet rollout",
        5_200_000,
        "negotiation",
        c0 + timedelta(days=10),
        MAYA,
        None,
        None,
        cfo.id,
        "Security review, then CFO sign-off",
        _ago(now, 80),
    )
    d.activities = [
        act("meeting", "outbound", _ago(now, 20), "Commercial terms review", contact=ops.id),
        act(
            "email",
            "inbound",
            _ago(now, 11, 3),
            "Asked for the SOC 2 report; security review pending",
            contact=ops.id,
        ),
        act("email", "outbound", _ago(now, 6), "Sent SOC 2 report and security questionnaire"),
        act("email", "outbound", _ago(now, 2), "Asked for a security review date"),
    ]
    d.history = [push(c0, c0 + timedelta(days=10), _ago(now, 15), MAYA)]
    deals.append(d)

    # Orbital Dental: R (22-8)/16=0.875 -> .2625 · I 1-0.8/3.5=0.771 -> .1929 · G 2 gaps -> .125
    # S 0 · total .5804 -> 58 Watch
    acct = Account("ac_01J9ORBITAL0000000000000", "Orbital Dental Group", "smb", DANIEL)
    owner = Contact("ct_01J9ORBITALOWNER000000000", "Dr. Elena Sato", "Managing Partner", True)
    acct.contacts = [
        owner,
        Contact("ct_01J9ORBITALOPS00000000000", "Tom Becker", "Practice Operations Manager"),
    ]
    d = Deal(
        ORBITAL,
        acct,
        "Orbital Dental Group · 14 clinics",
        3_100_000,
        "demo",
        today + timedelta(days=35),
        DANIEL,
        None,
        None,
        owner.id,
        "Practice owners approve at monthly partner meeting",
        _ago(now, 45),
    )
    d.activities = [
        act("email", "inbound", _ago(now, 30), "Interested in a demo for all 14 clinics", contact=owner.id),
        act("meeting", "outbound", _ago(now, 22, 1), "Demo for practice owners", contact=owner.id),
        act("email", "outbound", _ago(now, 8), "Shared demo recording and pricing sheet"),
        *[act("doc_view", "inbound", _ago(now, k, 5), "Pricing sheet viewed") for k in (12, 9, 5, 1)],
    ]
    deals.append(d)

    # Northgate Foods: R 0 (meeting 2 days ago) · I 1-2.8/5=0.44 -> .110 · G 0
    # S 1 push (+7 d) 0.35 -> .070 · total .180 -> 18 Healthy
    acct = Account("ac_01J9NORTHGATE00000000000", "Northgate Foods", "enterprise", PRIYA)
    vp = Contact("ct_01J9NORTHGATEVP0000000000", "Grace Okafor", "SVP Supply Chain", True)
    acct.contacts = [vp, Contact("ct_01J9NORTHGATELEGAL0000000", "Henry Liu", "Senior Counsel")]
    c0 = today + timedelta(days=10)
    d = Deal(
        NORTHGATE,
        acct,
        "Northgate Foods · Renewal",
        12_000_000,
        "closing",
        c0 + timedelta(days=7),
        PRIYA,
        "Final redlines with legal",
        today + timedelta(days=2),
        vp.id,
        "Legal review, then SVP signature",
        _ago(now, 120),
    )
    d.activities = [
        act("email", "inbound", _ago(now, 16), "Legal has the order form", contact=vp.id),
        act("meeting", "inbound", _ago(now, 2), "Renewal terms call requested by Grace", contact=vp.id),
        *[act("doc_view", "inbound", _ago(now, k, 6), "Order form viewed") for k in (10, 7, 4, 1)],
    ]
    d.history = [push(c0, c0 + timedelta(days=7), _ago(now, 40), PRIYA)]
    deals.append(d)
    return deals
