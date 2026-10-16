"""EXPLAIN (ANALYZE, BUFFERS) for the hot-path queries, written into docs/perf/ (TD §21.1, DD §21.2).

    make perf-plans                                  # local seed: make db-up migrate seed first
    uv run python scripts/explain_plans.py --check   # exit 1 when ranked_risks is over its budget

Each repo function runs unchanged on a connection that explains every statement before running it,
so the plans are of the exact SQL the code sends, as the app role (wf_app) under row-level security.
Only SELECTs are explained: EXPLAIN ANALYZE executes the statement.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import re
import subprocess
import sys
import textwrap
from datetime import timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from watchfloor_core import db  # noqa: E402
from watchfloor_core.clock import Clock  # noqa: E402
from watchfloor_core.repos import deals, rules, scores  # noqa: E402

OUT = ROOT / "docs" / "perf"
LOCAL_DSN = "postgresql://wf_app:devpassword@127.0.0.1:55432/watchfloor"  # docker-compose.yml, .env.example
WORKSPACE = "ws_lumenridge"
USER = "us_01J9PRIYA000000000000000"  # Priya, the demo user
RANKED_BUDGET_MS = 5.0  # TD §21.1: ranked risks stay under 5 ms on the seed


class Explaining:
    """A connection stand-in: every query is explained, then run as usual."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self.plans: list[tuple[str, str, float]] = []

    async def _explain(self, query: str, args: tuple[Any, ...]) -> None:
        if not re.match(r"\s*(SELECT|WITH)\b", query, re.I):
            raise RuntimeError(f"refusing to EXPLAIN ANALYZE a write: {query.split()[0]}")
        rows = await self._conn.fetch(f"EXPLAIN (ANALYZE, BUFFERS) {query}", *args)
        plan = "\n".join(r[0] for r in rows)
        m = re.search(r"Execution Time: ([\d.]+) ms", plan)
        self.plans.append((textwrap.dedent(query).strip(), plan, float(m.group(1)) if m else math.nan))

    async def fetch(self, query: str, *args: Any) -> Any:
        await self._explain(query, args)
        return await self._conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> Any:
        await self._explain(query, args)
        return await self._conn.fetchrow(query, *args)

    async def fetchval(self, query: str, *args: Any) -> Any:
        await self._explain(query, args)
        return await self._conn.fetchval(query, *args)


async def _collect() -> tuple[dict[str, list[tuple[str, str, float]]], dict[str, int]]:
    now = Clock().now()
    today = now.astimezone(ZoneInfo("America/Los_Angeles")).date()
    since = now - timedelta(days=1)
    cases: dict[str, Any] = {
        "ranked_risks": lambda c: scores.ranked_risks(c, limit=13, segment=None, since=since),
        "attention_counts": lambda c: scores.attention_counts(c, segment=None, since=since),
        "fired_since": lambda c: rules.fired_since(c, USER, since),
        "rule_match": lambda c: rules.matches(
            c, rule_id="ru_explain", conditions={"min_amount_cents": 5_000_000, "quiet_days": 6}, now=now
        ),
        "load_snapshots": lambda c: deals.load_snapshots(c, as_of=now, today=today),
    }
    found: dict[str, list[tuple[str, str, float]]] = {}
    async with db.ws_tx(WORKSPACE, statement_timeout="10s") as conn:
        counts = dict(
            await conn.fetchrow(
                """SELECT (SELECT count(*) FROM deals) AS deals, (SELECT count(*) FROM activities) AS activities,
                          (SELECT count(*) FROM risk_scores) AS risk_scores"""
            )
        )
        for name, call in cases.items():
            await call(conn)  # warm: the first run pays for planning and cold buffers
            spy = Explaining(conn)
            await call(spy)
            found[name] = spy.plans
    return found, counts


def _write(found: dict[str, list[tuple[str, str, float]]], counts: dict[str, int]) -> None:
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT)  # noqa: S607
    head = sha.stdout.strip() or "unknown"
    seed = f"{counts['deals']} deals · {counts['activities']} activities · {counts['risk_scores']} scores"
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, plans in found.items():
        parts = [f"-- {name} · commit {head} · {seed}\n"]
        for i, (sql, plan, ms) in enumerate(plans, 1):
            parts.append(f"\n-- statement {i} of {len(plans)}: {ms:.3f} ms\n{sql}\n\n{plan}\n")
        (OUT / f"{name}.txt").write_text("".join(parts), encoding="utf-8", newline="\n")
        rows.append(
            f"| `{name}` | {len(plans)} | {sum(p[2] for p in plans):.2f} ms | [{name}.txt]({name}.txt) |"
        )
    readme = f"""# Hot-path query plans

`EXPLAIN (ANALYZE, BUFFERS)` of the SQL the code runs (TD §21.1), as `wf_app` under row-level security on
the local seed ({seed}), commit `{head}`. Each function ran once to warm the cache, then once explained.
Regenerate with `make perf-plans` after a query or index changes; `--check` fails when `ranked_risks`
takes over {RANKED_BUDGET_MS:g} ms.

| Query | Statements | Execution time | Plan |
|---|---|---|---|
{chr(10).join(rows)}

`ranked_risks`, `fired_since` and `attention_counts` are the briefing (`get_risk_briefing`); `rule_match`
is one standing order in the watcher; `load_snapshots` builds the RIGS inputs for every open deal, the
watcher's 15-minute recompute. The seed is small, so these show plan shapes and index use, not scale.
"""
    (OUT / "README.md").write_text(readme, encoding="utf-8", newline="\n")


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dsn", default=os.environ.get("WF_DATABASE_URL", LOCAL_DSN))
    parser.add_argument("--check", action="store_true", help="exit 1 when ranked_risks is over budget")
    opts = parser.parse_args()
    await db.open_pool(dsn=opts.dsn)
    try:
        found, counts = await _collect()
    finally:
        await db.close_pool()
    _write(found, counts)
    ranked = sum(p[2] for p in found["ranked_risks"])
    print(f"docs/perf/ written; ranked_risks {ranked:.2f} ms (budget {RANKED_BUDGET_MS:g} ms)")
    return 1 if opts.check and ranked > RANKED_BUDGET_MS else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
