"""OAuth 2.1 authorization server end to end (DD §12): account linking, tokens, rotation, job tokens.

Every login comes from its own client IP and the flows spread across the four
seeded users, so the 5-per-minute login limits never interfere between tests.
The issued access tokens are used against the real MCP app, closing the loop.
"""

from __future__ import annotations

import base64
import hashlib
import html
import re
import secrets
from collections.abc import AsyncIterator
from itertools import count
from typing import Any
from urllib.parse import parse_qs, urlparse

import asyncpg
import httpx
import jwt
import pytest
from auth_server.app import create_app
from auth_server.clients import ALEXA, DISPATCHER, HARNESS, bootstrap
from auth_server.signer import FileSigner
from auth_server.storage import AuthStore

from tests.conftest import ISSUER, KID, OWNER_URL, PG_HOST, RESOURCE, TEST_DB, WS
from tests.mcp_client import Mcp

AUTH_URL = f"postgresql://wf_auth:devpassword@{PG_HOST}/{TEST_DB}"
PASSWORD = "correct horse battery staple"
ALEXA_SECRET = "alexa-test-secret-" + "x" * 20
DISPATCH_SECRET = "dispatcher-test-secret-" + "y" * 20
ALEXA_CB = "https://alexa.example/link/callback"
HARNESS_CB = "http://localhost:8082/oauth/callback"
_ips = count(1)


@pytest.fixture(scope="module")
async def emails(seeded: str) -> list[str]:
    await bootstrap(
        OWNER_URL,
        workspace_id=WS,
        password=PASSWORD,
        alexa_redirect_uris=(ALEXA_CB,),
        client_secrets={ALEXA: ALEXA_SECRET, DISPATCHER: DISPATCH_SECRET},
    )
    conn = await asyncpg.connect(OWNER_URL)
    try:
        await conn.execute("SELECT set_config('app.workspace_id', $1, false)", WS)
        rows = await conn.fetch("SELECT email FROM users WHERE workspace_id = $1 ORDER BY email", WS)
    finally:
        await conn.close()
    return [r["email"] for r in rows]


@pytest.fixture(scope="module")
async def auth_app(emails: list[str], signing_key: tuple[Any, dict[str, Any]]) -> AsyncIterator[Any]:
    store = await AuthStore.open(AUTH_URL)
    private, _ = signing_key
    yield create_app(store=store, signer=FileSigner(private, kid=KID))
    await store.close()


def _browser(auth_app: Any) -> httpx.AsyncClient:
    """A fresh 'browser' with its own cookie jar and client IP."""
    transport = httpx.ASGITransport(app=auth_app, client=(f"10.0.0.{next(_ips)}", 50000))
    return httpx.AsyncClient(transport=transport, base_url=ISSUER)


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _hidden(page: str) -> dict[str, str]:
    return {k: html.unescape(v) for k, v in re.findall(r'type="hidden" name="(\w+)" value="([^"]*)"', page)}


