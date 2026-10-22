"""Render boot step (deploy/render/start.sh): apply the migrations, then give wf_app and wf_auth logins.

Locally ``db.migrate --dev-passwords`` sets a fixed password; here each role's comes from its own
environment variable (WF_APP_DB_PASSWORD, WF_AUTH_DB_PASSWORD), set once on the Render service.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the repo root, for db.migrate

from db.migrate import migrate  # noqa: E402

ROLES = {"wf_app": "WF_APP_DB_PASSWORD", "wf_auth": "WF_AUTH_DB_PASSWORD"}


async def main() -> None:
    url = os.environ["WF_OWNER_DATABASE_URL"]
    await migrate(url)
    conn = await asyncpg.connect(url)
    try:
        for role, env in ROLES.items():
            password = os.environ[env]
            # ALTER ROLE takes no bind parameters, so only URL-safe characters are let through.
            if not re.fullmatch(r"[A-Za-z0-9_-]{24,}", password):
                raise SystemExit(f"{env} must be 24 or more URL-safe characters")
            await conn.execute(f"ALTER ROLE {role} LOGIN PASSWORD '{password}'")
    finally:
        await conn.close()
    print("migrations applied; wf_app and wf_auth can log in")


if __name__ == "__main__":
    asyncio.run(main())
