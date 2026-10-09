"""The seeder Lambda's bootstrap path (``auth_server.seeder.run``): pinned secrets only, the canary's own login.

The seed path is ``db.seed``, which the ``seeded`` fixture already runs; re-seeding here would move
the shared test database off ``FROZEN_NOW``.
"""

from __future__ import annotations

import asyncpg
import pytest
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from auth_server.clients import ALEXA, DISPATCHER
from auth_server.seeder import run

from tests.conftest import OWNER_URL, WS
from tests.integration.test_auth_server import ALEXA_SECRET, DISPATCH_SECRET, PASSWORD

CANARY = "daniel@lumenridge.example"
CANARY_PASSWORD = "canary-own-password-0123456789"
SECRETS = {ALEXA: ALEXA_SECRET, DISPATCHER: DISPATCH_SECRET}


async def _fetch(query: str, *args: object) -> dict[str, str]:
    conn = await asyncpg.connect(OWNER_URL)
    try:
        return {r[0]: r[1] for r in await conn.fetch(query, *args)}
    finally:
        await conn.close()


async def test_bootstrap_registers_pinned_clients_and_the_canarys_own_login(seeded: str) -> None:
    result = await run(
        OWNER_URL,
        seed=False,
        do_bootstrap=True,
        workspace=seeded,
        demo_password=PASSWORD,
        client_secrets=SECRETS,
        canary=(CANARY, CANARY_PASSWORD),
    )
    assert result == {"workspace": WS, "logins": 4, "canary_login": True}

    ph = PasswordHasher()
    clients = await _fetch("SELECT client_id, client_secret_hash FROM auth.clients")
    assert ph.verify(clients[ALEXA], ALEXA_SECRET) and ph.verify(clients[DISPATCHER], DISPATCH_SECRET)
    logins = await _fetch("SELECT email, password_hash FROM auth.credentials")
    assert ph.verify(logins[CANARY], CANARY_PASSWORD)
    with pytest.raises(VerifyMismatchError):
        ph.verify(logins[CANARY], PASSWORD)
    assert ph.verify(logins["priya@lumenridge.example"], PASSWORD)

    # Leave every login on the shared password again, as the auth tests expect.
    await run(
        OWNER_URL,
        seed=False,
        do_bootstrap=True,
        workspace=seeded,
        demo_password=PASSWORD,
        client_secrets=SECRETS,
    )


async def test_bootstrap_without_users_registers_clients_and_skips_the_canary(seeded: str) -> None:
    result = await run(
        OWNER_URL,
        seed=False,
        do_bootstrap=True,
        workspace="ws_not_seeded",
        demo_password=PASSWORD,
        client_secrets=SECRETS,
        canary=(CANARY, CANARY_PASSWORD),
    )
    assert result == {"workspace": "ws_not_seeded", "logins": 0, "canary_login": False}


async def test_short_demo_password_is_refused_before_any_write(seeded: str) -> None:
    with pytest.raises(ValueError, match="12 characters"):
        await run(OWNER_URL, seed=False, do_bootstrap=True, workspace=seeded, demo_password="short")
