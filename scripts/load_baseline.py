"""Local load baseline: how the read tools behave as concurrency rises, on this machine.

    uv run python scripts/load_baseline.py                       # levels 1, 4, 16, 64 for 15 s each
    uv run python scripts/load_baseline.py --levels 1,8 --seconds 10 --out docs/perf/load-baseline.md

Starts its own MCP server (one uvicorn process on a free loopback port, the way a container runs it)
against the local database from ``.env``, with the per-user and per-workspace rate limits lifted so
the limiter is not what is measured. Tokens are minted in-process and the signing key is read from
disk; the auth server is not needed. Then, per concurrency level, that many callers each send the
next read-tool call as soon as the last one returns (closed loop), and the table reports throughput,
latency percentiles and every kind of failure.

What it shows: the shape of the curve and where the server stops keeping up (the database pool is
two connections wide and waits two seconds for one, so past that point calls queue, then answer
``TEMPORARILY_UNAVAILABLE``). What it does not show: AWS numbers. It is one machine, loopback, a
database in Docker, and whatever else is running; ``scripts/latency_probe.py`` is the check against a
deployed environment and ``tests/load/locustfile.py`` the profile for it.

Read-only: the mix is the five read tools of ``latency_probe`` against the seeded Lumenridge workspace.
Exit status 1 when any call failed, or when ``--p95-ms`` / ``--p99-ms`` is given and a level misses it.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import inspect
import io
import json
import os
import platform
import socket
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import asyncpg
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dev_token  # noqa: E402
from demo import load_env_file  # noqa: E402
from latency_probe import READ_TOOLS, _arguments, percentile  # noqa: E402
from remote_mcp import Remote  # noqa: E402
from watchfloor_core import db  # noqa: E402
from watchfloor_core.settings import get_settings  # noqa: E402

LEVELS = (1, 4, 16, 64)
UNLIMITED = "1000000"


def classify(status: int, body: Any) -> str | None:
    """None for a good answer, otherwise a short label for how the call failed."""
    if status != 200:
        return f"http_{status}"
    if not isinstance(body, dict):
        return "bad_body"
    if "error" in body:
        return f"rpc_{(body['error'] or {}).get('code')}"
    result = body.get("result") or {}
    if result.get("isError"):
        code = ((result.get("structuredContent") or {}).get("error") or {}).get("code")
        return f"tool_{code or 'unknown'}"
    return None


def summarize(samples: dict[str, list[float]], errors: Counter[str], seconds: float) -> dict[str, Any]:
    """One level: throughput of the good calls, percentiles over all of them, every failure by label."""
    every = [ms for ms_list in samples.values() for ms in ms_list]
    row: dict[str, Any] = {
        "ok": len(every),
        "failed": sum(errors.values()),
        "errors": dict(sorted(errors.items())),
        "rps": round(len(every) / seconds, 1) if seconds > 0 else 0.0,
    }
    if every:
        row.update(
            p50=round(percentile(every, 50), 1),
            p95=round(percentile(every, 95), 1),
            p99=round(percentile(every, 99), 1),
            max=round(max(every), 1),
        )
    row["tools"] = {
        tool: {"n": len(ms), "p50": round(percentile(ms, 50), 1), "p95": round(percentile(ms, 95), 1)}
        for tool, ms in samples.items()
        if ms
    }
    return row


def misses(row: dict[str, Any], *, p95_ms: float | None, p99_ms: float | None) -> list[str]:
    """Why a level fails the gate; empty when it passes."""
    why = []
    if row["failed"]:
        why.append(f"{row['failed']} failed calls")
    if "p95" not in row:
        why.append("no successful calls")
    else:
        if p95_ms is not None and row["p95"] > p95_ms:
            why.append(f"p95 {row['p95']} ms > {p95_ms:g}")
        if p99_ms is not None and row["p99"] > p99_ms:
            why.append(f"p99 {row['p99']} ms > {p99_ms:g}")
    return why


def render(meta: dict[str, Any], levels: dict[int, dict[str, Any]]) -> str:
    out = [
        "# Local load baseline",
        "",
        f"Measured {meta['when']} at commit `{meta['commit']}` on {meta['machine']}. "
        f"{meta['seconds']} s per level, closed loop, reads only, "
        f"database pool {meta['pool_max']} connections with a {meta['acquire_s']:g} s wait for one.",
        "",
        "One uvicorn process on loopback, Postgres in Docker on the same machine, rate limits lifted. "
        "These are not AWS numbers and not a promise: they show how the curve bends here, and they move "
        "with whatever else the machine is doing. Regenerate with `make load-baseline`.",
        "",
        "| Callers | OK | Failed | Calls/s | p50 ms | p95 ms | p99 ms | max ms |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for c, r in levels.items():
        cells = [r.get(k, "-") for k in ("p50", "p95", "p99", "max")]
        out.append(f"| {c} | {r['ok']} | {r['failed']} | {r['rps']} | " + " | ".join(map(str, cells)) + " |")
    failed = {c: r["errors"] for c, r in levels.items() if r["errors"]}
    out += ["", "Failures by kind: " + (json.dumps(failed, sort_keys=True) if failed else "none") + ".", ""]
    top = max(levels)
    out += [
        f"Per tool at {top} callers:",
        "",
        "| Tool | Calls | p50 ms | p95 ms |",
        "|---|---:|---:|---:|",
    ]
    for tool, t in levels[top]["tools"].items():
        out.append(f"| `{tool}` | {t['n']} | {t['p50']} | {t['p95']} |")
    return "\n".join(out) + "\n"


async def run_level(
    m: Remote, args: dict[str, dict[str, Any]], tools: list[str], callers: int, seconds: float
) -> dict[str, Any]:
    samples: dict[str, list[float]] = {t: [] for t in tools}
    errors: Counter[str] = Counter()
    started = time.perf_counter()
    stop = started + seconds

    async def caller(n: int) -> None:
        k = n  # callers start on different tools, so the mix is even from the first call
        while time.perf_counter() < stop:
            tool = tools[k % len(tools)]
            msg = {
                "jsonrpc": "2.0",
                "id": k,
                "method": "tools/call",
                "params": {"name": tool, "arguments": args[tool]},
            }
            k += 1
            t0 = time.perf_counter()
            try:
                r = await m.post(msg)
            except httpx.HTTPError as exc:
                errors[type(exc).__name__] += 1
                continue
            ms = (time.perf_counter() - t0) * 1000
            try:
                body = r.json()
            except ValueError:
                body = None
            label = classify(r.status_code, body)
            if label:
                errors[label] += 1
            else:
                samples[tool].append(ms)

    await asyncio.gather(*(caller(n) for n in range(callers)))
    return summarize(samples, errors, time.perf_counter() - started)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextlib.contextmanager
def mcp_server(port: int) -> Iterator[None]:
    env = {
        **os.environ,
        "WF_RATE_LIMIT_USER": UNLIMITED,
        "WF_RATE_LIMIT_WS": UNLIMITED,
        "WF_JWKS_URL": "file:.secrets/dev_jwks.json",  # verify without the auth server
        "WF_ALLOWED_HOSTS": "127.0.0.1,localhost",
    }
    cmd = [sys.executable, "-m", "uvicorn", "mcp_server.app:app", "--host", "127.0.0.1", "--port", str(port)]
    # Arguments: this interpreter and a fixed module path, a loopback address and a port we just chose.
    proc = subprocess.Popen(  # noqa: S603
        [*cmd, "--no-access-log", "--log-level", "warning"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


async def wait_ready(base: str, *, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    async with httpx.AsyncClient(timeout=2.0) as http:
        while time.monotonic() < deadline:
            with contextlib.suppress(httpx.HTTPError):
                if (await http.get(f"{base}/healthz")).status_code == 200:
                    return
            await asyncio.sleep(0.3)
    raise SystemExit(f"the server did not become healthy on {base} within {timeout_s:g} s")


def mint_token() -> str:
    """A read token for the seeded user; held in memory and never printed."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        if dev_token.main(["--ttl", "3600"]) != 0:
            raise SystemExit("could not mint a token: run `uv run python scripts/dev_keys.py` first")
    return out.getvalue().strip()


