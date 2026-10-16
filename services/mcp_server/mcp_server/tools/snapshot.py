"""Writes (or checks) schemas/tools.snapshot.json: the exact tools/list each audience sees.

CI runs ``python -m mcp_server.tools.snapshot --check``; any change to a tool
name, description or schema fails the build until the snapshot is regenerated
and committed, then the utterance sheet is re-run in the harness (TD §15.4).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import orjson
from watchfloor_core.principal import USER_SCOPES, Principal, Scope

from . import visible

SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "schemas" / "tools.snapshot.json"


def build() -> dict[str, list[dict[str, Any]]]:
    audiences = {
        "user": Principal(user_id="snapshot", workspace_id="snapshot", scopes=USER_SCOPES),
        "agent": Principal(
            user_id="", workspace_id="snapshot", scopes=frozenset({Scope.AGENT_READ}), job_id="job_snapshot"
        ),
    }
    return {
        name: [
            spec.descriptor.model_dump(mode="json", by_alias=True, exclude_none=True) for spec in visible(p)
        ]
        for name, p in audiences.items()
    }


def render() -> bytes:
    return orjson.dumps(build(), option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS) + b"\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="fail if the committed snapshot is stale")
    opts = ap.parse_args(argv)
    fresh = render()
    if opts.check:
        current = SNAPSHOT_PATH.read_bytes() if SNAPSHOT_PATH.exists() else b""
        if current != fresh:
            print(f"{SNAPSHOT_PATH} is stale; run python -m mcp_server.tools.snapshot", file=sys.stderr)
            return 1
        print("tools snapshot is current")
        return 0
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_bytes(fresh)
    print(f"wrote {SNAPSHOT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
