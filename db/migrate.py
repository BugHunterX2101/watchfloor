"""Apply ``db/migrations/*.sql`` in order (TD §9.1).

Forward-only: each file runs once in its own transaction and is recorded with a
SHA-256 checksum. Editing an applied file is an error.

    python -m db.migrate                       # owner URL from WF_OWNER_DATABASE_URL
    python -m db.migrate --dev-passwords       # also enable local logins for wf_app / wf_auth
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
DEFAULT_OWNER_URL = "postgresql://wf_owner:devpassword@127.0.0.1:55432/watchfloor"


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))


async def migrate(url: str, *, dev_passwords: bool = False, verbose: bool = True) -> list[str]:
    conn = await asyncpg.connect(url)
    applied_now: list[str] = []
    try:
        await conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                 version text PRIMARY KEY, checksum text NOT NULL,
                 applied_at timestamptz NOT NULL DEFAULT now())"""
        )
        done = {
            r["version"]: r["checksum"]
            for r in await conn.fetch("SELECT version, checksum FROM schema_migrations")
        }
        for path in migration_files():
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            version = path.stem
            if version in done:
                if done[version] != checksum:
                    raise RuntimeError(f"applied migration {version} was edited; add a new migration instead")
                continue
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)", version, checksum
                )
            applied_now.append(version)
            if verbose:
                print(f"applied {version}")
        if dev_passwords:
            # Local development only: never used in AWS, where credentials live in Secrets Manager.
            for role in ("wf_app", "wf_auth", "wf_readonly"):
                await conn.execute(f"ALTER ROLE {role} LOGIN PASSWORD 'devpassword'")
            if verbose:
                print("enabled local logins for wf_app, wf_auth, wf_readonly")
    finally:
        await conn.close()
    return applied_now


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=os.environ.get("WF_OWNER_DATABASE_URL", DEFAULT_OWNER_URL))
    ap.add_argument("--dev-passwords", action="store_true")
    args = ap.parse_args(argv)
    applied = asyncio.run(migrate(args.url, dev_passwords=args.dev_passwords))
    if not applied:
        print("schema up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
