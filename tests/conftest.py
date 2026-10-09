"""Shared fixtures (TD §15.2).

- ``app`` / ``client``: the real Starlette app in-process (httpx ASGI transport,
  ``Host: localhost``), with a static test JWKS. No database is needed for the
  transport and edge conformance cases.
- ``seeded``: a throwaway ``watchfloor_test`` database on the local compose
  Postgres, migrated and seeded (seed 42) relative to ``FROZEN_NOW``; the
  service clock is frozen at the same instant so scores are deterministic.
  Tests that need it are skipped, visibly, when Postgres is not reachable.
- ``token_factory``: mints ES256 JWTs for any user, scopes, audience or expiry.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import asyncpg
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

PG_HOST = os.environ.get("WF_TEST_PG", "127.0.0.1:55432")
TEST_DB = "watchfloor_test"
ADMIN_URL = f"postgresql://wf_owner:devpassword@{PG_HOST}/postgres"
OWNER_URL = f"postgresql://wf_owner:devpassword@{PG_HOST}/{TEST_DB}"
APP_URL = f"postgresql://wf_app:devpassword@{PG_HOST}/{TEST_DB}"

RESOURCE = "http://localhost:8080/mcp"
ISSUER = "http://localhost:8081"
KID = "wf-test-1"
FROZEN_NOW = datetime(2026, 10, 21, 16, 0, tzinfo=UTC)  # 09:00 in Los Angeles on demo day

os.environ.update(
    {
        "WF_ENV": "test",
        "WF_DATABASE_URL": APP_URL,
        "WF_RESOURCE_URI": RESOURCE,
        "WF_ISSUER": ISSUER,
        "WF_ALLOWED_HOSTS": "localhost,127.0.0.1",
        "WF_ALLOWED_ORIGINS": "https://alexa.amazon.com",
        "WF_RATE_LIMIT_USER": "100000",
        "WF_RATE_LIMIT_WS": "100000",
    }
)
os.environ.pop("WF_OUTBOX_QUEUE_URL", None)

from watchfloor_core import db, queue  # noqa: E402
from watchfloor_core.clock import FixedClock  # noqa: E402
from watchfloor_core.services.context import set_clock  # noqa: E402
from watchfloor_core.settings import reset_settings  # noqa: E402

from tests.mcp_client import Mcp  # noqa: E402

reset_settings()

WS = "ws_lumenridge"
PRIYA = "us_01J9PRIYA000000000000000"
MAYA = "us_01J9MAYA0000000000000000"
KESTREL = "dl_01J9KESTREL0000000000000"
HARBOR = "dl_01J9HARBORPINE00000000000"
USER_SCOPE = "watchfloor.read watchfloor.write"
AGENT_SCOPE = "watchfloor.agent.read"


# ---------------------------------------------------------------- keys and tokens


@pytest.fixture(scope="session")
def signing_key() -> tuple[ec.EllipticCurvePrivateKey, dict[str, Any]]:
    key = ec.generate_private_key(ec.SECP256R1())
    jwk = jwt.algorithms.ECAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk.update({"kid": KID, "alg": "ES256", "use": "sig"})
    return key, {"keys": [jwk]}


TokenFactory = Callable[..., str]


@pytest.fixture(scope="session")
def token_factory(signing_key: tuple[ec.EllipticCurvePrivateKey, dict[str, Any]]) -> TokenFactory:
    private, _ = signing_key
    serial = iter(range(1, 1_000_000))

    def mint(
        *,
        sub: str = PRIYA,
        ws: str = WS,
        scope: str = USER_SCOPE,
        job: str | None = None,
        aud: str = RESOURCE,
        iss: str = ISSUER,
        expires_in: int = 300,
        kid: str = KID,
        key: Any = None,
    ) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": iss,
            "sub": sub,
            "aud": aud,
            "ws": ws,
            "scope": scope,
            "iat": now,
            "exp": now + expires_in,
            "jti": f"test-{next(serial)}",
        }
        if job:
            claims["job"] = job
        return jwt.encode(claims, key or private, algorithm="ES256", headers={"kid": kid})

    return mint


@pytest.fixture(scope="session")
def user_token(token_factory: TokenFactory) -> str:
    return token_factory()


# ---------------------------------------------------------------- app and client


async def _hold_lifespan(app: Any, started: asyncio.Event, stop: asyncio.Event) -> None:
    # Enter and exit the lifespan (and the SDK's task group) in one task.
    async with app.router.lifespan_context(app):
        started.set()
        await stop.wait()


@pytest.fixture(scope="session")
async def app(signing_key: tuple[Any, dict[str, Any]]) -> AsyncIterator[Any]:
    from mcp_server.app import create_app
    from mcp_server.middleware import JwksCache

    _, jwks = signing_key
    application = create_app(jwks=JwksCache(static=jwks), manage_pool=False)
    started, stop = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(_hold_lifespan(application, started, stop))
    await asyncio.wait_for(started.wait(), 10)
    yield application
    stop.set()
    await task


@pytest.fixture
async def client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


@pytest.fixture
def mcp(client: httpx.AsyncClient, user_token: str) -> Mcp:
    return Mcp(client, user_token)


# ---------------------------------------------------------------- database


async def _create_test_db() -> None:
    from db.migrate import migrate
    from db.seed.__main__ import seed

    admin = await asyncpg.connect(ADMIN_URL, timeout=3)
    try:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
            TEST_DB,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}"')
        await admin.execute(f'CREATE DATABASE "{TEST_DB}" OWNER wf_owner')
    finally:
        await admin.close()
    await migrate(OWNER_URL, dev_passwords=True, verbose=False)
    await seed(OWNER_URL, workspace=WS, seed_value=42, now=FROZEN_NOW, verbose=False)


@pytest.fixture(scope="session")
async def seeded(app: Any) -> AsyncIterator[str]:
    """Migrated, seeded test DB with the pool open and the clock frozen. Yields the workspace id."""
    try:
        await _create_test_db()
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"Postgres not reachable at {PG_HOST}: {exc}")
    set_clock(FixedClock(FROZEN_NOW))
    await db.open_pool(dsn=APP_URL, min_size=1, max_size=4)
    yield WS
    await db.close_pool()
    set_clock(None)


@pytest.fixture
def sqs_stub() -> Iterator[list[int]]:
    """Records post-commit outbox hand-offs instead of calling SQS."""
    sent: list[int] = []

    async def record(workspace_id: str, ids: list[int]) -> None:
        assert workspace_id.startswith("ws_")
        sent.extend(ids)

    queue.set_publisher(record)
    yield sent
    queue.set_publisher(None)
