"""One refresh at a time per refresh token, and the answer kept for a moment.

The authorization server rotates refresh tokens and treats a rotated one shown again as theft: it
revokes the whole family (RFC 9700 §4.14). The harness keeps the tokens in the browser's cookie and
refreshes on demand, so two requests that meet an expired access token together, or a request sent
with the old cookie just before the new one arrived, would both present the same refresh token and
sign the person out for nothing. Here the second one waits for the first and takes its result.

State is per process, like the rate limits (limits.py): exact for the one container the shipped
configs run.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable

from .session import Session

KEEP_S = 30.0  # long enough for the cookie race, short enough that old tokens are not held
MAX_KEPT = 1_000


def _key(refresh_token: str) -> str:
    return hashlib.sha256(refresh_token.encode()).hexdigest()[:24]


class Refreshes:
    def __init__(self, *, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._kept: dict[str, tuple[float, Session]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._waiting: dict[str, int] = {}

    def _forget_old(self) -> None:
        now = self._now()
        for key in [k for k, (until, _) in self._kept.items() if until <= now]:
            del self._kept[key]
        while len(self._kept) >= MAX_KEPT:
            del self._kept[next(iter(self._kept))]

    async def run(self, refresh_token: str, work: Callable[[], Awaitable[Session]]) -> Session:
        """The new session for ``refresh_token``: ``work()`` once, whoever else asks meanwhile.

        A refusal is not kept: it propagates, and the next caller asks the server for itself.
        """
        key = _key(refresh_token)
        self._forget_old()
        if (hit := self._kept.get(key)) is not None:
            return hit[1]
        lock = self._locks.setdefault(key, asyncio.Lock())
        self._waiting[key] = self._waiting.get(key, 0) + 1
        try:
            async with lock:
                if (hit := self._kept.get(key)) is not None:
                    return hit[1]
                session = await work()
                self._kept[key] = (self._now() + KEEP_S, session)
                return session
        finally:
            self._waiting[key] -= 1
            if not self._waiting[key]:
                del self._waiting[key]
                self._locks.pop(key, None)
