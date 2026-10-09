"""Repositories for the ``auth`` schema (DD §12.4). Connects as ``wf_auth``, which sees nothing else.

Codes and refresh tokens are stored only as SHA-256 hashes. Refresh tokens
rotate on every use; presenting a rotated (or revoked) token revokes its
whole family, which is how a stolen refresh token is detected.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import asyncpg
from watchfloor_core.db import dsn_from_secret
from watchfloor_core.settings import Settings

CodeStatus = Literal["ok", "missing", "used", "expired"]
RefreshStatus = Literal["ok", "missing", "revoked", "reused", "expired"]


def sha256(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


@dataclass(frozen=True)
class Client:
    client_id: str
    name: str
    secret_hash: str | None
    redirect_uris: tuple[str, ...]
    grant_types: frozenset[str]
    auth_method: str
    scopes: frozenset[str]

    @property
    def confidential(self) -> bool:
        return self.secret_hash is not None


def auth_dsn(settings: Settings) -> str:
    if settings.auth_database_url:
        return settings.auth_database_url
    if settings.auth_db_secret_arn:
        return dsn_from_secret(settings.auth_db_secret_arn)
    raise RuntimeError("set WF_AUTH_DATABASE_URL (local) or WF_AUTH_DB_SECRET_ARN (AWS)")


class AuthStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def open(cls, dsn: str, *, min_size: int = 1, max_size: int = 2) -> AuthStore:
        return cls(await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size))

    async def close(self) -> None:
        await self._pool.close()

    @asynccontextmanager
    async def _tx(self) -> AsyncIterator[asyncpg.Connection]:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SET LOCAL statement_timeout = '2s'")
            yield conn

    async def ping(self) -> bool:
        try:
            async with self._pool.acquire(timeout=1) as conn:
                return await conn.fetchval("SELECT 1", timeout=1) == 1
        except Exception:
            return False

    # -- clients and credentials
    async def client(self, client_id: str) -> Client | None:
        async with self._tx() as conn:
            row = await conn.fetchrow(
                """SELECT client_id, client_name, client_secret_hash, redirect_uris, grant_types,
                          token_endpoint_auth_method, scopes
                   FROM auth.clients WHERE client_id = $1""",
                client_id,
            )
        if row is None:
            return None
        return Client(
            client_id=row["client_id"],
            name=row["client_name"],
            secret_hash=row["client_secret_hash"],
            redirect_uris=tuple(row["redirect_uris"]),
            grant_types=frozenset(row["grant_types"]),
            auth_method=row["token_endpoint_auth_method"],
            scopes=frozenset(row["scopes"]),
        )

    async def credential(self, email: str) -> asyncpg.Record | None:
        async with self._tx() as conn:
            return await conn.fetchrow(
                "SELECT email, user_id, workspace_id, password_hash FROM auth.credentials WHERE email = $1",
                email.strip().lower(),
            )

    async def rate_hit(self, subject: str) -> int:
        async with self._tx() as conn:
            return await conn.fetchval(
                """INSERT INTO auth.rate_limits (subject, window_start, count)
                   VALUES ($1, date_trunc('minute', now()), 1)
                   ON CONFLICT (subject, window_start) DO UPDATE SET count = auth.rate_limits.count + 1
                   RETURNING count""",
                subject,
            )

    # -- authorization codes (60 s, single use)
    async def save_code(
        self,
        *,
        code: str,
        client_id: str,
        user_id: str,
        workspace_id: str,
        redirect_uri: str,
        code_challenge: str,
        resource: str,
        scope: str,
        expires_at: datetime,
    ) -> None:
        async with self._tx() as conn:
            await conn.execute(
                """INSERT INTO auth.auth_codes (code_hash, client_id, user_id, workspace_id, redirect_uri,
                                                code_challenge, resource, scope, expires_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                sha256(code),
                client_id,
                user_id,
                workspace_id,
                redirect_uri,
                code_challenge,
                resource,
                scope,
                expires_at,
            )

    async def take_code(self, code: str, now: datetime) -> tuple[CodeStatus, asyncpg.Record | None]:
        async with self._tx() as conn:
            row = await conn.fetchrow(
                """SELECT client_id, user_id, workspace_id, redirect_uri, code_challenge, resource, scope,
                          expires_at, used_at
                   FROM auth.auth_codes WHERE code_hash = $1 FOR UPDATE""",
                sha256(code),
            )
            if row is None:
                return "missing", None
            if row["used_at"] is not None:
                return "used", row
            if row["expires_at"] <= now:
                return "expired", row
            await conn.execute(
                "UPDATE auth.auth_codes SET used_at = $2 WHERE code_hash = $1", sha256(code), now
            )
            return "ok", row

    # -- refresh tokens (rotating families)
    async def save_refresh(
        self,
        *,
        token: str,
        family_id: str,
        client_id: str,
        user_id: str,
        workspace_id: str,
        scope: str,
        resource: str,
        expires_at: datetime,
        at: datetime,
    ) -> None:
        async with self._tx() as conn:
            await self._insert_refresh(
                conn, token, family_id, client_id, user_id, workspace_id, scope, resource, expires_at, at
            )

    @staticmethod
    async def _insert_refresh(
        conn: asyncpg.Connection,
        token: str,
        family_id: str,
        client_id: str,
        user_id: str,
        workspace_id: str,
        scope: str,
        resource: str,
        expires_at: datetime,
        at: datetime,
    ) -> None:
        await conn.execute(
            """INSERT INTO auth.refresh_tokens (token_hash, family_id, client_id, user_id, workspace_id, scope,
                                                resource, expires_at, created_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
            sha256(token),
            family_id,
            client_id,
            user_id,
            workspace_id,
            scope,
            resource,
            expires_at,
            at,
        )

    async def get_refresh(self, token: str) -> asyncpg.Record | None:
        async with self._tx() as conn:
            return await conn.fetchrow(
                """SELECT family_id, client_id, user_id, workspace_id, scope, resource
                   FROM auth.refresh_tokens WHERE token_hash = $1""",
                sha256(token),
            )

    async def rotate_refresh(
        self, token: str, *, new_token: str, now: datetime, expires_at: datetime
    ) -> tuple[RefreshStatus, asyncpg.Record | None]:
        async with self._tx() as conn:
            row = await conn.fetchrow(
                """SELECT family_id, client_id, user_id, workspace_id, scope, resource, expires_at,
                          rotated_at, revoked_at
                   FROM auth.refresh_tokens WHERE token_hash = $1 FOR UPDATE""",
                sha256(token),
            )
            if row is None:
                return "missing", None
            if row["revoked_at"] is not None or row["rotated_at"] is not None:
                # Replay of a spent token: assume theft and burn the whole family.
                await self._revoke_family(conn, row["family_id"], now)
                return ("revoked" if row["revoked_at"] is not None else "reused"), row
            if row["expires_at"] <= now:
                return "expired", row
            await conn.execute(
                "UPDATE auth.refresh_tokens SET rotated_at = $2 WHERE token_hash = $1", sha256(token), now
            )
            await self._insert_refresh(
                conn,
                new_token,
                row["family_id"],
                row["client_id"],
                row["user_id"],
                row["workspace_id"],
                row["scope"],
                row["resource"],
                expires_at,
                now,
            )
            return "ok", row

    async def revoke_refresh(self, token: str, client_id: str, now: datetime) -> bool:
        async with self._tx() as conn:
            family = await conn.fetchval(
                "SELECT family_id FROM auth.refresh_tokens WHERE token_hash = $1 AND client_id = $2",
                sha256(token),
                client_id,
            )
            if family is None:
                return False
            await self._revoke_family(conn, family, now)
            return True

    @staticmethod
    async def _revoke_family(conn: asyncpg.Connection, family_id: str, now: datetime) -> None:
        await conn.execute(
            "UPDATE auth.refresh_tokens SET revoked_at = $2 WHERE family_id = $1 AND revoked_at IS NULL",
            family_id,
            now,
        )
