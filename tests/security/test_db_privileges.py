"""What the application's database role can and cannot do, proved as that role (plan P0, phase 2).

The services connect as ``wf_app``. Row-level security keeps it inside one workspace (0002) and the
grants keep it from rewriting the things that account for its actions (0006).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import asyncpg
import pytest

from tests.conftest import APP_URL, FROZEN_NOW, OWNER_URL, WS

OTHER = "ws_other"


@pytest.fixture
async def wf_app(seeded: str) -> AsyncIterator[asyncpg.Connection]:
    """A fresh wf_app connection inside a transaction that is always rolled back."""
    conn = await asyncpg.connect(APP_URL)
    tx = conn.transaction()
    await tx.start()
    try:
        yield conn
    finally:
        await tx.rollback()
        await conn.close()


async def _scope(conn: asyncpg.Connection, workspace: str | None) -> None:
    await conn.execute("SELECT set_config('app.workspace_id', $1, true)", workspace or "")


async def _tenant_tables() -> list[str]:
    conn = await asyncpg.connect(OWNER_URL)
    try:
        rows = await conn.fetch(
            """SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               JOIN information_schema.columns k ON k.table_schema = 'public' AND k.table_name = c.relname
                                                AND k.column_name = 'workspace_id'
               WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY 1"""
        )
    finally:
        await conn.close()
    return [r["relname"] for r in rows]


async def test_every_table_with_a_workspace_column_forces_row_level_security(seeded: str) -> None:
    """A table added later without RLS would silently be readable across tenants: fail the build instead."""
    conn = await asyncpg.connect(OWNER_URL)
    try:
        weak = await conn.fetch(
            """SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = 'public' AND c.relkind = 'r'
                 AND EXISTS (SELECT 1 FROM information_schema.columns k WHERE k.table_schema = 'public'
                             AND k.table_name = c.relname AND k.column_name = 'workspace_id')
                 AND NOT (c.relrowsecurity AND c.relforcerowsecurity
                          AND EXISTS (SELECT 1 FROM pg_policies p WHERE p.schemaname = 'public'
                                      AND p.tablename = c.relname))"""
        )
    finally:
        await conn.close()
    assert [r["relname"] for r in weak] == []


async def test_without_a_workspace_the_app_role_sees_no_tenant_rows(
    seeded: str, wf_app: asyncpg.Connection
) -> None:
    await _scope(wf_app, None)
    tables = await _tenant_tables()
    assert len(tables) >= 16, "the catalog query found too little"
    for table in tables:
        assert await wf_app.fetchval(f"SELECT count(*) FROM {table}") == 0, table  # noqa: S608


async def test_inside_another_workspace_nothing_of_lumenridge_is_visible_or_writable(
    other_workspace: str, wf_app: asyncpg.Connection
) -> None:
    await _scope(wf_app, other_workspace)
    for table in await _tenant_tables():
        outside = f"SELECT count(*) FROM {table} WHERE workspace_id <> $1"  # noqa: S608
        assert await wf_app.fetchval(outside, OTHER) == 0, table
        touch = f"UPDATE {table} SET workspace_id = workspace_id WHERE workspace_id = $1"  # noqa: S608
        if table != "audit_log":  # its UPDATE is revoked outright (0006), tested below
            assert await wf_app.execute(touch, WS) == "UPDATE 0", table
        assert await wf_app.execute(f"DELETE FROM {table} WHERE workspace_id = $1", WS) == "DELETE 0", table  # noqa: S608


async def test_the_app_role_cannot_write_into_another_workspace(
    other_workspace: str, wf_app: asyncpg.Connection
) -> None:
    await _scope(wf_app, other_workspace)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await wf_app.execute(
            """INSERT INTO users (id, workspace_id, display_name, email, role)
               VALUES ('us_PLANTED', $1, 'Planted', 'planted@lumenridge.example', 'rep')""",
            WS,
        )


async def test_a_row_cannot_be_moved_into_another_workspace(
    other_workspace: str, wf_app: asyncpg.Connection
) -> None:
    await _scope(wf_app, other_workspace)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await wf_app.execute("UPDATE users SET workspace_id = $1 WHERE id = 'us_OUTSIDER'", WS)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO workspaces (id, name) VALUES ('ws_rogue', 'Rogue Co')",
        "DELETE FROM workspaces WHERE id = 'ws_other'",
        "UPDATE workspaces SET name = 'Renamed' WHERE id = 'ws_other'",
        "UPDATE workspaces SET timezone = 'UTC' WHERE id = 'ws_other'",
        "SELECT version FROM schema_migrations",
        "DELETE FROM schema_migrations",
        "INSERT INTO schema_migrations (version, checksum) VALUES ('9999_rogue', 'x')",
    ],
)
async def test_the_app_role_cannot_create_delete_or_rename_tenants_or_touch_the_ledger(
    statement: str, other_workspace: str, wf_app: asyncpg.Connection
) -> None:
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await wf_app.execute(statement)


async def test_the_app_role_can_still_do_what_the_services_need(
    other_workspace: str, wf_app: asyncpg.Connection
) -> None:
    assert await wf_app.fetchval("SELECT count(*) FROM workspaces") >= 2, "the workers visit every workspace"
    done = await wf_app.execute(
        "UPDATE workspaces SET clock_offset = interval '1 hour' WHERE id = 'ws_other'"
    )
    assert done == "UPDATE 1", "the demo clock moves"


async def _audit(conn: asyncpg.Connection, at: Any) -> int:
    await _scope(conn, WS)
    return await conn.fetchval(
        """INSERT INTO audit_log (workspace_id, user_id, tool, args_hash, outcome, at)
           VALUES ($1, NULL, 'probe', '\\x00', 'ok', $2) RETURNING id""",
        WS,
        at,
    )


async def test_audit_entries_can_be_added_but_not_edited(wf_app: asyncpg.Connection) -> None:
    entry = await _audit(wf_app, FROZEN_NOW)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await wf_app.execute("UPDATE audit_log SET tool = 'edited' WHERE id = $1", entry)


async def test_recent_audit_entries_cannot_be_deleted_by_the_app_role(wf_app: asyncpg.Connection) -> None:
    entry = await _audit(wf_app, FROZEN_NOW)
    assert await wf_app.execute("DELETE FROM audit_log WHERE id = $1", entry) == "DELETE 0"
    assert await wf_app.fetchval("SELECT count(*) FROM audit_log WHERE id = $1", entry) == 1


async def test_old_audit_entries_can_still_be_purged_for_retention(wf_app: asyncpg.Connection) -> None:
    entry = await _audit(wf_app, FROZEN_NOW - timedelta(days=40))
    assert await wf_app.execute("DELETE FROM audit_log WHERE id = $1", entry) == "DELETE 1"
