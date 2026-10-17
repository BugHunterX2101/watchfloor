"""The outbox under duplicate deliveries, crashes, lease expiry, redrive and undo races (plan phase 3).

Delivery is at-least-once: Slack's webhook has no idempotency key, so a worker that dies between
Slack's "ok" and the commit that records it makes one repeat post unavoidable. What these tests pin
down is everything that *is* avoidable, and that the unavoidable case stays at one repeat per crash.
"""

from __future__ import annotations

import asyncio
import secrets
from collections import Counter
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from watchfloor_core import db
from watchfloor_core.clock import FixedClock
from watchfloor_core.repos import outbox
from watchfloor_core.services.context import set_clock
from workers import dispatcher
from workers.errors import GiveUp

from tests.conftest import FROZEN_NOW, WS
from tests.integration.test_workers import Fakes, _enqueue, _row

SLACK = {"channel": "#deals-at-risk", "message": {"text": "hello", "blocks": []}}


@pytest.fixture
def at() -> Iterator[Any]:
    """Move the frozen clock: ``at(timedelta)`` puts "now" that far past FROZEN_NOW."""

    def move(delta: timedelta) -> None:
        set_clock(FixedClock(FROZEN_NOW + delta))

    yield move
    set_clock(FixedClock(FROZEN_NOW))


async def test_one_message_given_to_many_workers_at_once_posts_once(seeded: str) -> None:
    fakes = Fakes()
    oid = await _enqueue("slack_message", SLACK)
    outcomes = await asyncio.gather(*(dispatcher.process(WS, oid, fakes.deps()) for _ in range(8)))

    assert Counter(outcomes) == {"sent": 1, "skipped": 7}
    assert len(fakes.posts) == 1
    assert (await _row(oid))["attempts"] == 1


async def test_a_worker_that_dies_mid_delivery_holds_the_row_only_until_its_lease_ends(
    seeded: str, at: Any
) -> None:
    fakes = Fakes()
    oid = await _enqueue("slack_message", SLACK)
    async with db.ws_tx(WS) as conn:  # the doomed worker claims the row, then is killed before reporting
        assert await outbox.claim(conn, oid, now=FROZEN_NOW, lease=dispatcher.LEASE)

    assert await dispatcher.process(WS, oid, fakes.deps()) == "skipped"
    at(dispatcher.LEASE - timedelta(seconds=1))
    assert await dispatcher.process(WS, oid, fakes.deps()) == "skipped", "still inside the lease"
    assert fakes.posts == []

    at(dispatcher.LEASE)
    assert await dispatcher.process(WS, oid, fakes.deps()) == "sent"
    assert len(fakes.posts) == 1 and (await _row(oid))["attempts"] == 2


async def test_a_crash_after_the_send_repeats_the_post_once_then_stops(
    seeded: str, at: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unavoidable duplicate: Slack said ok, then the database went away before "sent" was recorded."""
    fakes = Fakes()
    oid = await _enqueue("slack_message", SLACK)
    real, crashes = outbox.mark_sent, []

    async def mark_sent_then_lose_the_database(conn: Any, outbox_id: int, when: Any) -> None:
        if not crashes:
            crashes.append(outbox_id)
            raise ConnectionResetError("the database went away")
        await real(conn, outbox_id, when)

    monkeypatch.setattr(outbox, "mark_sent", mark_sent_then_lose_the_database)
    with pytest.raises(ConnectionResetError):
        await dispatcher.process(WS, oid, fakes.deps())
    assert len(fakes.posts) == 1
    row = await _row(oid)
    assert row["status"] == "pending" and row["attempts"] == 1, "nothing was lost: the row is still owed"

    at(dispatcher.LEASE)
    assert await dispatcher.process(WS, oid, fakes.deps()) == "sent"
    assert len(fakes.posts) == 2, "one repeat for the one crash"
    assert await dispatcher.process(WS, oid, fakes.deps()) == "skipped"
    assert len(fakes.posts) == 2, "and no more after that"


async def test_a_database_that_fails_before_the_claim_posts_nothing_and_loses_nothing(
    seeded: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fakes = Fakes()
    oid = await _enqueue("slack_message", SLACK)
    real, failures = outbox.claim, []

    async def claim_once_broken(conn: Any, outbox_id: int, **kwargs: Any) -> Any:
        if not failures:
            failures.append(outbox_id)
            raise TimeoutError("no database connection")
        return await real(conn, outbox_id, **kwargs)

    monkeypatch.setattr(outbox, "claim", claim_once_broken)
    with pytest.raises(TimeoutError):
        await dispatcher.process(WS, oid, fakes.deps())
    row = await _row(oid)
    assert fakes.posts == [] and row["status"] == "pending" and row["attempts"] == 0

    assert await dispatcher.process(WS, oid, fakes.deps()) == "sent"
    assert len(fakes.posts) == 1


async def test_redriving_a_dead_row_from_many_hands_rearms_it_once_and_delivers_it_once(seeded: str) -> None:
    fakes = Fakes(slack_error=GiveUp("slack 400: invalid_blocks"))
    oid = await _enqueue("slack_message", SLACK)
    assert await dispatcher.process(WS, oid, fakes.deps()) == "dead"

    async def rearm() -> bool:
        async with db.ws_tx(WS) as conn:
            return await outbox.rearm(conn, oid, FROZEN_NOW)

    assert sorted(await asyncio.gather(rearm(), rearm(), rearm())) == [False, False, True]

    fakes.slack_error = None  # the operator fixed the message
    outcomes = await asyncio.gather(*(dispatcher.process(WS, oid, fakes.deps()) for _ in range(4)))
    assert Counter(outcomes) == {"sent": 1, "skipped": 3}
    assert len(fakes.posts) == 1
    assert await rearm() is False, "a delivered row cannot be redriven into a second post"


async def test_a_withdrawal_racing_its_delivery_never_does_both(seeded: str) -> None:
    """Undo cancels the held Slack post; if a worker has already taken the row, the undo is too late."""
    withdrawn = delivered = 0
    for _ in range(15):
        fakes = Fakes()
        key = f"race:{secrets.token_hex(6)}"
        async with db.ws_tx(WS) as conn:
            oid = await outbox.enqueue(
                conn, workspace_id=WS, kind="slack_message", payload=SLACK, dedupe_key=key, at=FROZEN_NOW
            )
        assert oid is not None

        async def withdraw(dedupe_key: str = key) -> bool:
            async with db.ws_tx(WS) as conn:
                return await outbox.cancel(conn, dedupe_key)

        cancelled, outcome = await asyncio.gather(withdraw(), dispatcher.process(WS, oid, fakes.deps()))
        row = await _row(oid)
        if cancelled:
            withdrawn += 1
            assert row["status"] == "cancelled" and fakes.posts == [] and outcome == "skipped"
        else:
            delivered += 1
            assert row["status"] == "sent" and len(fakes.posts) == 1 and outcome == "sent"
    assert withdrawn + delivered == 15


async def test_the_same_dedupe_key_enqueued_at_once_makes_one_row(seeded: str) -> None:
    key = f"dup:{secrets.token_hex(6)}"

    async def enqueue() -> int | None:
        async with db.ws_tx(WS) as conn:
            return await outbox.enqueue(
                conn, workspace_id=WS, kind="slack_message", payload=SLACK, dedupe_key=key, at=FROZEN_NOW
            )

    ids = await asyncio.gather(*(enqueue() for _ in range(8)))
    assert sum(i is not None for i in ids) == 1
