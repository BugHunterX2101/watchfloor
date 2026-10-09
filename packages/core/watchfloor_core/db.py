"""asyncpg pool and workspace-scoped transactions (TD §6.1).

``ws_tx`` is the only way request code touches tenant data: it opens a
transaction, sets ``app.workspace_id`` for row-level security and a 2 s
statement timeout. If the setting were missing, RLS would return zero rows.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import orjson

from .settings import get_settings

_pool: asyncpg.Pool | None = None


def _dumps(value: Any) -> str:
    return orjson.dumps(value).decode()


async def _init_connection(conn: asyncpg.Connection) -> None:
    for typ in ("jsonb", "json"):
        await conn.set_type_codec(typ, encoder=_dumps, decoder=orjson.loads, schema="pg_catalog")


def resolve_dsn() -> str:
    s = get_settings()
    if s.database_url:
        return s.database_url
    if s.db_secret_arn:
        return dsn_from_secret(s.db_secret_arn)
    raise RuntimeError("set WF_DATABASE_URL (local) or WF_DB_SECRET_ARN (AWS)")


def dsn_from_secret(arn: str) -> str:
    import json

    import boto3  # lazy: only in AWS

    secret = json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"])
    return (
        f"postgresql://{secret['username']}:{secret['password']}@{secret['host']}:"
        f"{secret.get('port', 5432)}/{secret.get('dbname', 'watchfloor')}?sslmode=require"
    )


async def open_pool(*, dsn: str | None = None, min_size: int = 1, max_size: int = 2) -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn or resolve_dsn(), min_size=min_size, max_size=max_size, init=_init_connection
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("database pool is not open")
    return _pool


@asynccontextmanager
async def ws_tx(workspace_id: str, *, statement_timeout: str = "2s") -> AsyncIterator[asyncpg.Connection]:
    """Transaction with RLS scoped to one workspace. Fails closed if unset."""
    if not workspace_id:
        raise ValueError("workspace_id is required")
    async with pool().acquire() as conn, conn.transaction():
        # One round trip for both transaction-local settings.
        await conn.execute(
            "SELECT set_config('app.workspace_id', $1, true), set_config('statement_timeout', $2, true)",
            workspace_id,
            statement_timeout,
        )
        yield conn


async def ping() -> bool:
    """Liveness probe for /healthz: True when a pooled connection answers."""
    try:
        async with pool().acquire(timeout=1) as conn:
            return await conn.fetchval("SELECT 1", timeout=1) == 1
    except Exception:
        return False


@asynccontextmanager
async def system_tx() -> AsyncIterator[asyncpg.Connection]:
    """Transaction without a workspace: only non-tenant tables (workspaces, rate_limits) are visible."""
    async with pool().acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL statement_timeout = '2s'")
        yield conn
