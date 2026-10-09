"""Demo operations on the seeded workspace (DD §24, TD §17.3): clock offset, status, outbox retry.

Shared by ``scripts/demo.py`` (locally, straight to Postgres) and the seeder Lambda (in dev and
demo, from inside the VPC: ``{"demo": "status" | "clock" | "outbox-retry", ...}``). Both connect as
the owner role with row-level security scoped to the workspace.
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import asyncpg
from watchfloor_core.db import _init_connection

from db.seed import scenarios

MAX_OFFSET = timedelta(days=30)
_OFFSET = re.compile(r"^([+-]?)(\d{1,4})([mhd])$")
_UNITS = {"m": "minutes", "h": "hours", "d": "days"}
NAMES = {
    scenarios.KESTREL: "Kestrel",
    scenarios.HARBOR: "Harbor & Pine",
    scenarios.ORBITAL: "Orbital Dental",
    scenarios.NORTHGATE: "Northgate",
}


def parse_offset(raw: str) -> timedelta:
    """``+14h``, ``-30m``, ``2d`` or ``0``; at most 30 days either way."""
    text = raw.strip().lower()
    if text in {"0", "+0", "-0", "none", "off"}:
        return timedelta(0)
    m = _OFFSET.match(text)
    if not m:
        raise ValueError(f"offset {raw!r}: use a signed amount with m, h or d, e.g. +14h, -30m, 2d or 0")
    sign, amount, unit = m.groups()
    delta = timedelta(**{_UNITS[unit]: int(amount)})
    if delta > MAX_OFFSET:
        raise ValueError(f"offset {raw!r} is more than 30 days")
    return -delta if sign == "-" else delta


def describe_offset(offset: timedelta | None) -> str:
    if not offset:
        return "none (real time)"
    sign = "-" if offset < timedelta(0) else "+"
    minutes = int(abs(offset).total_seconds() // 60)
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    parts = [f"{n}{u}" for n, u in ((days, "d"), (hours, "h"), (mins, "m")) if n]
    return sign + "".join(parts)


def demo_mode() -> bool:
    return os.environ.get("WF_DEMO_MODE", "").strip().lower() in {"1", "true", "yes", "on"}


@asynccontextmanager
async def scoped(url: str, ws: str) -> AsyncIterator[asyncpg.Connection]:
    """One owner transaction, RLS scoped to ``ws`` (FORCE RLS applies to the owner too)."""
    conn = await asyncpg.connect(url, timeout=5)
    await _init_connection(conn)
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', $1, true), set_config('statement_timeout', '10s', true)",
                ws,
            )
            yield conn
    finally:
        await conn.close()


# ---------------------------------------------------------------- queries


async def set_offset(conn: asyncpg.Connection, ws: str, offset: timedelta) -> None:
    done = await conn.execute("UPDATE workspaces SET clock_offset = $2 WHERE id = $1", ws, offset)
    if done != "UPDATE 1":
        raise LookupError(f"workspace {ws} does not exist; run `make demo-reset` first")


async def retry_dead(conn: asyncpg.Connection, ws: str, ids: list[int], *, all_dead: bool) -> list[int]:
    rows = await conn.fetch(
        """UPDATE outbox SET status = 'pending', attempts = 0, next_attempt_at = now(), last_error = NULL
           WHERE workspace_id = $1 AND status = 'dead' AND ($3 OR id = ANY($2::bigint[]))
           RETURNING id""",
        ws,
        ids,
        all_dead,
    )
    return sorted(r["id"] for r in rows)


async def status(conn: asyncpg.Connection, ws: str) -> dict[str, Any]:
    workspace = await conn.fetchrow("SELECT name, timezone, clock_offset FROM workspaces WHERE id = $1", ws)
    if workspace is None:
        raise LookupError(f"workspace {ws} does not exist; run `make demo-reset` first")
    bands = {"healthy": 0, "watch": 0, "at_risk": 0}
    for r in await conn.fetch("SELECT band::text AS band, count(*) AS n FROM risk_scores GROUP BY 1"):
        bands[r["band"]] = r["n"]
    scores = {
        r["deal_id"]: (r["score"], r["band"])
        for r in await conn.fetch(
            "SELECT deal_id, score, band::text AS band FROM risk_scores WHERE deal_id = ANY($1::text[])",
            list(scenarios.EXPECTED),
        )
    }
    board = await conn.fetch(
        """SELECT d.id, a.name AS account, rs.score, rs.band::text AS band
           FROM risk_scores rs JOIN deals d ON d.id = rs.deal_id JOIN accounts a ON a.id = d.account_id
           WHERE rs.band IN ('at_risk','watch')
           ORDER BY (rs.band = 'at_risk') DESC, rs.score::bigint * d.amount_cents DESC, d.id LIMIT 3"""
    )
    proposals = await conn.fetchval(
        "SELECT count(*) FROM action_proposals WHERE status = 'pending' AND expires_at > now()"
    )
    jobs = {
        r["status"]: r["n"]
        for r in await conn.fetch(
            "SELECT status, count(*) AS n FROM triage_jobs WHERE status IN ('queued','running') GROUP BY 1"
        )
    }
    outbox = {
        (r["kind"], r["status"]): r["n"]
        for r in await conn.fetch(
            "SELECT kind, status, count(*) AS n FROM outbox WHERE status <> 'sent' GROUP BY 1, 2"
        )
    }
    return {
        "workspace": ws,
        "name": workspace["name"],
        "timezone": workspace["timezone"],
        "clock_offset": workspace["clock_offset"],
        "bands": bands,
        "scenarios": {did: (scores.get(did), want) for did, want in scenarios.EXPECTED.items()},
        "board": [(r["id"], r["account"], r["score"], r["band"]) for r in board],
        "pending_proposals": proposals,
        "jobs": jobs,
        "outbox": outbox,
    }


# ---------------------------------------------------------------- the report


def render_status(st: dict[str, Any], *, demo_mode: bool) -> str:
    offset = describe_offset(st["clock_offset"])
    if st["clock_offset"] and not demo_mode:
        offset += "  (ignored unless WF_DEMO_MODE=true)"
    lines = [
        f"{st['name']} ({st['workspace']}, {st['timezone']})",
        f"  clock offset   {offset}",
        "  bands          " + "  ".join(f"{band} {n}" for band, n in st["bands"].items()),
        "  scenarios",
    ]
    for did, (got, want) in st["scenarios"].items():
        mark = "ok" if got == want else f"DRIFT (expected {want[0]} {want[1]})"
        shown = f"{got[0]} {got[1]}" if got else "missing"
        lines.append(f"    {NAMES.get(did, did):<15} {shown:<14} {mark}")
    lines.append("  board          " + (", ".join(f"{a} {s}" for _, a, s, _ in st["board"]) or "empty"))
    lines.append(f"  proposals      {st['pending_proposals']} pending")
    lines.append(
        "  triage jobs    " + (", ".join(f"{n} {s}" for s, n in sorted(st["jobs"].items())) or "none running")
    )
    backlog = ", ".join(f"{n} {kind} {state}" for (kind, state), n in sorted(st["outbox"].items()))
    lines.append("  outbox         " + (backlog or "empty"))
    return "\n".join(lines)


def scenarios_ok(st: dict[str, Any]) -> bool:
    return all(got == want for got, want in st["scenarios"].values())


def status_to_json(st: dict[str, Any]) -> dict[str, Any]:
    """:func:`status` as a Lambda result: the offset in seconds, tuple keys spelled out."""
    offset = st["clock_offset"]
    return {
        **st,
        "clock_offset": offset.total_seconds() if offset is not None else None,
        "scenarios": {did: {"got": got, "want": want} for did, (got, want) in st["scenarios"].items()},
        "outbox": [[kind, state, n] for (kind, state), n in st["outbox"].items()],
    }


def status_from_json(raw: dict[str, Any]) -> dict[str, Any]:
    """The inverse of :func:`status_to_json`, so :func:`render_status` reads both alike."""
    offset = raw["clock_offset"]
    return {
        **raw,
        "clock_offset": timedelta(seconds=offset) if offset is not None else None,
        "scenarios": {
            did: (tuple(s["got"]) if s["got"] else None, tuple(s["want"]))
            for did, s in raw["scenarios"].items()
        },
        "board": [tuple(row) for row in raw["board"]],
        "outbox": {(kind, state): n for kind, state, n in raw["outbox"]},
    }


# ---------------------------------------------------------------- the seeder Lambda's side


async def run_op(dsn: str, ws: str, event: dict[str, Any]) -> dict[str, Any]:
    """One demo operation from a seeder Lambda event; ``ValueError`` on a bad event."""
    op = event["demo"]
    result: dict[str, Any] = {"workspace": ws, "demo_mode": demo_mode()}
    if op == "status":
        async with scoped(dsn, ws) as conn:
            st = await status(conn, ws)
        return {**result, "status": status_to_json(st), "scenarios_ok": scenarios_ok(st)}
    if op == "clock":
        offset = parse_offset(str(event.get("offset", "")))
        async with scoped(dsn, ws) as conn:
            await set_offset(conn, ws, offset)
        return {**result, "clock_offset": describe_offset(offset)}
    if op == "outbox-retry":
        ids, all_dead = [int(i) for i in event.get("ids") or []], bool(event.get("all_dead"))
        if not ids and not all_dead:
            raise ValueError("give ids or all_dead")
        async with scoped(dsn, ws) as conn:
            return {**result, "rearmed": await retry_dead(conn, ws, ids, all_dead=all_dead)}
    raise ValueError(f"unknown demo operation {op!r}: status, clock or outbox-retry")