async def _link(
    auth_app: Any,
    email: str,
    *,
    client_id: str = HARNESS,
    redirect_uri: str = HARNESS_CB,
    decision: str = "allow",
    scope: str | None = None,
) -> tuple[dict[str, list[str]], str]:
    """Authorize → login → consent. Returns the redirect query and the PKCE verifier."""
    verifier, challenge = _pkce()
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "st-123",
        "resource": RESOURCE,
    }
    if scope:
        params["scope"] = scope
    async with _browser(auth_app) as browser:
        r = await browser.get("/authorize", params=params)
        assert r.status_code == 200, r.text
        assert r.headers["x-frame-options"] == "DENY"
        r = await browser.post("/login", data={**_hidden(r.text), "email": email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        assert "Allow" in r.text and "See your pipeline risk" in r.text
        r = await browser.post("/consent", data={**_hidden(r.text), "decision": decision})
    assert r.status_code == 303
    location = urlparse(r.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == redirect_uri
    return parse_qs(location.query), verifier


async def _token(auth_app: Any, data: dict[str, str], auth: tuple[str, str] | None = None) -> httpx.Response:
    async with _browser(auth_app) as c:
        return await c.post("/token", data=data, auth=auth)


async def test_metadata_and_jwks(auth_app: Any) -> None:
    async with _browser(auth_app) as c:
        meta = (await c.get("/.well-known/oauth-authorization-server")).json()
        keys = (await c.get("/jwks.json")).json()
    assert meta["issuer"] == ISSUER and meta["token_endpoint"] == f"{ISSUER}/token"
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert meta["authorization_response_iss_parameter_supported"] is True
    assert [k["kid"] for k in keys["keys"]] == [KID]


async def test_public_client_links_and_calls_mcp(
    auth_app: Any, emails: list[str], client: httpx.AsyncClient
) -> None:
    query, verifier = await _link(auth_app, emails[0])
    assert query["state"] == ["st-123"] and query["iss"] == [ISSUER]
    r = await _token(
        auth_app,
        {
            "grant_type": "authorization_code",
            "code": query["code"][0],
            "redirect_uri": HARNESS_CB,
            "code_verifier": verifier,
            "client_id": HARNESS,
            "resource": RESOURCE,
        },
    )
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["token_type"] == "Bearer" and body["refresh_token"]
    claims = jwt.decode(body["access_token"], options={"verify_signature": False})
    assert claims["aud"] == RESOURCE and claims["iss"] == ISSUER and claims["ws"] == WS
    assert claims["scope"] == "watchfloor.read watchfloor.write"

    tools = await Mcp(client, body["access_token"]).tools()
    assert len(tools) == 12, "a linked Alexa+/harness user sees the customer tool set"


async def test_code_is_single_use_and_pkce_bound(auth_app: Any, emails: list[str]) -> None:
    query, verifier = await _link(auth_app, emails[1])
    exchange = {
        "grant_type": "authorization_code",
        "code": query["code"][0],
        "redirect_uri": HARNESS_CB,
        "client_id": HARNESS,
    }
    wrong = await _token(auth_app, {**exchange, "code_verifier": _pkce()[0]})
    assert wrong.status_code == 400 and wrong.json()["error"] == "invalid_grant"
    # The failed attempt consumed the code: a correct verifier is now too late.
    late = await _token(auth_app, {**exchange, "code_verifier": verifier})
    assert late.json()["error"] == "invalid_grant"


async def test_confidential_client_must_authenticate(auth_app: Any, emails: list[str]) -> None:
    query, verifier = await _link(auth_app, emails[2], client_id=ALEXA, redirect_uri=ALEXA_CB)
    exchange = {
        "grant_type": "authorization_code",
        "code": query["code"][0],
        "redirect_uri": ALEXA_CB,
        "code_verifier": verifier,
    }
    bad = await _token(auth_app, exchange, auth=(ALEXA, "wrong-secret"))
    assert bad.status_code == 401 and bad.json()["error"] == "invalid_client"
    assert bad.headers["www-authenticate"].startswith("Basic")
    good = await _token(auth_app, exchange, auth=(ALEXA, ALEXA_SECRET))
    assert good.status_code == 200, good.text


async def test_refresh_rotates_and_replay_burns_the_family(auth_app: Any, emails: list[str]) -> None:
    query, verifier = await _link(auth_app, emails[3])
    first = (
        await _token(
            auth_app,
            {
                "grant_type": "authorization_code",
                "code": query["code"][0],
                "redirect_uri": HARNESS_CB,
                "code_verifier": verifier,
                "client_id": HARNESS,
            },
        )
    ).json()
    refresh = {"grant_type": "refresh_token", "client_id": HARNESS}
    second = await _token(auth_app, {**refresh, "refresh_token": first["refresh_token"]})
    assert second.status_code == 200 and second.json()["refresh_token"] != first["refresh_token"]

    narrowed = await _token(
        auth_app,
        {**refresh, "refresh_token": second.json()["refresh_token"], "scope": "watchfloor.read"},
    )
    assert narrowed.status_code == 200 and narrowed.json()["scope"] == "watchfloor.read"

    replay = await _token(auth_app, {**refresh, "refresh_token": first["refresh_token"]})
    assert replay.json()["error"] == "invalid_grant"
    latest = await _token(auth_app, {**refresh, "refresh_token": narrowed.json()["refresh_token"]})
    assert latest.json()["error"] == "invalid_grant", "reuse of a rotated token must revoke the whole family"


async def test_revocation_kills_the_refresh_token(auth_app: Any, emails: list[str]) -> None:
    query, verifier = await _link(auth_app, emails[0], client_id=ALEXA, redirect_uri=ALEXA_CB)
    tokens = (
        await _token(
            auth_app,
            {
                "grant_type": "authorization_code",
                "code": query["code"][0],
                "redirect_uri": ALEXA_CB,
                "code_verifier": verifier,
                "client_id": ALEXA,
                "client_secret": ALEXA_SECRET,
            },
        )
    ).json()
    async with _browser(auth_app) as c:
        r = await c.post("/revoke", data={"token": tokens["refresh_token"]}, auth=(ALEXA, ALEXA_SECRET))
    assert r.status_code == 200
    after = await _token(
        auth_app,
        {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]},
        auth=(ALEXA, ALEXA_SECRET),
    )
    assert after.json()["error"] == "invalid_grant"


async def test_dispatcher_gets_short_lived_job_tokens(auth_app: Any, client: httpx.AsyncClient) -> None:
    job = {
        "grant_type": "client_credentials",
        "scope": "watchfloor.agent.read",
        "ws": WS,
        "job": "job_TEST01",
    }
    r = await _token(auth_app, job, auth=(DISPATCHER, DISPATCH_SECRET))
    assert r.status_code == 200, r.text
    assert "refresh_token" not in r.json()
    claims = jwt.decode(r.json()["access_token"], options={"verify_signature": False})
    assert claims["sub"] == "svc:triage" and claims["job"] == "job_TEST01" and claims["ws"] == WS
    assert claims["exp"] - claims["iat"] == 300
    assert {t["name"] for t in await Mcp(client, r.json()["access_token"]).tools()} >= {"get_playbook"}

    missing = await _token(auth_app, {**job, "job": ""}, auth=(DISPATCHER, DISPATCH_SECRET))
    assert missing.json()["error"] == "invalid_request"
    user_scope = await _token(
        auth_app, {**job, "scope": "watchfloor.write"}, auth=(DISPATCHER, DISPATCH_SECRET)
    )
    assert user_scope.json()["error"] == "invalid_scope"
    public = await _token(auth_app, {**job, "client_id": HARNESS})
    assert public.json()["error"] == "unauthorized_client"


async def test_unregistered_redirect_never_redirects(auth_app: Any) -> None:
    _, challenge = _pkce()
    async with _browser(auth_app) as c:
        r = await c.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": HARNESS,
                "redirect_uri": "https://evil.example/steal",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
    assert r.status_code == 400 and "location" not in r.headers


async def test_missing_pkce_redirects_with_error(auth_app: Any) -> None:
    async with _browser(auth_app) as c:
        r = await c.get(
            "/authorize",
            params={"response_type": "code", "client_id": HARNESS, "redirect_uri": HARNESS_CB, "state": "s1"},
        )
    assert r.status_code == 303
    query = parse_qs(urlparse(r.headers["location"]).query)
    assert query["error"] == ["invalid_request"] and query["state"] == ["s1"] and query["iss"] == [ISSUER]


async def test_denied_consent_returns_access_denied(auth_app: Any, emails: list[str]) -> None:
    query, _ = await _link(auth_app, emails[1], decision="deny")
    assert query["error"] == ["access_denied"] and "code" not in query


async def test_login_needs_csrf_and_is_rate_limited(auth_app: Any) -> None:
    _, challenge = _pkce()
    params = {
        "response_type": "code",
        "client_id": HARNESS,
        "redirect_uri": HARNESS_CB,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    async with _browser(auth_app) as browser:
        page = await browser.get("/authorize", params=params)
        form = {**_hidden(page.text), "email": "nobody@example.com", "password": "nope"}
        statuses = [(await browser.post("/login", data=form)).status_code for _ in range(6)]
    assert statuses[:5] == [401] * 5 and statuses[5] == 429

    async with _browser(auth_app) as cookieless:
        r = await cookieless.post("/login", data=form)
    assert r.status_code == 403
