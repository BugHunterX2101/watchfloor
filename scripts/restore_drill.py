"""Backup and restore drill: prove a backup brings the system back, not just that a file exists.

    uv run python scripts/restore_drill.py drill --source watchfloor --migrate
    uv run python scripts/restore_drill.py compare --source-url <owner url> --target-url <owner url> \
        [--app-url <wf_app url of the target>]

``drill`` dumps a local database (``pg_dump -Fc`` inside the Postgres container), restores it into a
scratch database, checks the copy against the original, optionally applies the migrations the copy is
missing (an upgrade on realistic data) and drops the scratch database. ``compare`` is the checking half
on its own, for a database restored some other way: an RDS snapshot or point-in-time restore brought up
as a new instance is compared with the instance it came from, read-only.

What "restored" means here, every item checked and any difference listed:

- the same tables, each with the same row count and a content digest (per workspace for tenant tables,
  because the owner role is itself subject to row-level security and would otherwise see nothing);
- the same migration ledger;
- the same row-level security switches, policies and grants, i.e. the isolation came back with the data;
- as ``wf_app``, the way the services connect: no tenant rows without a workspace, and in each workspace
  the same counts the owner saw.

It does not prove an RDS backup is restorable (only AWS can do that): it proves the restored result is
right once you have one, and it measures how long the logical path takes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SCHEMAS = ("public", "auth")
LOCAL_OWNER_URL = "postgresql://wf_owner:devpassword@127.0.0.1:55432/watchfloor"
CONTAINER = "watchfloor-db-1"
DUMP_PATH = "/tmp/wf_restore_drill.dump"  # noqa: S108 - inside the throwaway database container
SCRATCH_PREFIX = "watchfloor_restore"
SECURITY_LISTS = ("migrations", "policies", "table_grants", "column_grants")

TABLES = """SELECT n.nspname AS schema, c.relname AS name,
                   EXISTS (SELECT 1 FROM information_schema.columns k WHERE k.table_schema = n.nspname
                           AND k.table_name = c.relname AND k.column_name = 'workspace_id') AS tenant,
                   c.relrowsecurity AS rls, c.relforcerowsecurity AS forced
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'r' AND n.nspname = ANY($1::text[]) ORDER BY 1, 2"""
POLICIES = """SELECT schemaname, tablename, policyname, permissive, roles::text, cmd, qual, with_check
              FROM pg_policies WHERE schemaname = ANY($1::text[]) ORDER BY 1, 2, 3"""
TABLE_GRANTS = """SELECT table_schema, table_name, grantee, privilege_type FROM information_schema.role_table_grants
                  WHERE table_schema = ANY($1::text[]) AND grantee LIKE 'wf\\_%' AND grantee <> 'wf_owner'
                  ORDER BY 1, 2, 3, 4"""
COLUMN_GRANTS = """SELECT table_schema, table_name, column_name, grantee, privilege_type
                   FROM information_schema.column_privileges
                   WHERE table_schema = ANY($1::text[]) AND grantee LIKE 'wf\\_%' AND grantee <> 'wf_owner'
                   ORDER BY 1, 2, 3, 4, 5"""


def ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def with_database(url: str, database: str) -> str:
    return urlunsplit(urlsplit(url)._replace(path="/" + database))


def as_role(url: str, role: str, password: str) -> str:
    parts = urlsplit(url)
    host = f"{parts.hostname}:{parts.port}" if parts.port else str(parts.hostname)
    return urlunsplit(parts._replace(netloc=f"{role}:{quote(password, safe='')}@{host}"))


def database_of(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


async def _digest(conn: asyncpg.Connection, schema: str, table: str) -> list[Any]:
    """Row count and an order-independent digest of every row's text form."""
    row = await conn.fetchrow(
        "SELECT count(*)::bigint AS n, coalesce(sum(hashtextextended(t::text, 0)::numeric), 0)::text AS d "  # noqa: S608
        f"FROM {ident(schema)}.{ident(table)} t"
    )
    return [row["n"], row["d"]]


