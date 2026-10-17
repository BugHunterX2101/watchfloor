"""Shared plumbing for the worker Lambdas, the ECS worker service and their local command-line twins.

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


def _unit(name: str) -> str:
    return "Milliseconds" if name.endswith("Ms") else "Count"


@lru_cache(maxsize=1)
def _cloudwatch() -> Any:
    import boto3  # lazy: only in AWS
    from botocore.config import Config

    # Short and bounded: a metric must never hold up the work it describes.
    return boto3.client(
        "cloudwatch", config=Config(connect_timeout=2, read_timeout=3, retries={"max_attempts": 2})
    )


def put_metrics(dims: dict[str, str], values: dict[str, float]) -> None:
    """The same values through PutMetricData, where stdout is not read as EMF (ECS, ADR-025)."""
    dimensions = [{"Name": k, "Value": v} for k, v in dims.items()]
    try:
        _cloudwatch().put_metric_data(
            Namespace=NAMESPACE,
            MetricData=[
                {"MetricName": name, "Dimensions": dimensions, "Value": float(value), "Unit": _unit(name)}
                for name, value in values.items()
            ],
        )
    except Exception as exc:  # noqa: BLE001 - observability is best effort
        log.warning("metrics not sent (%s): %s %s", type(exc).__name__, dims, values)


def emit_metrics(service: str, values: dict[str, float], dimensions: dict[str, str] | None = None) -> None:
    """One EMF record. Names ending in ``Ms`` are milliseconds; everything else is a count.

    Inside Lambda stdout feeds CloudWatch; on ECS (``WF_METRICS_API=true``) the values go through
    the metrics API instead; locally they go to the debug log.
    """
    dims = {"service": service, **(dimensions or {})}
    if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        if os.environ.get("WF_METRICS_API", "").lower() == "true":
            put_metrics(dims, values)
        else:
            log.debug("metrics %s %s", dims, values)
        return
    record = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": NAMESPACE,
                    "Dimensions": [list(dims)],
                    "Metrics": [{"Name": name, "Unit": _unit(name)} for name in values],
                }
            ],
        },
        **dims,
        **values,
    }
    print(json.dumps(record), flush=True)


@lru_cache(maxsize=1)
def _secrets_client() -> Any:
    import boto3  # lazy: only in AWS
    from botocore.config import Config

    # Bounded like the other clients: a stuck secrets call must not hold up a dispatch or the canary.
    return boto3.client(
        "secretsmanager", config=Config(connect_timeout=3, read_timeout=5, retries={"total_max_attempts": 3})
    )


@lru_cache(maxsize=8)
def secret_string(arn: str) -> str:
    return _secrets_client().get_secret_value(SecretId=arn)["SecretString"]


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
