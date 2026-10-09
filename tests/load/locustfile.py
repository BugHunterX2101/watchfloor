"""Load profile (DD §21.3, TD §15.1): the voice mix at 20 rps against a deployed MCP server.

    uv run locust -f tests/load/locustfile.py --headless -t 7m --host https://mcp.dev.watchfloor.example

Mix: 50% get_risk_briefing, 25% explain_deal_risk, 10% lookup_deal, 10% propose_action, 5%
confirm_action. Shape: ramp to 40 users (0.5 calls/s each, so 20 rps) over 2 minutes, hold 5
minutes. Pass: p95 ≤ 150 ms, p99 ≤ 300 ms, and no failures; a tool's business error (an expired
token, an ambiguous name) is the expected kind and counts as a success. Exit status 1 on a miss.

Callers. ``WF_LOAD_TOKENS`` (comma-separated bearer tokens) spreads the users over several real
users; otherwise every user shares one canary sign-in (``scripts/remote_mcp.py``; pass
``WF_LOAD_ENV=dev|demo`` for the canary secret). Either way the default limits (60 calls per user
and 600 per workspace per minute) are below 20 rps, so a 429 shows up as a failure: raise
``rate_limit_user`` / ``rate_limit_ws`` in ``infra/config/<env>.yaml`` for the load window, deploy,
run, and put them back.

Side effects: confirmed proposals create tasks on the briefing's deals (``make demo-reset`` clears
them). Nothing posts to Slack: every proposal is a ``create_task``.
"""

from __future__ import annotations

import itertools
import json
import os
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
from locust import HttpUser, LoadTestShape, constant_throughput, events, task
from locust.env import Environment

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import remote_mcp  # noqa: E402

PATH = "/mcp"
USERS = 40
CALLS_PER_USER_S = 0.5
RAMP_S, HOLD_S = 120, 300
P95_MS, P99_MS = 150, 300
# Error codes that mean the server, not the request, is the problem.
FAILURES = {"RATE_LIMITED", "TEMPORARILY_UNAVAILABLE"}

_tokens: itertools.cycle[str] | None = None
_deals: list[dict[str, str]] = []
_signed_in: tuple[str | None, str | None] = (None, None)  # (refresh token, MCP URL) to revoke at the end


def _token_command(command: str, url: str, stdin: str = "") -> str:
    """Canary sign-in or revoke in a child process: asyncio hangs under Locust's gevent patching."""
    args = [sys.executable, str(SCRIPTS / "remote_mcp.py"), command, url]
    if os.environ.get("WF_LOAD_ENV"):
        args.append(os.environ["WF_LOAD_ENV"])
    # Arguments: this interpreter, a repo script, the --host URL and an env name; no shell.
    done = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=60, check=False)  # noqa: S603
    if done.returncode != 0:
        raise SystemExit(f"canary {command} failed: {done.stderr.strip()[-300:]}")
    return done.stdout


def _message(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}


def _headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": remote_mcp.PROTOCOL_VERSION,
        "Authorization": f"Bearer {token}",
    }


@events.init.add_listener
def _prepare(environment: Environment, **_: Any) -> None:
    """Tokens, then the briefing's deals (ids, account names, owners) for the mix to work on.

    On ``init``, once, before the shape starts: in ``test_start`` it takes longer than a shape tick,
    and each tick that changes the user count kills and restarts the start-up greenlet.
    """
    global _tokens, _signed_in
    if _tokens is not None:
        return
    url = f"{(environment.host or remote_mcp.LOCAL_URL.removesuffix(PATH)).rstrip('/')}{PATH}"
    tokens = [t.strip() for t in os.environ.get("WF_LOAD_TOKENS", "").split(",") if t.strip()]
    if not tokens:
        signed_in = json.loads(_token_command("sign-in", url))
        tokens, _signed_in = [signed_in["access"]], (signed_in["refresh"], url)
    _tokens = itertools.cycle(tokens)

    with httpx.Client(timeout=remote_mcp.TIMEOUT_S, headers=_headers(tokens[0])) as http:

        def call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
            r = http.post(url, json=_message(tool, args))
            r.raise_for_status()
            return r.json()["result"]["structuredContent"]

        for item in call("get_risk_briefing", {"limit": 5}).get("deals", []):
            deal = call("explain_deal_risk", {"deal_id": item["deal_id"]})["deal"]
            _deals.append(
                {"deal_id": deal["deal_id"], "account": deal["account"], "owner": deal["owner"].split()[0]}
            )
    if not _deals:
        raise SystemExit("the briefing returned no deals; seed the workspace first")


