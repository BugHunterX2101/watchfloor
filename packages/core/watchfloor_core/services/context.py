"""Per-call service context: who is asking, in which workspace, and what time it is there."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg

from ..clock import Clock
from ..errors import ErrorCode, WatchfloorError
from ..principal import Principal
from ..repos import jobs, workspaces
from ..settings import get_settings

_clock: Clock | None = None


def clock() -> Clock:
    global _clock
    if _clock is None:
        _clock = Clock(demo_mode=get_settings().demo_mode)
    return _clock


def set_clock(c: Clock | None) -> None:
    """Tests and the seeder install a FixedClock here."""
    global _clock
    _clock = c


@dataclass
class WorkspaceTime:
    now: datetime
    today: date
    tz: ZoneInfo


class UnknownWorkspace(LookupError):
    """A credential names a workspace that does not exist (deleted, or never provisioned)."""


async def workspace_time(conn: asyncpg.Connection, workspace_id: str) -> WorkspaceTime:
    ws = await workspaces.get(conn, workspace_id)
    if ws is None:
        raise UnknownWorkspace(workspace_id)  # fail closed rather than guess a timezone
    offset: timedelta | None = ws["clock_offset"]
    now = clock().now(offset)
    tz = ZoneInfo(ws["timezone"])
    return WorkspaceTime(now=now, today=now.astimezone(tz).date(), tz=tz)


@dataclass
class ServiceContext:
    conn: asyncpg.Connection
    principal: Principal
    now: datetime
    today: date
    tz: ZoneInfo
    trace_id: str | None = None
    outbox_ids: list[int] = field(default_factory=list)
    held_outbox_ids: list[int] = field(default_factory=list)  # deliverable after the undo window

    @property
    def ws(self) -> str:
        return self.principal.workspace_id

    @property
    def user_id(self) -> str:
        return self.principal.user_id

    def local_iso(self, dt: datetime) -> str:
        return dt.astimezone(self.tz).isoformat(timespec="seconds")


async def make_context(
    conn: asyncpg.Connection, principal: Principal, trace_id: str | None = None
) -> ServiceContext:
    if principal.job_id is not None:
        principal = await _bind_job(conn, principal)
    t = await workspace_time(conn, principal.workspace_id)
    return ServiceContext(
        conn=conn, principal=principal, now=t.now, today=t.today, tz=t.tz, trace_id=trace_id
    )


async def _bind_job(conn: asyncpg.Connection, principal: Principal) -> Principal:
    """A job token acts for the user who started the job, and only while the job is live."""
    job = await jobs.get(conn, principal.job_id or "")
    if job is None or job["status"] not in ("queued", "running"):
        raise WatchfloorError(ErrorCode.JOB_NOT_FOUND, "This review is no longer running.")
    return replace(principal, user_id=job["user_id"])