async def snapshot(url: str) -> dict[str, Any]:
    """Everything the drill compares, read with the owner role. Row-level security is honoured, not bypassed."""
    conn = await asyncpg.connect(url)
    try:
        await conn.execute("SET timezone = 'UTC'")
        tables = await conn.fetch(TABLES, list(SCHEMAS))
        workspaces = [r["id"] for r in await conn.fetch("SELECT id FROM public.workspaces ORDER BY id")]
        data: dict[str, dict[str, list[Any]]] = {}
        for t in tables:
            key = f"{t['schema']}.{t['name']}"
            if not t["tenant"]:
                data[key] = {"*": await _digest(conn, t["schema"], t["name"])}
                continue
            data[key] = {}
            for ws in workspaces:  # one transaction per workspace: the policy needs it set
                async with conn.transaction():
                    await conn.execute("SELECT set_config('app.workspace_id', $1, true)", ws)
                    data[key][ws] = await _digest(conn, t["schema"], t["name"])
        ledger = await conn.fetch("SELECT version, checksum FROM public.schema_migrations ORDER BY version")
        return {
            "database": database_of(url),
            "tables": {f"{t['schema']}.{t['name']}": [t["tenant"], t["rls"], t["forced"]] for t in tables},
            "data": data,
            "migrations": [list(r.values()) for r in ledger],
            "policies": [[str(v) for v in r.values()] for r in await conn.fetch(POLICIES, list(SCHEMAS))],
            "table_grants": [list(r.values()) for r in await conn.fetch(TABLE_GRANTS, list(SCHEMAS))],
            "column_grants": [list(r.values()) for r in await conn.fetch(COLUMN_GRANTS, list(SCHEMAS))],
        }
    finally:
        await conn.close()


def _listed(label: str, source: list[Any], target: list[Any]) -> list[str]:
    missing = [f"{label}: missing from the copy: {x}" for x in source if x not in target]
    return missing + [f"{label}: only in the copy: {x}" for x in target if x not in source]


def diff(
    source: dict[str, Any], target: dict[str, Any], *, content: bool = True, security: bool = True
) -> list[str]:
    """Every way the copy differs from the original; an empty list means it is the same."""
    problems: list[str] = []
    if source["tables"].keys() != target["tables"].keys():
        problems += _listed("tables", sorted(source["tables"]), sorted(target["tables"]))
    for key in sorted(source["tables"].keys() & target["tables"].keys()):
        if security and source["tables"][key] != target["tables"][key]:
            problems.append(
                f"{key}: tenant/rls/force flags {source['tables'][key]} became {target['tables'][key]}"
            )
        for scope, (n, digest) in source["data"][key].items():
            got = target["data"][key].get(scope)
            if got is None:
                problems.append(f"{key}[{scope}]: scope missing from the copy")
            elif got[0] != n:
                problems.append(f"{key}[{scope}]: {n} rows became {got[0]}")
            elif content and got[1] != digest:
                problems.append(f"{key}[{scope}]: same row count, different content")
    if security:
        for label in SECURITY_LISTS:
            problems += _listed(label, source[label], target[label])
    return problems


async def functional_check(app_url: str, expected: dict[str, Any]) -> list[str]:
    """Connect the way the services do (wf_app): isolation must work, and see what the owner saw."""
    problems: list[str] = []
    try:
        conn = await asyncpg.connect(app_url)
    except (OSError, asyncpg.PostgresError) as exc:
        return [f"wf_app could not connect to the copy: {type(exc).__name__}: {exc}"]
    try:
        for key, (tenant, _rls, _forced) in expected["tables"].items():
            if not tenant or not key.startswith(
                "public."
            ):  # wf_app has no access to the auth schema, by design
                continue
            schema, table = key.split(".", 1)
            count = f"SELECT count(*) FROM {ident(schema)}.{ident(table)}"  # noqa: S608
            async with conn.transaction():
                seen = await conn.fetchval(count)
            if seen:
                problems.append(f"{key}: wf_app saw {seen} rows with no workspace set")
            for ws, (n, _) in expected["data"][key].items():
                async with conn.transaction():
                    await conn.execute("SELECT set_config('app.workspace_id', $1, true)", ws)
                    got = await conn.fetchval(count)
                if got != n:
                    problems.append(f"{key}[{ws}]: wf_app sees {got} rows, the owner saw {n}")
    except asyncpg.PostgresError as exc:
        problems.append(f"wf_app could not read the copy: {type(exc).__name__}: {exc}")
    finally:
        await conn.close()
    return problems


def _docker(container: str, *args: str, password: str) -> None:
    cmd = ["docker", "exec", "-e", f"PGPASSWORD={password}", container, *args]
    done = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
    if done.returncode:
        raise RuntimeError(f"{args[0]} failed: {done.stderr.strip()[:500]}")