@events.quitting.add_listener
def _verdict(environment: Environment, **_: Any) -> None:
    refresh, url = _signed_in
    if refresh and url:
        _token_command("revoke", url, stdin=refresh)
    total = environment.stats.total
    p95 = total.get_response_time_percentile(0.95)
    p99 = total.get_response_time_percentile(0.99)
    ok = total.num_requests > 0 and total.num_failures == 0 and p95 <= P95_MS and p99 <= P99_MS
    print(
        f"{'PASS' if ok else 'FAIL'}  {total.num_requests} calls, {total.num_failures} failures, "
        f"p95 {p95} ms (<= {P95_MS}), p99 {p99} ms (<= {P99_MS})"  # ASCII: Windows consoles are cp1252
    )
    environment.process_exit_code = 0 if ok else 1


class RampHold(LoadTestShape):
    """Linear ramp to USERS over RAMP_S, then hold for HOLD_S (DD §21.3); ``-t`` cuts it short."""

    use_common_options = True  # lets the shape read --run-time

    def tick(self) -> tuple[int, float] | None:
        t = self.get_run_time()
        options = self.runner.environment.parsed_options if self.runner else None
        end = min(RAMP_S + HOLD_S, getattr(options, "run_time", None) or RAMP_S + HOLD_S)
        if t >= end:
            return None
        return max(1, round(USERS * min(1.0, t / RAMP_S))), 1.0


class VoiceCaller(HttpUser):
    wait_time = constant_throughput(CALLS_PER_USER_S)

    def on_start(self) -> None:
        assert _tokens is not None
        self.headers = _headers(next(_tokens))
        self.pending: list[str] = []

    def _call(self, tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
        """The tool's structured result; None on a failure or an expected business error."""
        with self.client.post(
            PATH, json=_message(tool, args), headers=self.headers, name=tool, catch_response=True
        ) as r:
            if r.status_code != 200:
                r.failure(f"HTTP {r.status_code}")
                return None
            body = r.json()
            if "result" not in body:
                r.failure(f"JSON-RPC error {body.get('error', {}).get('code')}")
                return None
            out = body["result"].get("structuredContent") or {}
            if not body["result"].get("isError"):
                r.success()
                return out
            code = (out.get("error") or {}).get("code")
            if code in FAILURES or not code:
                r.failure(f"tool error {code or 'without a code'}")
            else:
                r.success()
            return None

    @task(50)
    def briefing(self) -> None:
        self._call("get_risk_briefing", {})

    @task(25)
    def explain(self) -> None:
        self._call("explain_deal_risk", {"deal_id": random.choice(_deals)["deal_id"]})

    @task(10)
    def lookup(self) -> None:
        self._call("lookup_deal", {"name_query": random.choice(_deals)["account"]})

    @task(10)
    def propose(self) -> None:
        deal = random.choice(_deals)
        args = {
            "deal_id": deal["deal_id"],
            "action_type": "create_task",
            "assignee_name": deal["owner"],
            "title": "Load test: confirm next step",
        }
        out = self._call("propose_action", args)
        if out and out.get("confirmation_token"):
            self.pending.append(out["confirmation_token"])

    @task(5)
    def confirm(self) -> None:
        if self.pending:
            self._call("confirm_action", {"confirmation_token": self.pending.pop()})
        else:  # nothing to confirm yet: keep the call rate, not the exact split
            self.propose()
