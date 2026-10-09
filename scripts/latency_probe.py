"""Hot-path latency probe (TD §12.3, §19.2): N calls per read tool → p50 / p95 / p99 table.

    uv run python scripts/latency_probe.py --n 20                  # local `make dev`
    uv run python scripts/latency_probe.py --env demo --n 200      # URL from infra/config/demo.yaml

Times each ``tools/call`` round trip as the client sees it (TLS, gateway, Lambda, database). Calls
are interleaved across tools, so each tool is sampled over the same period, and evenly spaced under
the per-user rate limit (``--per-minute``, 60 on the server): 200 calls × 5 tools take about 18
minutes. A 429 waits out ``Retry-After`` and is retried, not counted. Read-only, so it is safe against
demo. Token and sign-in: see ``remote_mcp``.

Exit status 1 when any tool's p95 exceeds ``--p95-ms`` (150), its p99 exceeds ``--p99-ms`` (300),
or any call fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remote_mcp import Remote, connect, default_url  # noqa: E402

READ_TOOLS = [
    "get_risk_briefing",
    "explain_deal_risk",
    "lookup_deal",
    "list_standing_orders",
    "get_triage_result",
]


def percentile(samples: list[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..100) of a non-empty sample."""
    ordered = sorted(samples)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def summarize(
    samples: dict[str, list[float]], errors: dict[str, int], *, p95_ms: float, p99_ms: float
) -> list[dict[str, Any]]:
    rows = []
    for tool, ms in samples.items():
        row: dict[str, Any] = {"tool": tool, "n": len(ms), "errors": errors.get(tool, 0)}
        if ms:
            row.update(
                p50=round(percentile(ms, 50), 1),
                p95=round(percentile(ms, 95), 1),
                p99=round(percentile(ms, 99), 1),
                max=round(max(ms), 1),
            )
        row["ok"] = bool(ms) and row["errors"] == 0 and row["p95"] <= p95_ms and row["p99"] <= p99_ms
        rows.append(row)
    return rows


async def _arguments(m: Remote) -> dict[str, dict[str, Any]]:
    found = (await m.call("lookup_deal", {"name_query": "kestrel"}))["structuredContent"]
    if not found.get("candidates"):
        raise SystemExit("lookup_deal found no Kestrel deal: seed the workspace first")
    deal_id = found["candidates"][0]["deal_id"]
    return {
        "get_risk_briefing": {},
        "explain_deal_risk": {"deal_id": deal_id},
        "lookup_deal": {"name_query": "kestrel"},
        "list_standing_orders": {},
        "get_triage_result": {"deal_id": deal_id},
    }


async def _timed_call(m: Remote, tool: str, args: dict[str, Any], rid: int) -> float | None:
    """Milliseconds for one successful call, or None when it failed. Retries after a 429."""
    msg = {"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": {"name": tool, "arguments": args}}
    while True:
        t0 = time.perf_counter()
        r = await m.post(msg)
        elapsed = (time.perf_counter() - t0) * 1000
        if r.status_code == 429:
            await asyncio.sleep(float(r.headers.get("retry-after", "5")))
            continue
        ok = r.status_code == 200 and "result" in r.json() and not r.json()["result"].get("isError")
        return elapsed if ok else None


async def probe(
    url: str, *, token: str | None, env: str | None, n: int, tools: list[str], per_minute: float, warmup: int
) -> tuple[dict[str, list[float]], dict[str, int]]:
    samples: dict[str, list[float]] = {t: [] for t in tools}
    errors: dict[str, int] = {}
    interval = 60.0 / per_minute
    async with connect(url, token=token, env=env) as m:
        args = await _arguments(m)
        unknown = set(tools) - set(args)
        if unknown:
            raise SystemExit(f"not a read tool this probe knows: {', '.join(sorted(unknown))}")
        schedule = [(None, t) for t in tools for _ in range(warmup)] + [
            (i, t) for i in range(n) for t in tools
        ]
        start = time.perf_counter()
        for k, (i, tool) in enumerate(schedule):
            await asyncio.sleep(max(0.0, start + k * interval - time.perf_counter()))
            ms = await _timed_call(m, tool, args[tool], k + 1)
            if i is None:
                continue  # warm-up: connection set-up and first-call costs are not the hot path
            if ms is None:
                errors[tool] = errors.get(tool, 0) + 1
            else:
                samples[tool].append(ms)
            if (k + 1) % 50 == 0:
                print(f"  {k + 1}/{len(schedule)} calls", file=sys.stderr, flush=True)
    return samples, errors


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # "≤" on a cp1252 console
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="the MCP endpoint (default: local, or from infra/config/<env>.yaml)")
    ap.add_argument("--token", help="bearer token (default: WF_MCP_TOKEN, else canary sign-in)")
    ap.add_argument("--env", choices=["local", "dev", "demo"], help="dev|demo: canary credentials from AWS")
    ap.add_argument("--n", type=int, default=200, help="measured calls per tool (default 200)")
    ap.add_argument("--tools", default=",".join(READ_TOOLS), help="comma-separated read tools")
    ap.add_argument("--per-minute", type=float, default=55, help="call rate; the server allows 60 per user")
    ap.add_argument("--warmup", type=int, default=2, help="unmeasured calls per tool first")
    ap.add_argument("--p95-ms", type=float, default=150)
    ap.add_argument("--p99-ms", type=float, default=300)
    ap.add_argument("--json", type=Path, help="also write the table here")
    opts = ap.parse_args(argv)
    opts.url = opts.url or default_url(opts.env)

    tools = [t.strip() for t in opts.tools.split(",") if t.strip()]
    total = (opts.n + opts.warmup) * len(tools)
    print(
        f"{total} calls at {opts.per_minute:g}/min: about {total / opts.per_minute:.0f} min", file=sys.stderr
    )
    samples, errors = asyncio.run(
        probe(
            opts.url,
            token=opts.token,
            env=opts.env,
            n=opts.n,
            tools=tools,
            per_minute=opts.per_minute,
            warmup=opts.warmup,
        )
    )
    rows = summarize(samples, errors, p95_ms=opts.p95_ms, p99_ms=opts.p99_ms)

    print(f"{'tool':<22} {'n':>4} {'err':>4} {'p50':>7} {'p95':>7} {'p99':>7} {'max':>7}")
    for r in rows:
        cells = [f"{r.get(k, float('nan')):>7.1f}" for k in ("p50", "p95", "p99", "max")]
        mark = "" if r["ok"] else "  FAIL"
        print(f"{r['tool']:<22} {r['n']:>4} {r['errors']:>4} {' '.join(cells)}{mark}")
    print(f"\nbudget: p95 ≤ {opts.p95_ms:g} ms, p99 ≤ {opts.p99_ms:g} ms, no errors · {opts.url}")
    if opts.json:
        report = {"url": opts.url, "n": opts.n, "p95_ms": opts.p95_ms, "p99_ms": opts.p99_ms, "rows": rows}
        opts.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
