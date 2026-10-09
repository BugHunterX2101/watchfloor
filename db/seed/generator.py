"""Background accounts and deals, rejection-sampled with the real RIGS engine.

Constraints that keep the demo deterministic (documented in docs/decisions.md):
* no background deal is At Risk, so the curated scenarios lead the board;
* background Watch deals rank below Orbital Dental (score x amount);
* every deal of $50K or more had an inbound reply in the last 4 days, so the
  J4 standing order ("over $50K, silent 5 days") fires on scenario deals only.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from watchfloor_core import rigs
from watchfloor_core.rigs_params import RigsParams

from . import names
from .hostile_notes import HOSTILE_NOTES
from .model import Account, Activity, Contact, Deal, History, det_id, snapshot
from .scenarios import DANIEL, MARCUS, MAYA, PRIYA

OWNERS = (MAYA, DANIEL, MAYA, DANIEL, MARCUS, PRIYA)
STAGES = (("discovery", 25), ("demo", 25), ("proposal", 20), ("negotiation", 15), ("closing", 15))
AMOUNTS = {"smb": (8_000, 40_000), "mid": (25_000, 90_000), "enterprise": (60_000, 250_000)}
BIG_DEAL_CENTS = 5_000_000
HOSTILE_DEALS = 10
ORBITAL_RANK = 58 * 3_100_000


def _company_names(rng: random.Random, n: int) -> list[str]:
    pool = [f"{p} {s}" for p in names.PREFIX for s in names.SUFFIX]
    rng.shuffle(pool)
    return pool[:n]


def _person(rng: random.Random) -> str:
    return f"{rng.choice(names.FIRST)} {rng.choice(names.LAST)}"


def build(
    now: datetime,
    today: date,
    rng: random.Random,
    params: RigsParams,
    *,
    accounts: int = 36,
    open_deals: int = 114,
    closed_deals: int = 18,
) -> tuple[list[Deal], list[Deal]]:
    accts: list[Account] = []
    for name in _company_names(rng, accounts):
        seg = rng.choices(("smb", "mid", "enterprise"), weights=(40, 40, 20))[0]
        acct = Account(det_id("ac", rng, now - timedelta(days=200)), name, seg, rng.choice(OWNERS))
        for k in range(rng.randint(2, 4)):
            eb = k == 0
            acct.contacts.append(
                Contact(
                    det_id("ct", rng, now - timedelta(days=200)),
                    _person(rng),
                    rng.choice(names.EB_TITLES if eb else names.TITLES),
                    eb,
                )
            )
        accts.append(acct)

    open_out = [_sample_open_deal(accts[i % len(accts)], now, today, rng, params) for i in range(open_deals)]
    # Ten healthy deals carry hostile free text for the injection evals (DD §10.6, §22.5). The first
    # two draw ids from the main stream, as they always have; the other eight use their own streams,
    # so adding them changed no other row. A deal the notes would lift out of Healthy is skipped.
    hostile, index = 0, 0
    while hostile < HOSTILE_DEALS:
        target, index = open_out[index], index + (7 if hostile < 2 else 5)
        stream = rng if hostile < 2 else random.Random(f"hostile-{hostile}")
        target.activities += [
            Activity(
                det_id("act", stream, now - timedelta(days=3)),
                "note",
                "internal",
                now - timedelta(days=3),
                "Imported note from shared inbox",
                HOSTILE_NOTES[hostile % 4],
            ),
            Activity(
                det_id("act", stream, now - timedelta(days=40)),
                "email",
                "inbound",
                now - timedelta(days=40),
                "Customer email",
                HOSTILE_NOTES[(hostile + 2) % 4],
            ),
        ]
        score = rigs.score(snapshot(target, now, today), params).score
        if hostile >= 2 and rigs.band_for(score, None, params.bands) != "healthy":
            del target.activities[-2:]
            continue
        hostile += 1

    closed_out: list[Deal] = []
    for _ in range(closed_deals):
        acct = rng.choice(accts)
        lo, hi = AMOUNTS[acct.segment]
        closed_at = today - timedelta(days=rng.randint(20, 170))
        closed_out.append(
            Deal(
                det_id("dl", rng, now - timedelta(days=200)),
                acct,
                f"{acct.name} · {rng.choice(names.PRODUCTS)}",
                rng.randint(lo // 1000, hi // 1000) * 100_000,
                rng.choice(("won", "won", "lost")),
                closed_at,
                acct.owner_id,
                None,
                None,
                None,
                None,
                now - timedelta(days=200),
            )
        )
    return open_out, closed_out


def _sample_open_deal(
    acct: Account, now: datetime, today: date, rng: random.Random, params: RigsParams
) -> Deal:
    for _attempt in range(500):
        deal = _candidate(acct, now, today, rng, params)
        res = rigs.score(snapshot(deal, now, today), params)
        band = rigs.band_for(res.score, None, params.bands)
        if band == "at_risk":
            continue
        if band == "watch" and (
            deal.amount_cents >= BIG_DEAL_CENTS or res.score * deal.amount_cents >= 0.9 * ORBITAL_RANK
        ):
            continue
        return deal
    raise RuntimeError("could not sample a background deal within constraints")


def _candidate(acct: Account, now: datetime, today: date, rng: random.Random, params: RigsParams) -> Deal:
    stage = rng.choices([s for s, _ in STAGES], weights=[w for _, w in STAGES])[0]
    sp = params.stage(stage)
    lo, hi = AMOUNTS[acct.segment]
    amount = rng.randint(lo // 1000, hi // 1000) * 100_000
    big = amount >= BIG_DEAL_CENTS
    created = now - timedelta(days=rng.randint(30, 180))
    eb = next((c.id for c in acct.contacts if c.is_economic_buyer), None)
    contact = rng.choice(acct.contacts).id

    def a(t: str, d: str, days: float, summary: str) -> Activity:
        at = now - timedelta(days=days)
        return Activity(det_id("act", rng, at), t, d, at, summary, None, contact if d == "inbound" else None)

    watchish = rng.random() < 0.22
    silent = rng.randint(sp.norm_days, 3 * sp.norm_days) if watchish else rng.randint(0, sp.norm_days)
    if big:
        silent = min(silent, rng.randint(0, 4))
    acts: list[Activity] = []
    # The most recent two-way touch is designed explicitly; for big deals it is an inbound reply.
    if big or rng.random() < 0.6:
        acts.append(a("email", "inbound", silent + rng.random() * 0.5, "Reply from buyer"))
    else:
        acts.append(a("meeting", "outbound", silent + rng.random() * 0.5, "Working session"))
    # Extra two-way engagement inside the window, never newer than the designed last touch.
    for _ in range(rng.randint(0, 1) if watchish else rng.randint(0, 3)):
        if silent < 13:
            acts.append(
                a(
                    rng.choice(("email", "meeting")),
                    "inbound",
                    rng.uniform(silent + 0.6, 13.5),
                    "Earlier conversation",
                )
            )
    for _ in range(rng.randint(0, 5)):
        acts.append(a("doc_view", "inbound", rng.uniform(0.2, 13.5), "Document viewed"))
    for _ in range(rng.randint(1, 4)):
        acts.append(a("email", "outbound", rng.uniform(0.2, 20), "Follow-up from rep"))
    # Older history strictly outside the window and older than the last touch.
    t = max(silent, 15) + rng.uniform(1, 6)
    while t < (now - created).days:
        acts.append(a(rng.choice(("email", "meeting", "call")), "inbound", t, "Earlier conversation"))
        t += rng.uniform(6, 16)
    for act in acts:
        if act.type == "meeting":
            act.direction = "outbound"

    history: list[History] = []
    close = today + timedelta(days=rng.randint(10, 90))
    if rng.random() < (0.35 if watchish else 0.12):
        old = close - timedelta(days=rng.randint(5, 20))
        at = now - timedelta(days=rng.randint(5, 60))
        history.append(
            History(
                det_id("hist", rng, at), "close_date", old.isoformat(), close.isoformat(), at, acct.owner_id
            )
        )

    gap_p = 0.35 if watchish else 0.08
    has_step = rng.random() > gap_p
    return Deal(
        id=det_id("dl", rng, created),
        account=acct,
        name=f"{acct.name} · {rng.choice(names.PRODUCTS)}",
        amount_cents=amount,
        stage=stage,
        close_date=close,
        owner_id=acct.owner_id,
        next_step="Follow-up call" if has_step else None,
        next_step_date=today + timedelta(days=rng.randint(1, 10))
        if has_step and rng.random() > gap_p
        else None,
        economic_buyer_id=eb if rng.random() > gap_p else None,
        decision_process="Standard procurement" if rng.random() > gap_p else None,
        created_at=created,
        activities=acts,
        history=history,
    )
