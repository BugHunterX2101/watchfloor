"""The restore drill notices a bad restore (plan phase 3): data lost or altered, isolation or grants undone.

A clean copy must compare equal; then a copy is damaged one way at a time and each way must be named.
The end-to-end drill (real pg_dump and pg_restore) runs when the Postgres container is reachable.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import time
from argparse import Namespace
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType

import asyncpg
import pytest

from db.migrate import migrate
from db.seed.__main__ import seed
from tests.conftest import ADMIN_URL, FROZEN_NOW, PG_HOST, TEST_DB, WS


def _load() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "restore_drill.py"
    spec = importlib.util.spec_from_file_location("wf_restore_drill", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


drill = _load()
STAMP = int(time.time())
ORIGINAL, COPY = f"wf_drill_a_{STAMP}", f"wf_drill_b_{STAMP}"


def _owner(db: str) -> str:
    return f"postgresql://wf_owner:devpassword@{PG_HOST}/{db}"


def _app(db: str) -> str:
    return f"postgresql://wf_app:devpassword@{PG_HOST}/{db}"


@pytest.fixture(scope="module")
async def databases(seeded: str) -> AsyncIterator[tuple[str, str]]:
    """A migrated, seeded database and a bit-for-bit copy of it (a template copy)."""
    admin = await asyncpg.connect(ADMIN_URL)
    try:
        await admin.execute(f'CREATE DATABASE "{ORIGINAL}" OWNER wf_owner')
        await migrate(_owner(ORIGINAL), dev_passwords=True, verbose=False)
        await seed(_owner(ORIGINAL), workspace=WS, seed_value=42, now=FROZEN_NOW, verbose=False)
        await admin.execute(f'CREATE DATABASE "{COPY}" TEMPLATE "{ORIGINAL}" OWNER wf_owner')
        yield ORIGINAL, COPY
    finally:
        for name in (ORIGINAL, COPY):
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def _run(url: str, *statements: str) -> None:
    conn = await asyncpg.connect(url)
    try:
        for sql in statements:
            await conn.execute(sql)
    finally:
        await conn.close()


async def test_an_identical_copy_compares_equal_and_the_role_check_passes(databases: tuple[str, str]) -> None:
    original, copy = databases
    before, after = await drill.snapshot(_owner(original)), await drill.snapshot(_owner(copy))
    assert sum(n for scopes in before["data"].values() for n, _ in scopes.values()) > 500, "the seed is there"
    assert drill.diff(before, after) == []
    assert await drill.functional_check(_app(copy), before) == []


async def test_each_kind_of_damage_to_a_copy_is_named(databases: tuple[str, str]) -> None:
    original, copy = databases
    before = await drill.snapshot(_owner(original))
    owner, app = _owner(copy), _app(copy)

    async def problems() -> list[str]:
        return drill.diff(before, await drill.snapshot(owner)) + await drill.functional_check(app, before)

    seen: set[str] = set()

    async def damage(label: str, *statements: str) -> list[str]:
        await _run(owner, *statements)
        found = [p for p in await problems() if p not in seen]
        seen.update(found)
        assert found, f"damage not noticed: {label}"
        return found

    assert await problems() == []
    scope = f"SELECT set_config('app.workspace_id', '{WS}', false)"

    # Rows lost, and rows changed without changing how many there are.
    lost = await damage("rows lost", scope, "DELETE FROM risk_score_daily WHERE score > 0")
    assert any("rows became" in p for p in lost)
    changed = await damage(
        "a value changed",
        scope,
        "UPDATE users SET display_name = display_name || '!' WHERE id = (SELECT min(id) FROM users)",
    )
    assert any("different content" in p for p in changed)

    # Isolation undone: the switch, a policy, a grant.
    switched = await damage("RLS forcing removed", "ALTER TABLE deals NO FORCE ROW LEVEL SECURITY")
    assert any("deals" in p and "flags" in p for p in switched)
    dropped = await damage("a policy dropped", "DROP POLICY ws_isolation ON contacts")
    assert any("policies" in p and "contacts" in p for p in dropped)
    granted = await damage("a grant added", "GRANT DELETE ON workspaces TO wf_app")
    assert any("table_grants" in p and "workspaces" in p for p in granted)

    # What the services themselves would see: RLS off means wf_app reads tenant rows with no workspace set.
    exposed = await damage("RLS disabled", "ALTER TABLE users DISABLE ROW LEVEL SECURITY")
    assert any("wf_app saw" in p and "no workspace set" in p for p in exposed)


def _docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    probe = ["docker", "exec", drill.CONTAINER, "pg_dump", "--version"]
    return subprocess.run(probe, capture_output=True, check=False).returncode == 0  # noqa: S603


@pytest.mark.skipif(not _docker_ready(), reason=f"container {drill.CONTAINER} (pg_dump) not reachable")
async def test_a_real_dump_and_restore_round_trips_and_then_upgrades(seeded: str) -> None:
    opts = Namespace(
        url=_owner("postgres"),
        source=TEST_DB,
        container=drill.CONTAINER,
        app_password="devpassword",
        migrate=True,
        keep=False,
    )
    report = await drill.drill(opts)
    assert report["problems"] == [] and report["ok"] is True
    assert report["tables"] >= 19 and report["rows"] > 500
    assert report["migrated"] == [], "the test database is already at the latest migration"
