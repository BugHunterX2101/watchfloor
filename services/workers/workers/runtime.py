"""Shared plumbing for the worker Lambdas and their local command-line twins.

Each Lambda keeps one event loop for the life of its container, so the asyncpg
pool opened by the first invocation is reused by warm ones. Metrics go out as
CloudWatch Embedded Metric Format lines on stdout: no agent or SDK is needed,
and locally they are just JSON lines in the console.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from collections.abc import Coroutine
from functools import lru_cache
from typing import Any

from watchfloor_core import db
from watchfloor_core.repos import workspaces

log = logging.getLogger("watchfloor.workers")
NAMESPACE = os.environ.get("POWERTOOLS_METRICS_NAMESPACE", "Watchfloor")
_loop: asyncio.AbstractEventLoop | None = None


def configure_logging() -> None:
    # Windows consoles default to cp1252; Slack previews and spoken text carry "·", "•" and "—".
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )


def run_in_lambda[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a handler coroutine on the container's long-lived loop (keeps the pool warm)."""
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
    return _loop.run_until_complete(coro)


async def ensure_pool(max_size: int = 2) -> None:
    await db.open_pool(min_size=1, max_size=max_size)


async def workspace_ids() -> list[str]:
    async with db.system_tx() as conn:
        return await workspaces.all_ids(conn)


def emit_metrics(service: str, values: dict[str, float], dimensions: dict[str, str] | None = None) -> None:
    """One EMF record. Names ending in ``Ms`` are milliseconds; everything else is a count.

    Only inside Lambda, where stdout feeds CloudWatch; locally the values go to the debug log.
    """
    dims = {"service": service, **(dimensions or {})}
    if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        log.debug("metrics %s %s", dims, values)
        return
    record = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": NAMESPACE,
                    "Dimensions": [list(dims)],
                    "Metrics": [
                        {"Name": name, "Unit": "Milliseconds" if name.endswith("Ms") else "Count"}
                        for name in values
                    ],
                }
            ],
        },
        **dims,
        **values,
    }
    print(json.dumps(record), flush=True)


@lru_cache(maxsize=8)
def secret_string(arn: str) -> str:
    import boto3  # lazy: only in AWS

    return boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]


def secret_field(arn: str, field: str) -> str:
    """A field of a JSON secret, or the whole secret when it is a plain string."""
    raw = secret_string(arn)
    try:
        value = json.loads(raw)
    except ValueError:
        return raw
    return str(value[field]) if isinstance(value, dict) else raw


def elapsed_ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)
