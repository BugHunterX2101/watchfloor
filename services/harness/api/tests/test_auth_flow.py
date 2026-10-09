"""Sign-in as the public PKCE client, the callback's checks, and sign-out."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
from conftest import ISSUER, MCP_URL, REDIRECT, FakeAuth, sign_in
from harness_api import pkce


async def test_login_redirects_to_authorize_with_pkce_and_resource(client: httpx.AsyncClient) -> None:
    r = await client.get("/oauth/login")
    assert r.status_code == 302
    url = urlparse(r.headers["location"])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert f"{url.scheme}://{url.netloc}{url.path}" == f"{ISSUER}/authorize"
    assert q["client_id"] == "wf-harness" and q["redirect_uri"] == REDIRECT
    assert q["code_challenge_method"] == "S256" and len(q["code_challenge"]) == 43
    assert q["resource"] == MCP_URL and q["scope"] == "watchfloor.read watchfloor.write"
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("wf_h_pkce=") and "HttpOnly" in cookie and "SameSite=lax" in cookie


async def test_callback_exchanges_the_code_with_the_matching_verifier(
    client: httpx.AsyncClient, auth: FakeAuth
) -> None:
    r = await client.get("/oauth/login")
    q = parse_qs(urlparse(r.headers["location"]).query)
    r = await client.get("/oauth/callback", params={"code": "c1", "state": q["state"][0], "iss": ISSUER})
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "wf_h=" in r.headers["set-cookie"]
    assert auth.tokens["grant_type"] == "authorization_code" and auth.tokens["code"] == "c1"
    assert auth.tokens["client_id"] == "wf-harness" and "client_secret" not in auth.tokens
    assert auth.tokens["resource"] == MCP_URL and auth.tokens["redirect_uri"] == REDIRECT
    assert pkce.challenge(auth.tokens["code_verifier"]) == q["code_challenge"][0]
    assert (await client.get("/api/session")).json() == {"signed_in": True, "engine": "offline"}
    await sign_in(client)  # the shared helper takes the same path


async def test_callback_refuses_forged_state_and_mixed_up_issuers(
    client: httpx.AsyncClient, auth: FakeAuth
) -> None:
    await client.get("/oauth/login")
    bad = await client.get("/oauth/callback", params={"code": "c1", "state": "forged"})
    assert bad.headers["location"] == "/?signin=state_mismatch"

    r = await client.get("/oauth/login")  # the failed callback cleared the attempt: start a new one
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    mixed = await client.get(
        "/oauth/callback", params={"code": "c1", "state": state, "iss": "http://evil.test"}
    )
    assert mixed.headers["location"] == "/?signin=issuer_mismatch"

    denied = await client.get("/oauth/callback", params={"error": "access_denied"})
    assert denied.headers["location"] == "/?signin=access_denied"
    assert auth.tokens == {}  # no code was ever exchanged
    assert (await client.get("/api/session")).json()["signed_in"] is False


async def test_a_refused_exchange_leaves_the_user_signed_out(
    client: httpx.AsyncClient, auth: FakeAuth
) -> None:
    auth.refusals.add("authorization_code")
    r = await client.get("/oauth/login")
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = await client.get("/oauth/callback", params={"code": "c1", "state": state})
    assert r.headers["location"] == "/?signin=token_error"
    assert (await client.get("/api/session")).json()["signed_in"] is False


async def test_logout_revokes_the_refresh_token(client: httpx.AsyncClient, auth: FakeAuth) -> None:
    await sign_in(client)
    r = await client.post("/api/logout")
    assert r.json() == {"signed_in": False} and auth.revoked == ["rt1"]
    assert (await client.get("/api/session")).json()["signed_in"] is False


async def test_tampered_cookies_are_ignored(client: httpx.AsyncClient) -> None:
    client.cookies.set("wf_h", "not-a-real-session")
    assert (await client.get("/api/session")).json()["signed_in"] is False
    assert (await client.post("/converse", json={"message": "hi"})).status_code == 401
