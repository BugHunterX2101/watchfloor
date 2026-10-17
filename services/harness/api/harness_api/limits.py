"""A ceiling on what one signed-in browser can ask for, per minute.

A turn calls a language model, which costs credits (or a free quota). The MCP server limits tool calls,
but only after the model has already run, so the harness counts first. The window slides: the n-th
request in the last 60 seconds is refused, and the refusal says how long until a slot frees up.

State is per process. That is exact for the one container the shipped configs run, and a per-instance
ceiling anywhere else, which still bounds what any one instance can spend.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections import deque
from collections.abc import Callable

WINDOW_S = 60.0
MAX_KEYS = 10_000


def _key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def subject(access_token: str) -> str:
    """Who a session belongs to, as a key that does not keep the token in memory. The access token is
    read, not trusted: the MCP server checks its signature, and this only chooses a counter. A token
    that cannot be read is counted on its own."""
    try:
        payload = access_token.split(".")[1]
        sub = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("sub")
        if isinstance(sub, str) and sub:
            return _key(sub)
    except (IndexError, ValueError, AttributeError):
        pass
    return _key(access_token)


class Limiter:
    def __init__(self, per_minute: int, *, now: Callable[[], float] = time.monotonic) -> None:
        self.per_minute = per_minute
        self._now = now
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> int:
        """Count a request. Returns 0 when it may go ahead, else the whole seconds to wait."""
        now = self._now()
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= MAX_KEYS:
                self._sweep(now)
            hits = self._hits[key] = deque()
        while hits and now - hits[0] >= WINDOW_S:
            hits.popleft()
        if len(hits) >= self.per_minute:
            return max(1, int(WINDOW_S - (now - hits[0])) + 1)
        hits.append(now)
        return 0

    def _sweep(self, now: float) -> None:
        """Forget everyone who has been quiet for a minute; if that is nobody, the oldest tenth."""
        for key in [k for k, h in self._hits.items() if not h or now - h[-1] >= WINDOW_S]:
            del self._hits[key]
        if len(self._hits) >= MAX_KEYS:
            for key in sorted(self._hits, key=lambda k: self._hits[k][-1])[: MAX_KEYS // 10]:
                del self._hits[key]