def _took(report: dict[str, Any], step: str, started: float) -> None:
    report["seconds"][step] = round(time.perf_counter() - started, 2)


async def drill(opts: argparse.Namespace) -> dict[str, Any]:
    owner = urlsplit(opts.url)
    user, password = owner.username or "wf_owner", owner.password or ""
    source_url = with_database(opts.url, opts.source)
    scratch = f"{SCRATCH_PREFIX}_{int(time.time())}"
    scratch_url = with_database(opts.url, scratch)
    app_url = as_role(scratch_url, "wf_app", opts.app_password) if opts.app_password else ""
    report: dict[str, Any] = {"source": opts.source, "scratch": scratch, "seconds": {}, "problems": []}

    admin = await asyncpg.connect(with_database(opts.url, "postgres"))
    try:
        t0 = time.perf_counter()
        before = await snapshot(source_url)
        _took(report, "snapshot_source", t0)

        t0 = time.perf_counter()
        _docker(opts.container, "pg_dump", "-U", user, "-Fc", "-f", DUMP_PATH, opts.source, password=password)
        _took(report, "dump", t0)

        t0 = time.perf_counter()
        await admin.execute(f"CREATE DATABASE {ident(scratch)} OWNER {ident(user)}")
        _docker(
            opts.container, "pg_restore", "-U", user, "--exit-on-error", "-d", scratch, DUMP_PATH, password=password
        )  # fmt: skip
        _took(report, "restore", t0)

        t0 = time.perf_counter()
        report["problems"] += diff(before, await snapshot(scratch_url))
        if app_url:
            report["problems"] += await functional_check(app_url, before)
        _took(report, "verify", t0)

        if opts.migrate:
            from db.migrate import migrate  # noqa: PLC0415 - only the upgrade step needs it

            t0 = time.perf_counter()
            report["migrated"] = await migrate(scratch_url, verbose=False)
            upgraded = await snapshot(scratch_url)
            # An upgrade may add tables and change rows' shape: hold it to "nothing lost", not "identical".
            lost = diff(before, upgraded, content=False, security=False)
            ledger_grows = (
                "public.schema_migrations["  # each applied migration adds a row, which is the point
            )
            report["problems"] += [p for p in lost if "only in the copy" not in p and ledger_grows not in p]
            if app_url:
                report["problems"] += await functional_check(app_url, upgraded)
            _took(report, "migrate_and_reverify", t0)
    finally:
        if not opts.keep:
            await admin.execute(f"DROP DATABASE IF EXISTS {ident(scratch)} WITH (FORCE)")
            _docker(opts.container, "rm", "-f", DUMP_PATH, password=password)
        await admin.close()

    report["tables"] = len(before["tables"])
    report["rows"] = sum(n for scopes in before["data"].values() for n, _ in scopes.values())
    report["ok"] = not report["problems"]
    return report


async def compare(opts: argparse.Namespace) -> dict[str, Any]:
    source, target = await snapshot(opts.source_url), await snapshot(opts.target_url)
    problems = diff(source, target)
    if opts.app_url:
        problems += await functional_check(opts.app_url, source)
    return {"tables": len(source["tables"]), "problems": problems, "ok": not problems}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    d = sub.add_parser("drill", help="dump, restore into a scratch database, verify, optionally migrate")
    d.add_argument("--url", default=LOCAL_OWNER_URL, help="owner URL of the local server (default: compose)")
    d.add_argument("--source", default="watchfloor", help="database to back up")
    d.add_argument(
        "--container", default=CONTAINER, help="Postgres container that has pg_dump and pg_restore"
    )
    d.add_argument("--app-password", default="devpassword", help="wf_app's password; '' skips the role check")
    d.add_argument("--migrate", action="store_true", help="then apply the migrations the copy is missing")
    d.add_argument("--keep", action="store_true", help="keep the scratch database")

    c = sub.add_parser("compare", help="check a restored database against its original (read-only)")
    c.add_argument("--source-url", required=True)
    c.add_argument("--target-url", required=True)
    c.add_argument("--app-url", default="", help="wf_app's URL on the target, for the role check")

    opts = ap.parse_args(argv)
    report = asyncio.run(drill(opts) if opts.command == "drill" else compare(opts))
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
