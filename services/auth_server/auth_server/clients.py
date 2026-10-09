"""Pre-registered OAuth clients (no dynamic registration, DD §12.1) and the provisioning CLI.

    uv run python -m auth_server.clients --password '<demo password>' \
        --alexa-redirect-uri https://pitangui.amazon.com/api/skill/link/XXXX

Runs as the database owner: upserts the three clients and an Argon2id login
for every user in the workspace. Generated client secrets are printed once;
pass WF_ALEXA_CLIENT_SECRET / WF_DISPATCHER_CLIENT_SECRET to pin them instead.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
from dataclasses import dataclass, field

import asyncpg
from argon2 import PasswordHasher

ALEXA = "wf-alexa"
HARNESS = "wf-harness"
DISPATCHER = "wf-dispatcher"
USER_SCOPES = ("watchfloor.read", "watchfloor.write")

_hasher = PasswordHasher()


@dataclass(frozen=True)
class ClientSpec:
    client_id: str
    name: str
    grant_types: tuple[str, ...]
    auth_method: str  # client_secret_basic | client_secret_post | none
    scopes: tuple[str, ...]
    redirect_uris: tuple[str, ...] = field(default_factory=tuple)
    secret_env: str | None = None

    @property
    def confidential(self) -> bool:
        return self.auth_method != "none"


def default_clients(
    alexa_redirect_uris: tuple[str, ...] = (), harness_redirect_uris: tuple[str, ...] = ()
) -> list[ClientSpec]:
    return [
        # ⚠ S2: Alexa+'s token-endpoint auth method is unconfirmed; the token endpoint accepts basic or post.
        ClientSpec(
            ALEXA,
            "Alexa+",
            ("authorization_code", "refresh_token"),
            "client_secret_post",
            USER_SCOPES,
            alexa_redirect_uris,
            "WF_ALEXA_CLIENT_SECRET",
        ),
        ClientSpec(
            HARNESS,
            "Watchfloor harness",
            ("authorization_code", "refresh_token"),
            "none",
            USER_SCOPES,
            harness_redirect_uris or ("http://localhost:8082/oauth/callback",),
        ),
        ClientSpec(
            DISPATCHER,
            "Watchfloor dispatcher",
            ("client_credentials",),
            "client_secret_basic",
            ("watchfloor.agent.read",),
            (),
            "WF_DISPATCHER_CLIENT_SECRET",
        ),
    ]


async def upsert_client(conn: asyncpg.Connection, spec: ClientSpec, secret: str | None) -> str | None:
    """Insert or update a client. Returns a newly generated secret, if one had to be created."""
    existing = await conn.fetchval(
        "SELECT client_secret_hash FROM auth.clients WHERE client_id = $1", spec.client_id
    )
    generated = None
    if spec.confidential and secret is None and existing is None:
        secret = generated = secrets.token_urlsafe(32)
    secret_hash = _hasher.hash(secret) if secret else existing if spec.confidential else None
    await conn.execute(
        """INSERT INTO auth.clients (client_id, client_name, client_secret_hash, redirect_uris, grant_types,
                                     token_endpoint_auth_method, scopes)
           VALUES ($1, $2, $3, $4, $5, $6, $7)
           ON CONFLICT (client_id) DO UPDATE SET
             client_name = EXCLUDED.client_name, client_secret_hash = EXCLUDED.client_secret_hash,
             redirect_uris = EXCLUDED.redirect_uris, grant_types = EXCLUDED.grant_types,
             token_endpoint_auth_method = EXCLUDED.token_endpoint_auth_method, scopes = EXCLUDED.scopes""",
        spec.client_id,
        spec.name,
        secret_hash,
        list(spec.redirect_uris),
        list(spec.grant_types),
        spec.auth_method,
        list(spec.scopes),
    )
    return generated


async def provision_logins(conn: asyncpg.Connection, workspace_id: str, password: str) -> int:
    """One Argon2id login per user in the workspace, keyed by their email."""
    await conn.execute("SELECT set_config('app.workspace_id', $1, true)", workspace_id)
    users = await conn.fetch("SELECT id, email FROM users WHERE workspace_id = $1", workspace_id)
    password_hash = _hasher.hash(password)
    for u in users:
        await conn.execute(
            """INSERT INTO auth.credentials (email, user_id, workspace_id, password_hash)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (email) DO UPDATE SET user_id = EXCLUDED.user_id,
                 workspace_id = EXCLUDED.workspace_id, password_hash = EXCLUDED.password_hash""",
            u["email"].lower(),
            u["id"],
            workspace_id,
            password_hash,
        )
    return len(users)


async def set_login_password(conn: asyncpg.Connection, email: str, password: str) -> bool:
    """Give one existing login its own password (the canary's). False when there is no such login."""
    updated = await conn.fetchval(
        "UPDATE auth.credentials SET password_hash = $2 WHERE email = $1 RETURNING email",
        email.lower(),
        _hasher.hash(password),
    )
    return updated is not None


async def bootstrap(
    owner_url: str,
    *,
    workspace_id: str,
    password: str,
    alexa_redirect_uris: tuple[str, ...] = (),
    harness_redirect_uris: tuple[str, ...] = (),
    client_secrets: dict[str, str] | None = None,
    logins: dict[str, str] | None = None,
) -> dict[str, str]:
    """Provision clients and logins; returns any client secrets generated on this run.

    ``logins`` maps an email to its own password, overriding the shared one (the canary's
    login in AWS). An email with no seeded user raises ``LookupError`` and rolls back.
    """
    pinned = dict(client_secrets or {})
    generated: dict[str, str] = {}
    conn = await asyncpg.connect(owner_url)
    try:
        async with conn.transaction():
            for spec in default_clients(alexa_redirect_uris, harness_redirect_uris):
                secret = pinned.get(spec.client_id) or (
                    os.environ.get(spec.secret_env) if spec.secret_env else None
                )
                new = await upsert_client(conn, spec, secret or None)
                if new:
                    generated[spec.client_id] = new
            await provision_logins(conn, workspace_id, password)
            for email, own_password in (logins or {}).items():
                if not await set_login_password(conn, email, own_password):
                    raise LookupError(f"no login for {email} in {workspace_id}; seed the workspace first")
    finally:
        await conn.close()
    return generated


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--owner-url",
        default=os.environ.get(
            "WF_OWNER_DATABASE_URL", "postgresql://wf_owner:devpassword@127.0.0.1:55432/watchfloor"
        ),
    )
    ap.add_argument("--workspace", default="ws_lumenridge")
    ap.add_argument(
        "--password", default=os.environ.get("WF_DEMO_PASSWORD"), help="login password for every user"
    )
    ap.add_argument("--alexa-redirect-uri", action="append", default=[])
    ap.add_argument("--harness-redirect-uri", action="append", default=[])
    opts = ap.parse_args(argv)
    if not opts.password or len(opts.password) < 12:
        print("a --password (or WF_DEMO_PASSWORD) of at least 12 characters is required", file=sys.stderr)
        return 2
    generated = asyncio.run(
        bootstrap(
            opts.owner_url,
            workspace_id=opts.workspace,
            password=opts.password,
            alexa_redirect_uris=tuple(opts.alexa_redirect_uri),
            harness_redirect_uris=tuple(opts.harness_redirect_uri),
        )
    )
    print(f"provisioned clients {ALEXA}, {HARNESS}, {DISPATCHER} and logins for {opts.workspace}")
    for client_id, secret in generated.items():
        print(f"  new secret for {client_id} (shown once, store it now): {secret}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