async def require_database() -> None:
    try:
        conn = await asyncpg.connect(get_settings().database_url, timeout=3)
    except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
        raise SystemExit(f"the local database is not reachable ({type(exc).__name__}): `make db-up`") from exc
    await conn.close()


def commit() -> str:
    with contextlib.suppress(OSError, subprocess.CalledProcessError):
        return subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    return "unknown"


async def measure(opts: argparse.Namespace, levels: list[int]) -> dict[int, dict[str, Any]]:
    await require_database()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    results: dict[int, dict[str, Any]] = {}
    with mcp_server(port):
        await wait_ready(base)
        limits = httpx.Limits(max_connections=max(levels) + 4, max_keepalive_connections=max(levels) + 4)
        async with httpx.AsyncClient(timeout=15.0, limits=limits) as http:
            m = Remote(http=http, url=f"{base}/mcp", token=mint_token())
            args = await _arguments(m)
            tools = list(READ_TOOLS)
            for tool in tools:  # unmeasured: first-call costs are not the curve
                await m.call(tool, args[tool])
            for c in levels:
                print(f"  {c} callers for {opts.seconds:g} s ...", file=sys.stderr, flush=True)
                results[c] = await run_level(m, args, tools, c, opts.seconds)
    return results


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--levels", default=",".join(map(str, LEVELS)), help="concurrent callers, comma-separated"
    )
    ap.add_argument("--seconds", type=float, default=15.0, help="length of each level")
    ap.add_argument("--p95-ms", type=float, help="fail a level whose p95 is above this")
    ap.add_argument("--p99-ms", type=float, help="fail a level whose p99 is above this")
    ap.add_argument("--out", type=Path, help="also write the report here (markdown)")
    opts = ap.parse_args(argv)
    levels = sorted({int(x) for x in opts.levels.split(",") if x.strip()})
    if not levels or levels[0] < 1:
        raise SystemExit("--levels needs positive integers")

    load_env_file(ROOT / ".env")  # before the settings are first read, as `make dev` does through honcho
    results = asyncio.run(measure(opts, levels))
    meta = {
        "when": time.strftime("%Y-%m-%d", time.gmtime()),
        "commit": commit(),
        "machine": f"{platform.system()} {platform.release()}, {os.cpu_count()} logical CPUs, "
        f"Python {platform.python_version()}",
        "seconds": f"{opts.seconds:g}",
        "pool_max": inspect.signature(db.open_pool).parameters["max_size"].default,  # app.py uses the default
        "acquire_s": db.ACQUIRE_TIMEOUT_S,
    }
    report = render(meta, results)
    print(report)
    if opts.out:
        opts.out.write_text(report, encoding="utf-8")
    bad = {c: why for c, r in results.items() if (why := misses(r, p95_ms=opts.p95_ms, p99_ms=opts.p99_ms))}
    for c, why in bad.items():
        print(f"FAIL at {c} callers: {'; '.join(why)}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
