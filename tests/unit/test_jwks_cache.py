"""The key cache: an outage at cold start costs one failed fetch, not one per waiting request."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from mcp_server.middleware.auth import JwksCache, JwksUnavailable

from tests.conftest import KID


class _Fetched(JwksCache):
    """A cache whose fetch is scripted: it fails while ``doc`` is None, then serves it."""

    def __init__(self, doc: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__("https://auth.example/jwks.json", **kwargs)
        self.doc = doc
        self.loads = 0

    async def _load(self) -> dict[str, Any]:
        self.loads += 1
        await asyncio.sleep(0)  # let the other waiting requests queue behind the lock
        if self.doc is None:
            raise OSError("connection refused")
        return self.doc


async def test_waiting_requests_share_one_failed_fetch() -> None:
    cache = _Fetched()
    results = await asyncio.gather(*(cache.key(KID) for _ in range(5)), return_exceptions=True)
    assert all(isinstance(r, JwksUnavailable) for r in results)
    assert cache.loads == 1


async def test_the_cache_recovers_once_the_window_has_passed(signing_key: tuple[Any, dict[str, Any]]) -> None:
    _, jwks = signing_key
    cache = _Fetched(retry_after_s=0)
    with pytest.raises(JwksUnavailable):
        await cache.key(KID)
    cache.doc = jwks
    key = await cache.key(KID)
    assert key is not None and key.key_id == KID
