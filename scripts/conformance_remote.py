"""MCP conformance (DD §22.3) against a running server: local ``make dev`` or a deployed environment.

    uv run python scripts/conformance_remote.py                      # local `make dev`
    uv run python scripts/conformance_remote.py --env dev            # URL from infra/config/dev.yaml
    uv run python scripts/conformance_remote.py --url $URL --token $TOKEN --json out.json

Read-only, so it is safe against demo: CF-15 covers the five read tools a user token can call, not
the write tools (the in-process suite covers those). Not checkable from outside: CF-04 needs a
job-bound agent token, and CF-12's Host check runs only against a local URL, because API Gateway
routes by Host and never forwards a foreign one. Token and sign-in: see ``remote_mcp``.
Exit status 1 when any case fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import orjson
from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remote_mcp import PROTOCOL_VERSION, Remote, connect, default_url, is_local, prm_url  # noqa: E402

USER_TOOLS = [
    "get_risk_briefing",
    "explain_deal_risk",
    "lookup_deal",
    "start_deep_triage",
    "get_triage_result",
    "propose_action",
    "extend_preview",
    "list_standing_orders",
    "confirm_action",
    "undo_action",
    "set_standing_order",
    "remove_standing_order",
    "log_deal_update",
    "update_briefing_preferences",
]
LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "conformance-remote", "version": "1"},
    },
}

Case = Callable[[Remote], Awaitable[str | None]]  # returns a note, or None; raises AssertionError on failure


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


async def cf01(m: Remote) -> None:
    h = m.headers()
    h.pop("MCP-Protocol-Version")
    r = await m.http.post(m.url, json=INITIALIZE, headers=h)
    _check(r.status_code == 200, f"HTTP {r.status_code}")
    _check(r.json()["result"]["protocolVersion"] == PROTOCOL_VERSION, "protocol version not echoed")
    _check("mcp-session-id" not in r.headers, "server issued MCP-Session-Id")


async def cf02(m: Remote) -> None:
    r = await m.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
    _check(r.status_code == 202 and r.content == b"", f"HTTP {r.status_code}, body {r.content[:80]!r}")


async def cf03(m: Remote) -> None:
    from mcp_server.tools.snapshot import SNAPSHOT_PATH

    tools = await m.tools()
    _check([t["name"] for t in tools] == USER_TOOLS, f"tools: {[t['name'] for t in tools]}")
    _check(
        tools == orjson.loads(SNAPSHOT_PATH.read_bytes())["user"],
        "tools/list differs from tools.snapshot.json: the deployed code is not this checkout",
    )


async def cf05(m: Remote) -> None:
    h = m.headers()
    h.pop("MCP-Protocol-Version")
    r = await m.http.post(m.url, json=LIST, headers=h)
    _check(r.status_code == 400, f"HTTP {r.status_code}")


async def cf06(m: Remote) -> None:
    r = await m.post(LIST, **{"MCP-Protocol-Version": "2099-01-01"})
    _check(r.status_code == 400, f"HTTP {r.status_code}")


async def cf07(m: Remote) -> None:
    r = await m.post(LIST, Accept="application/json")
    _check(r.status_code == 406, f"HTTP {r.status_code}")


async def cf08(m: Remote) -> None:
    for method in ("GET", "DELETE"):
        r = await m.http.request(method, m.url, headers=m.headers())
        _check(r.status_code == 405, f"{method}: HTTP {r.status_code}")


async def cf09(m: Remote) -> None:
    r = await m.http.post(m.url, content=b'{"jsonrpc": "2.0", "id": ', headers=m.headers())
    _check(r.status_code == 400, f"HTTP {r.status_code}")
    _check(r.json()["error"]["code"] == -32700, f"error {r.json().get('error')}")


async def cf10(m: Remote) -> None:
    env = await m.rpc("watchfloor/selfDestruct")
    _check(env.get("error", {}).get("code") == -32601, f"envelope {env}")


async def cf11(m: Remote) -> None:
    env = await m.rpc("tools/call", {"name": "get_risk_briefing", "arguments": {"limit": 9}})
    err = env.get("error", {})
    _check(err.get("code") == -32602 and "limit" in err.get("message", ""), f"error {err}")


async def cf12(m: Remote) -> str | None:
    r = await m.post(LIST, Origin="https://evil.example")
    _check(r.status_code == 403, f"bad Origin: HTTP {r.status_code}")
    if not is_local(m.url):
        return "Host check skipped: API Gateway routes by Host"
    r = await m.post(LIST, Host="mcp.evil.example")
    _check(r.status_code == 421, f"bad Host: HTTP {r.status_code}")
    return None


async def cf13(m: Remote) -> None:
    for label, token in (("no token", None), ("garbage token", "not.a.jwt")):
        r = await Remote(m.http, m.url, token).post(LIST)
        _check(r.status_code == 401, f"{label}: HTTP {r.status_code}")
        _check("www-authenticate" not in {k.lower() for k in r.headers}, f"{label}: WWW-Authenticate sent")


async def cf14(m: Remote) -> None:
    suffixed = prm_url(m.url)
    for url in dict.fromkeys([suffixed.removesuffix("/mcp"), suffixed]):
        r = await m.http.get(url)
        _check(r.status_code == 200, f"{url}: HTTP {r.status_code}")
        doc = r.json()
        _check(doc["resource"] == m.url, f"resource {doc['resource']!r} is not {m.url!r}")
        _check(bool(doc.get("authorization_servers")), "no authorization_servers")
        _check(doc["scopes_supported"] == ["watchfloor.read", "watchfloor.write"], f"scopes {doc}")
        _check(doc["bearer_methods_supported"] == ["header"], "bearer_methods_supported")


async def cf15(m: Remote) -> str:
    tools = {t["name"]: t for t in await m.tools()}

    async def run(name: str, args: dict[str, Any], *, may_fail_with: str = "") -> dict[str, Any] | None:
        result = await m.call(name, args)
        structured = result["structuredContent"]
        if (
            may_fail_with
            and result.get("isError")
            and structured.get("error", {}).get("code") == may_fail_with
        ):
            return None  # a documented tool error, not a schema result
        _check(not result.get("isError"), f"{name}: {structured}")
        errors = list(Draft202012Validator(tools[name]["outputSchema"]).iter_errors(structured))
        _check(not errors, f"{name}: {[e.message for e in errors[:3]]}")
        text = [c for c in result["content"] if c["type"] == "text"]
        _check(bool(text) and orjson.loads(text[0]["text"]) == structured, f"{name}: text twin differs")
        _check(next(iter(structured)) == "summary", f"{name}: summary is not first")
        return structured

    await run("get_risk_briefing", {})
    found = await run("lookup_deal", {"name_query": "kestrel"})
    assert found is not None
    _check(bool(found["candidates"]), "lookup_deal found no Kestrel deal: is the workspace seeded?")
    deal_id = found["candidates"][0]["deal_id"]
    await run("explain_deal_risk", {"deal_id": deal_id})
    # A freshly seeded workspace has no deep review yet (the seed creates no triage jobs, and a
    # read-only check cannot start one), so the documented JOB_NOT_FOUND answer is a pass too.
    triage = await run("get_triage_result", {"deal_id": deal_id}, may_fail_with="JOB_NOT_FOUND")
    await run("list_standing_orders", {})
    return "5 read tools" if triage is not None else "4 read tools; get_triage_result: no review yet"


CASES: list[tuple[str, str, Case]] = [
    ("CF-01", "initialize echoes the version, no session", cf01),
    ("CF-02", "notification is 202 with an empty body", cf02),
    ("CF-03", "14 user tools, matching the snapshot", cf03),
    ("CF-05", "missing MCP-Protocol-Version is 400", cf05),
    ("CF-06", "unsupported version is 400", cf06),
    ("CF-07", "Accept without event-stream is 406", cf07),
    ("CF-08", "GET and DELETE are 405", cf08),
    ("CF-09", "malformed JSON is 400 / -32700", cf09),
    ("CF-10", "unknown method is -32601", cf10),
    ("CF-11", "schema violation is -32602 with the field", cf11),
    ("CF-12", "bad Origin 403 (bad Host 421 locally)", cf12),
    ("CF-13", "no or bad token is 401 without WWW-Authenticate", cf13),
    ("CF-14", "PRM resource is the canonical URL", cf14),
    ("CF-15", "structuredContent matches outputSchema", cf15),
]


async def run(url: str, *, token: str | None, env: str | None) -> list[dict[str, Any]]:
    results = []
    async with connect(url, token=token, env=env) as m:
        for cid, title, case in CASES:
            try:
                note = await case(m)
                results.append({"id": cid, "case": title, "ok": True, "note": note or ""})
            except (AssertionError, KeyError, ValueError) as exc:
                results.append(
                    {"id": cid, "case": title, "ok": False, "note": f"{type(exc).__name__}: {exc}"}
                )
    return results


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="the MCP endpoint (default: local, or from infra/config/<env>.yaml)")
    ap.add_argument("--token", help="bearer token (default: WF_MCP_TOKEN, else canary sign-in)")
    ap.add_argument("--env", choices=["local", "dev", "demo"], help="dev|demo: canary credentials from AWS")
    ap.add_argument("--json", type=Path, help="also write the results here")
    opts = ap.parse_args(argv)
    opts.url = opts.url or default_url(opts.env)

    results = asyncio.run(run(opts.url, token=opts.token, env=opts.env))
    for r in results:
        mark = "PASS" if r["ok"] else "FAIL"
        print(f"{mark}  {r['id']}  {r['case']}" + (f"  ({r['note']})" if r["note"] else ""))
    failed = sum(not r["ok"] for r in results)
    print(f"\n{len(results) - failed}/{len(results)} passed against {opts.url}")
    if opts.json:
        opts.json.write_text(json.dumps({"url": opts.url, "results": results}, indent=2), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
