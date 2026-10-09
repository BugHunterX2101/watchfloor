"""Deep-triage evals (DD §22.5, TD §12.3, §15.1) against a running stack, through the user's tools.

    uv run python tests/evals/run_triage_eval.py --env local        # `make dev` (offline engine)
    uv run python tests/evals/run_triage_eval.py --env dev          # deployed agents on Bedrock
    uv run python tests/evals/run_triage_eval.py --env demo --out tests/evals/results/v1.0.0.json
    uv run python tests/evals/run_triage_eval.py --write-golden     # after a seed or RIGS change

Each case drives one job the way Alexa+ does: ``lookup_deal`` → ``start_deep_triage`` → poll
``get_triage_result``. Hostile deals are read before and after, so any change nobody confirmed shows.
Scoring is in ``scoring.py``. Exit status 1 when a threshold fails.

The golden set (``golden_triage.jsonl``) is built from the seed itself, at the frozen demo instant:
40 deals with evidence-backed reasons, labeled with the RIGS factors behind their top three reasons
(the ground truth by construction, since RIGS generated the data), plus the 10 hostile-note deals.
Deals are keyed by name and amount, because seeded ids carry the seed time. A label that no longer matches the
live ``explain_deal_risk`` is reported as drift; it means the golden set needs rebuilding.

Side effects: every finished job posts its plan to the environment's Slack webhook (an incoming
webhook can't be redirected) and adds a memory record for the signed-in user. Nothing else is
written. A deal reviewed within the last few minutes returns that review (``deduplicated``); such
cases are scored but left out of latency.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

from remote_mcp import Remote, connect, default_url  # noqa: E402

from tests.evals.scoring import Case, score  # noqa: E402

GOLDEN = HERE / "golden_triage.jsonl"
RESULTS = HERE / "results"
FROZEN_NOW = datetime(2026, 10, 21, 16, 0, tzinfo=UTC)  # the demo instant tests/conftest.py uses
DRIVER_CASES = 40
HOSTILE_CASES = 10
POLL_S = 3.0
JOB_TIMEOUT_S = 150.0
TERMINAL = {"done", "failed", "timed_out"}


def labels(reasons: list[Any]) -> list[str]:
    """RIGS factors behind the top three reasons that cite evidence (what a driver can be built on)."""
    found = []
    for r in reasons[:3]:
        factor, evidence = (
            (r["factor"], r["evidence_ids"]) if isinstance(r, dict) else (r.factor, r.evidence_ids)
        )
        if evidence and factor not in found:
            found.append(factor)
    return found


# ---------------------------------------------------------------- golden set


def build_golden() -> list[dict[str, Any]]:
    from zoneinfo import ZoneInfo

    from watchfloor_core import rigs

    from db.seed import generator, scenarios
    from db.seed.__main__ import load_params
    from db.seed.hostile_notes import HOSTILE_NOTES
    from db.seed.model import snapshot

    params = load_params()
    today = FROZEN_NOW.astimezone(ZoneInfo(scenarios.TIMEZONE)).date()
    rng = random.Random(42)
    demo = scenarios.build(FROZEN_NOW, today, rng)
    background, _ = generator.build(FROZEN_NOW, today, rng, params)
    deals = [*demo, *background]
    keys = [(d.name, d.amount_cents) for d in deals]
    unique = {k for k in keys if keys.count(k) == 1}

    hostile, scored = [], []
    for d in deals:
        if (d.name, d.amount_cents) not in unique:
            continue
        key = {"deal": d.name, "account": d.account.name, "amount_cents": d.amount_cents}
        if any(a.body in HOSTILE_NOTES for a in d.activities):
            hostile.append({"kind": "injection", **key})
            continue
        res = rigs.score(snapshot(d, FROZEN_NOW, today), params)
        found = labels(list(res.reasons))
        if found:
            scored.append((res.score, key, found))
    scored.sort(key=lambda row: (-row[0], row[1]["deal"], row[1]["amount_cents"]))
    rows = [
        {"kind": "drivers", **key, "labels": found, "score_at_seed": s}
        for s, key, found in scored[:DRIVER_CASES]
    ]
    if len(rows) < DRIVER_CASES or len(hostile) < HOSTILE_CASES:
        raise SystemExit(
            f"the seed gives {len(rows)} driver cases and {len(hostile)} distinguishable hostile deals; "
            f"need {DRIVER_CASES} and {HOSTILE_CASES}"
        )
    return rows + hostile


def load_golden() -> list[dict[str, Any]]:
    return [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------- one case


async def _structured(m: Remote, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await m.call(tool, args)
    out = result["structuredContent"]
    if result.get("isError"):
        raise LookupError(out.get("error", {}).get("code") or out.get("summary") or "error")
    return out


async def _deal_id(m: Remote, row: dict[str, Any]) -> str:
    """The deal named in the row; same-named deals are told apart by amount.

    ``lookup_deal`` returns its three best matches, so the full deal name goes first (an account
    often has more than three deals); the account name is the fallback.
    """
    matches: list[str] = []
    for query in (row["deal"], row["account"]):
        found = await _structured(m, "lookup_deal", {"name_query": query})
        matches = [c["deal_id"] for c in found.get("candidates", []) if c["name"] == row["deal"]]
        if matches:
            break
    if len(matches) > 1:
        amounts = {
            d: (await _structured(m, "explain_deal_risk", {"deal_id": d}))["deal"]["amount_cents"]
            for d in matches
        }
        matches = [d for d in matches if amounts[d] == row["amount_cents"]]
    if len(matches) != 1:
        raise LookupError(f"{len(matches)} deals named {row['deal']!r} for {row['amount_cents']} cents")
    return matches[0]


async def run_case(m: Remote, row: dict[str, Any], slots: asyncio.Semaphore) -> Case:
    case = Case(kind=row["kind"], deal=row["deal"], deal_id=None, labels=row.get("labels", []))
    try:
        case.deal_id = await _deal_id(m, row)
    except LookupError as exc:
        case.status, case.note = "lookup_failed", str(exc)
        return case
    explained = await _structured(m, "explain_deal_risk", {"deal_id": case.deal_id})
    case.live_labels = labels(explained.get("reasons", []))
    case.before = explained["deal"]

    async with slots:  # the server runs at most 3 reviews per workspace
        t0 = time.monotonic()
        while True:
            try:
                started = await _structured(m, "start_deep_triage", {"deal_id": case.deal_id})
                break
            except LookupError as exc:
                if str(exc) != "JOB_LIMIT_REACHED" or time.monotonic() - t0 > JOB_TIMEOUT_S:
                    case.status, case.note = "start_failed", str(exc)
                    return case
                await asyncio.sleep(10)
        case.deduplicated = bool(started.get("deduplicated"))
        t0 = time.monotonic()
        status = started.get("status", "queued")
        result: dict[str, Any] = {}
        while time.monotonic() - t0 < JOB_TIMEOUT_S:
            result = await _structured(m, "get_triage_result", {"job_id": started["job_id"]})
            status = result["status"]
            if status in TERMINAL:
                break
            await asyncio.sleep(POLL_S)
        case.seconds = round(time.monotonic() - t0, 1)
        case.status, case.result = (status if status in TERMINAL else "no_result"), result

    if case.kind == "injection":
        case.after = (await _structured(m, "explain_deal_risk", {"deal_id": case.deal_id}))["deal"]
    return case


async def run(
    url: str, *, token: str | None, env: str | None, rows: list[dict[str, Any]], concurrency: int
) -> list[Case]:
    slots = asyncio.Semaphore(concurrency)
    async with connect(url, token=token, env=env, min_interval=60 / 55) as m:
        tasks = [asyncio.create_task(run_case(m, row, slots)) for row in rows]
        cases = []
        for finished in asyncio.as_completed(tasks):
            case = await finished
            cases.append(case)
            print(f"  {len(cases):>2}/{len(rows)} {case.status:<13} {case.deal}", file=sys.stderr, flush=True)
    order = {row["deal"]: i for i, row in enumerate(rows)}
    return sorted(cases, key=lambda c: order[c.deal])


# ---------------------------------------------------------------- CLI


def _print(report: dict[str, Any]) -> None:
    for name, m in report["metrics"].items():
        mark = {True: "PASS", False: "FAIL", None: "n/a "}[m["ok"]]
        print(f"{mark}  {name:<19} {m['value']!s:>7}  (threshold {m['threshold']}; {m['detail']})")
    print(f"      groundedness       {report['groundedness']['supported']}")
    print(f"      cost               {report['cost']}")
    if report["label_drift"]:
        print(f"      label drift on {len(report['label_drift'])} deals: rebuild with --write-golden")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", choices=["local", "dev", "demo"], default="local")
    ap.add_argument("--url", help="the MCP endpoint (default: local, or from infra/config/<env>.yaml)")
    ap.add_argument("--token", help="bearer token (default: WF_MCP_TOKEN, else canary sign-in)")
    ap.add_argument("--kind", choices=["all", "drivers", "injection"], default="all")
    ap.add_argument("--limit", type=int, help="at most this many cases of each kind (a smoke run)")
    ap.add_argument("--concurrency", type=int, default=3, help="reviews in flight (the server caps at 3)")
    ap.add_argument("--out", type=Path, help="results JSON (default: tests/evals/results/<env>-<time>.json)")
    ap.add_argument("--write-golden", action="store_true", help="rebuild golden_triage.jsonl from the seed")
    opts = ap.parse_args(argv)

    if opts.write_golden:
        rows = build_golden()
        GOLDEN.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        print(f"wrote {len(rows)} cases to {GOLDEN.relative_to(ROOT)}")
        return 0

    rows = [r for r in load_golden() if opts.kind in ("all", r["kind"])]
    if opts.limit:
        rows = [
            r
            for kind in ("drivers", "injection")
            for r in [x for x in rows if x["kind"] == kind][: opts.limit]
        ]
    url = opts.url or default_url(opts.env)
    print(f"{len(rows)} cases against {url}", file=sys.stderr)
    cases = asyncio.run(run(url, token=opts.token, env=opts.env, rows=rows, concurrency=opts.concurrency))
    report = score(cases)
    _print(report)

    out = opts.out or RESULTS / f"{opts.env}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    full = {"env": opts.env, "url": url, **report, "cases": [asdict(c) for c in cases]}
    out.write_text(json.dumps(full, indent=2, default=str), encoding="utf-8")
    print(f"\nresults: {out}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
