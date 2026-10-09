"""Seed and bootstrap a deployed environment from inside the VPC (TD Appendix A.1, ``make seed-remote``).

The AWS twin of ``make seed`` + ``make auth-bootstrap``. Event (both steps default to on):

    {"seed": true, "bootstrap": true}

- ``seed``: resets the demo workspace relative to now with ``db.seed`` and asserts the
  scenario scores. dev and demo hold synthetic data only, so a reset loses nothing real.
- ``bootstrap``: registers the three OAuth clients and an Argon2id login for every user.
  Every secret comes from Secrets Manager, generated there by the Auth stack, and none
  is returned: the Alexa+ client secret, the dispatcher's, the shared demo password, and
  the canary's own password for its email.

Without a seeded workspace the bootstrap still registers the clients, and skips the
logins (there are no users yet). The result names what ran, never a secret.

A ``demo`` event runs one demo operation instead (``db.seed.ops``, for ``scripts/demo.py --env
dev|demo``) and reads no login secret:

    {"demo": "status"} · {"demo": "clock", "offset": "+14h"} · {"demo": "outbox-retry", "ids": [42]}

Packaging: the auth_server Lambda zip carries the repository's ``db/`` package.
"""

from __future__ import annotations

import asyncio
import json
import os
from functools import lru_cache
from typing import Any

import asyncpg
from watchfloor_core.clock import Clock
from watchfloor_core.db import dsn_from_secret

from .clients import ALEXA, DISPATCHER, bootstrap

WORKSPACE = "ws_lumenridge"


@lru_cache(maxsize=8)
def _secret(arn: str) -> dict[str, Any]:
    import boto3  # lazy: only in AWS

    return json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"])


def _uris(name: str) -> tuple[str, ...]:
    return tuple(u.strip() for u in os.environ.get(name, "").split(",") if u.strip())


async def _user_count(dsn: str, workspace: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.workspace_id', $1, true)", workspace)
            return await conn.fetchval("SELECT count(*) FROM users WHERE workspace_id = $1", workspace)
    finally:
        await conn.close()


async def run(
    dsn: str,
    *,
    seed: bool,
    do_bootstrap: bool,
    workspace: str = WORKSPACE,
    demo_password: str = "",
    client_secrets: dict[str, str] | None = None,
    canary: tuple[str, str] | None = None,
    alexa_redirect_uris: tuple[str, ...] = (),
    harness_redirect_uris: tuple[str, ...] = (),
) -> dict[str, Any]:
    result: dict[str, Any] = {"workspace": workspace}
    if seed:
        from db.seed.__main__ import seed as seed_workspace  # bundled beside this package

        result["bands"] = await seed_workspace(
            dsn, workspace=workspace, seed_value=42, now=Clock().now(), verbose=False
        )
    if do_bootstrap:
        if len(demo_password) < 12:
            raise ValueError("the demo login password must be at least 12 characters")
        users = await _user_count(dsn, workspace)
        logins = {canary[0]: canary[1]} if canary and users else {}
        generated = await bootstrap(
            dsn,
            workspace_id=workspace,
            password=demo_password,
            alexa_redirect_uris=alexa_redirect_uris,
            harness_redirect_uris=harness_redirect_uris,
            client_secrets=client_secrets,
            logins=logins,
        )
        if generated:  # every confidential client has a pinned secret here, so this is a wiring bug
            raise RuntimeError(f"client secrets were generated instead of read: {sorted(generated)}")
        result["logins"] = users
        result["canary_login"] = bool(logins)
    return result


def handler(event: dict[str, Any] | None, _ctx: object) -> dict[str, Any]:
    event = event or {}
    workspace = os.environ.get("WF_WORKSPACE_ID", WORKSPACE)
    if "demo" in event:  # `make demo-status|demo-clock ENV=…`: no reset, no login secret read
        from db.seed import ops

        return asyncio.run(
            ops.run_op(dsn_from_secret(os.environ["WF_OWNER_DB_SECRET_ARN"]), workspace, event)
        )
    canary = _secret(os.environ["WF_CANARY_SECRET_ARN"])
    return asyncio.run(
        run(
            dsn_from_secret(os.environ["WF_OWNER_DB_SECRET_ARN"]),
            seed=bool(event.get("seed", True)),
            do_bootstrap=bool(event.get("bootstrap", True)),
            workspace=workspace,
            demo_password=_secret(os.environ["WF_DEMO_LOGIN_SECRET_ARN"])["password"],
            client_secrets={
                ALEXA: _secret(os.environ["WF_ALEXA_CLIENT_SECRET_ARN"])["client_secret"],
                DISPATCHER: _secret(os.environ["WF_DISPATCHER_CLIENT_SECRET_ARN"])["client_secret"],
            },
            canary=(canary["email"], canary["password"]),
            alexa_redirect_uris=_uris("WF_ALEXA_REDIRECT_URIS"),
            harness_redirect_uris=_uris("WF_HARNESS_REDIRECT_URI"),
        )
    )
