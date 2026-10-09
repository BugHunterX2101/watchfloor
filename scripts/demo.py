"""Demo operations (DD §24, TD §17.3): reset · clock · watch-now · status · outbox-retry.

    uv run python scripts/demo.py reset                  # re-seed relative to now, assert the scenario scores
    uv run python scripts/demo.py clock --offset +14h    # the "next morning" scene, then one watcher run
    uv run python scripts/demo.py clock --offset 0       # back to real time
    uv run python scripts/demo.py watch-now              # one watcher run: fires standing orders on demand
    uv run python scripts/demo.py status                 # bands, board, proposals, jobs, outbox backlog
    uv run python scripts/demo.py outbox-retry --id 42   # re-arm a dead outbox row (or --all-dead)

Locally, commands talk to Postgres directly, as the owner role with row-level security scoped to
the workspace (``db.seed.ops``). With ``--env dev|demo`` they invoke the environment's Lambdas with
your AWS credentials (``lambda:InvokeFunction``): ``wf-<env>-seeder`` resets, sets the clock, reads
the status and re-arms outbox rows from inside the VPC; ``wf-<env>-watcher`` is the watcher run.

Clock offsets are honoured only by processes running with ``WF_DEMO_MODE=true``
(DD §24; ``demo_mode`` in ``infra/config/<env>.yaml``); elsewhere the stored offset is ignored.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db.seed import scenarios  # noqa: E402
from db.seed.ops import (  # noqa: E402, F401  (scoped and NAMES are re-exported for the tests)
    NAMES,
    describe_offset,
    parse_offset,
    render_status,
    retry_dead,
    scenarios_ok,
    scoped,
    set_offset,
    status,
    status_from_json,
)
from db.seed.ops import demo_mode as _demo_mode  # noqa: E402

LOCAL_OWNER_URL = "postgresql://wf_owner:devpassword@127.0.0.1:55432/watchfloor"
LOCAL_APP_URL = "postgresql://wf_app:devpassword@127.0.0.1:55432/watchfloor"
# Read timeouts for a Lambda invoke: the seeder's reset runs up to 5 minutes, the watcher 1.
RESET_TIMEOUT_S, WATCH_TIMEOUT_S, OP_TIMEOUT_S = 330, 90, 60


# ---------------------------------------------------------------- local commands


async def watch_now(app_url: str) -> dict[str, int]:
    from watchfloor_core import db
    from workers import watcher

    await db.open_pool(dsn=app_url, min_size=1, max_size=2)
    try:
        return await watcher.run()
    finally:
        await db.close_pool()


async def _reset(opts: argparse.Namespace) -> int:
    from watchfloor_core.clock import Clock

    from db.seed.__main__ import seed

    await seed(opts.url, workspace=opts.workspace, seed_value=opts.seed, now=Clock().now())
    print("clock offset cleared; scenario scores asserted")
    return 0


async def _clock(opts: argparse.Namespace) -> int:
    offset = parse_offset(opts.offset)
    async with scoped(opts.url, opts.workspace) as conn:
        await set_offset(conn, opts.workspace, offset)
    print(f"clock offset for {opts.workspace}: {describe_offset(offset)}")
    if offset and not _demo_mode():
        print("note: offsets are honoured only by processes running with WF_DEMO_MODE=true", file=sys.stderr)
    if not opts.no_watch:
        print("watcher", await watch_now(opts.app_url))
    return 0


async def _watch_now(opts: argparse.Namespace) -> int:
    print("watcher", await watch_now(opts.app_url))
    return 0


async def _status(opts: argparse.Namespace) -> int:
    async with scoped(opts.url, opts.workspace) as conn:
        st = await status(conn, opts.workspace)
    print(render_status(st, demo_mode=_demo_mode()))
    return 0 if scenarios_ok(st) else 1


async def _outbox_retry(opts: argparse.Namespace) -> int:
    async with scoped(opts.url, opts.workspace) as conn:
        rearmed = await retry_dead(conn, opts.workspace, opts.id or [], all_dead=opts.all_dead)
    print(_rearmed(rearmed))
    return 0


COMMANDS = {
    "reset": _reset,
    "clock": _clock,
    "watch-now": _watch_now,
    "status": _status,
    "outbox-retry": _outbox_retry,
}


def _rearmed(ids: list[int]) -> str:
    return f"re-armed {len(ids)} dead outbox row(s): {ids}" if ids else "no dead rows matched"


# ---------------------------------------------------------------- dev and demo, through the Lambdas


class RemoteError(Exception):
    """A Lambda invoke that failed, or a function that raised."""


def config_value(env: str, key: str) -> str | None:
    """A top-level scalar from ``infra/config/<env>.yaml`` (no YAML parser in the dev environment)."""
    path = ROOT / "infra" / "config" / f"{env}.yaml"
    if not path.is_file():
        return None
    match = re.search(rf"^{key}:\s*([^\s#]+)", path.read_text(encoding="utf-8"), re.MULTILINE)
    return match.group(1).strip("'\"") if match else None


def invoke(function: str, event: dict[str, Any], *, region: str | None, timeout: int = OP_TIMEOUT_S) -> Any:
    """Invoke ``function`` synchronously, once: a reset or a watcher run must not repeat on a retry."""
    import boto3
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError

    config = Config(region_name=region, read_timeout=timeout, retries={"max_attempts": 0})
    client = boto3.client("lambda", config=config)
    try:
        r = client.invoke(FunctionName=function, Payload=json.dumps(event).encode())
    except (BotoCoreError, ClientError) as exc:
        raise RemoteError(f"{function}: {exc}") from None
    body = json.loads(r["Payload"].read() or b"null")
    if r.get("FunctionError"):
        raise RemoteError(f"{function}: {body.get('errorType')}: {body.get('errorMessage')}")
    return body


def run_remote(opts: argparse.Namespace) -> int:
    seeder, watcher = f"wf-{opts.env}-seeder", f"wf-{opts.env}-watcher"
    region = config_value(opts.env, "region")  # the stacks' region, not the CLI's default

    def call(function: str, event: dict[str, Any], timeout: int = OP_TIMEOUT_S) -> Any:
        return invoke(function, event, region=region, timeout=timeout)

    if opts.command == "reset":
        result = call(seeder, {"bootstrap": False}, timeout=RESET_TIMEOUT_S)
        print(
            f"{result['workspace']} re-seeded {result['bands']}; clock offset cleared; scenario scores asserted"
        )
        return 0
    if opts.command == "watch-now":
        print("watcher", call(watcher, {}, timeout=WATCH_TIMEOUT_S))
        return 0
    if opts.command == "clock":
        parse_offset(opts.offset)  # refuse a bad offset before reaching AWS
        result = call(seeder, {"demo": "clock", "offset": opts.offset})
        print(f"clock offset for {result['workspace']}: {result['clock_offset']}")
        if not result["demo_mode"]:
            print(
                f"note: {opts.env} runs without WF_DEMO_MODE (demo_mode in infra/config/{opts.env}.yaml), "
                "so its services ignore the offset",
                file=sys.stderr,
            )
        if not opts.no_watch:
            print("watcher", call(watcher, {}, timeout=WATCH_TIMEOUT_S))
        return 0
    if opts.command == "status":
        result = call(seeder, {"demo": "status"})
        print(render_status(status_from_json(result["status"]), demo_mode=result["demo_mode"]))
        return 0 if result["scenarios_ok"] else 1
    result = call(seeder, {"demo": "outbox-retry", "ids": opts.id or [], "all_dead": opts.all_dead})
    print(_rearmed(result["rearmed"]))
    return 0


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--env", default="local", choices=("local", "dev", "demo"))
    common.add_argument("--workspace", default=scenarios.WORKSPACE_ID, help="local only")
    common.add_argument("--url", default=os.environ.get("WF_OWNER_DATABASE_URL", LOCAL_OWNER_URL))
    common.add_argument("--app-url", default=os.environ.get("WF_DATABASE_URL", LOCAL_APP_URL))

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    reset = sub.add_parser("reset", parents=[common], help="re-seed the demo workspace relative to now")
    reset.add_argument("--seed", type=int, default=42, help="local only (the seeder Lambda uses 42)")
    clock = sub.add_parser("clock", parents=[common], help="set the workspace clock offset")
    clock.add_argument("--offset", required=True, help="+14h, -30m, 2d or 0")
    clock.add_argument("--no-watch", action="store_true", help="do not run the watcher afterwards")
    sub.add_parser("watch-now", parents=[common], help="run the watcher once")
    sub.add_parser("status", parents=[common], help="bands, board, proposals, jobs and outbox")
    retry = sub.add_parser("outbox-retry", parents=[common], help="re-arm dead outbox rows")
    retry.add_argument("--id", type=int, action="append", help="outbox row id (repeatable)")
    retry.add_argument("--all-dead", action="store_true")
    return ap


def load_env_file(path: Path) -> list[str]:
    """Fill unset ``WF_*`` variables from ``.env``, as honcho does for ``make dev``.

    Without this, ``make demo-clock`` would run its watcher without ``WF_DEMO_MODE``
    and ignore the offset it just stored. Variables already set always win.
    """
    if not path.is_file():
        return []
    loaded = []
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if not sep or not key.startswith("WF_") or not value.strip() or key in os.environ:
            continue
        os.environ[key] = value.strip().strip("'\"")
        loaded.append(key)
    if loaded:
        from watchfloor_core.settings import reset_settings

        reset_settings()
    return loaded


def main(argv: list[str] | None = None, *, env_file: Path | None = ROOT / ".env") -> int:
    for stream in (sys.stdout, sys.stderr):  # "·" and "—" on a cp1252 console
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if env_file is not None:  # before parsing: the URL defaults read it (the remote path reads none of it)
        load_env_file(env_file)
    opts = build_parser().parse_args(argv)
    if opts.command == "outbox-retry" and not opts.id and not opts.all_dead:
        print("give --id N (repeatable) or --all-dead", file=sys.stderr)
        return 2
    try:
        if opts.env != "local":
            return run_remote(opts)
        return asyncio.run(COMMANDS[opts.command](opts))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (LookupError, RemoteError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
        print(f"database not reachable at {opts.url.split('@')[-1]}: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
