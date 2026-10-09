"""The migrator turns on logins for the NOLOGIN roles with passwords from Secrets Manager (TD §9.1)."""

from __future__ import annotations

import secrets

import asyncpg
import pytest
from workers.migrator import set_role_passwords

from tests.conftest import OWNER_URL, PG_HOST, TEST_DB


async def test_role_passwords_enable_logins_and_quote_safely(seeded: str) -> None:
    password = "it's-" + secrets.token_urlsafe(12)  # a quote, to prove %L quoting
    try:
        assert await set_role_passwords(OWNER_URL, {"wf_app": password}) == ["wf_app"]
        conn = await asyncpg.connect(f"postgresql://wf_app@{PG_HOST}/{TEST_DB}", password=password, timeout=5)
        try:
            assert await conn.fetchval("SELECT current_user") == "wf_app"
        finally:
            await conn.close()
        with pytest.raises(ValueError, match="unexpected role"):
            await set_role_passwords(OWNER_URL, {"wf_owner": "nope-nope-nope"})
    finally:
        await set_role_passwords(OWNER_URL, {"wf_app": "devpassword"})
