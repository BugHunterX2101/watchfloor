"""A refresh token is shown to the authorization server once: it rotates, and a second showing is theft."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from conftest import FakeAuth, FakeMcp, sign_in
from harness_api.pkce import OAuthError
from harness_api.refresh import KEEP_S, Refreshes
from harness_api.session import Session


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


async def test_requests_that_meet_an_expired_token_together_refresh_once() -> None:
    refreshes, calls, gate = Refreshes(), [], asyncio.Event()

    async def work() -> Session:
        calls.append(1)
        await gate.wait()  # the authorization server is slow: everyone else arrives meanwhile
        return Session("at2", "rt2", 9e9)

    tasks = [asyncio.create_task(refreshes.run("rt1", work)) for _ in range(5)]
    await asyncio.sleep(0)
    gate.set()
    sessions = await asyncio.gather(*tasks)
    assert len(calls) == 1 and {s.access_token for s in sessions} == {"at2"}
    assert not refreshes._locks and not refreshes._waiting  # nothing is left behind


async def test_the_answer_is_kept_for_a_moment_and_then_forgotten() -> None:
    clock = Clock()
    refreshes, calls = Refreshes(now=clock), []

    async def work() -> Session:
        calls.append(1)
        return Session(f"at{len(calls) + 1}", "rt2", 9e9)

    assert (await refreshes.run("rt1", work)).access_token == "at2"
    assert (await refreshes.run("rt1", work)).access_token == "at2"  # the cookie race
    assert len(calls) == 1
    clock.now += KEEP_S + 1
    assert (await refreshes.run("rt1", work)).access_token == "at3"  # not held on to
    assert len(calls) == 2


async def test_a_refusal_is_not_kept() -> None:
    refreshes, calls = Refreshes(), []

    async def work() -> Session:
        calls.append(1)
        if len(calls) == 1:
            raise OAuthError("invalid_grant")
        return Session("at2", "rt2", 9e9)

    with pytest.raises(OAuthError):
        await refreshes.run("rt1", work)
    assert (await refreshes.run("rt1", work)).access_token == "at2"
    assert not refreshes._locks and not refreshes._waiting


async def test_a_request_sent_with_the_old_cookie_does_not_show_the_old_refresh_token_again(
    client: httpx.AsyncClient, auth: FakeAuth, mcp: FakeMcp
) -> None:
    await sign_in(client)
    old = client.cookies.get("wf_h")
    mcp.valid.discard("at1")
    first = await client.post("/api/tool", json={"name": "get_risk_briefing", "arguments": {}})
    assert first.status_code == 200 and auth.issued == 2  # one refresh

    # A second request left with the cookie from before the first one's answer arrived.
    second = await client.post(
        "/api/tool",
        json={"name": "get_risk_briefing", "arguments": {}},
        headers={"cookie": f"wf_h={old}"},
    )
    assert second.status_code == 200
    assert auth.issued == 2  # the authorization server never saw rt1 twice
    assert "wf_h=" in second.headers["set-cookie"]  # and the browser ends up with the new session
