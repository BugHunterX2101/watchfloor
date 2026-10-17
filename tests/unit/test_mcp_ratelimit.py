"""The MCP rate limiter: a noisy caller is refused without using up the workspace's allowance."""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from mcp_server.middleware import ratelimit


async def _ok(scope: Any, receive: Any, send: Any) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b""})


async def _call(app: Any, subject: str, workspace: str = "ws_a") -> int:
    principal = SimpleNamespace(rate_subject=subject, workspace_id=workspace)
    scope = {"type": "http", "path": "/mcp", "method": "POST", "state": {"principal": principal}}
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return int(sent[0]["status"])


@pytest.fixture
def counts(monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    hits: Counter[str] = Counter()

    async def rate_hit(_conn: Any, subject: str) -> int:
        hits[subject] += 1
        return hits[subject]

    @asynccontextmanager
    async def tx() -> AsyncIterator[None]:
        yield None

    monkeypatch.setattr(ratelimit, "system_tx", tx)
    monkeypatch.setattr(ratelimit.misc, "rate_hit", rate_hit)
    return hits


async def test_refused_requests_are_not_charged_to_the_workspace(counts: Counter[str]) -> None:
    app = ratelimit.RateLimit(_ok, per_user=2, per_ws=3)
    assert [await _call(app, "alice") for _ in range(5)] == [200, 200, 429, 429, 429]
    assert counts["ws:ws_a"] == 2
    assert await _call(app, "bob") == 200, "a colleague is still served after alice was refused"


async def test_the_workspace_limit_still_holds(counts: Counter[str]) -> None:
    app = ratelimit.RateLimit(_ok, per_user=10, per_ws=3)
    statuses = [await _call(app, user) for user in ("alice", "bob", "cara", "dan")]
    assert statuses == [200, 200, 200, 429]
