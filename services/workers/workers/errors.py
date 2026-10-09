"""How a delivery attempt failed, which decides what the outbox does next (DD §8.4)."""

from __future__ import annotations

from datetime import timedelta


class RetryLater(Exception):
    """Transient: 5xx, timeouts, throttling. The row backs off (30 s, 2 min, 10 min, 30 min)."""

    def __init__(self, message: str, *, retry_after: timedelta | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class GiveUp(Exception):
    """Permanent: bad request, missing configuration. The row goes dead at once (alarm)."""
