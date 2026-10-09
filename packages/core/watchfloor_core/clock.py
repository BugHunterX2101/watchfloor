"""Single source of time for Watchfloor.

``Clock.now`` is the only place that reads the wall clock (ruff bans
``datetime.datetime.now`` elsewhere). In demo mode a per-workspace offset shifts
"now" so the "next morning" scene can be recorded without waiting (DD §24).
Offsets are ignored unless demo mode is on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class Clock:
    def __init__(self, demo_mode: bool = False) -> None:
        self._demo = demo_mode

    @property
    def demo_mode(self) -> bool:
        return self._demo

    def now(self, workspace_offset: timedelta | None = None) -> datetime:
        base = datetime.now(UTC)  # noqa: TID251 - the one allowed call site
        if self._demo and workspace_offset:
            return base + workspace_offset
        return base


class FixedClock(Clock):
    """Clock frozen at an instant; used by tests and the deterministic seeder."""

    def __init__(self, instant: datetime, demo_mode: bool = False) -> None:
        super().__init__(demo_mode)
        if instant.tzinfo is None:
            raise ValueError("FixedClock needs a timezone-aware instant")
        self._instant = instant

    def now(self, workspace_offset: timedelta | None = None) -> datetime:
        if self._demo and workspace_offset:
            return self._instant + workspace_offset
        return self._instant
