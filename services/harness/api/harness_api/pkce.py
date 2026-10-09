"""Public-client OAuth 2.1 with PKCE S256 against the Watchfloor authorization server.

The same account-linking path Alexa+ takes (DD §12, §17): ``/authorize`` with a
``resource`` indicator, a browser login and consent, then ``/token``. The harness
holds no client secret.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

import httpx
from watchfloor_core.settings import Settings

from .session import Session

USER_SCOPE = "watchfloor.read watchfloor.write"
TIMEOUT_S = 10.0


class OAuthError(Exception):
    """The authorization server refused, or answered with something unusable."""


def new_attempt() -> tuple[str, str]:
    """A fresh (verifier, state) pair."""
    return secrets.token_urlsafe(48), secrets.token_urlsafe(16)


def challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def authorize_url(s: Settings, verifier: str, state: str) -> str:
    query = {
        "response_type": "code",
        "client_id": s.harness_client_id,
        "redirect_uri": s.harness_redirect_uri,
        "code_challenge": challenge(verifier),
        "code_challenge_method": "S256",
        "state": state,
        "resource": s.resource_uri,
        "scope": USER_SCOPE,
    }
    return f"{s.issuer}/authorize?{urlencode(query)}"


def _session(body: dict, previous_refresh: str | None = None) -> Session:
    if not isinstance(body.get("access_token"), str):
        raise OAuthError("token response has no access_token")
    return Session(
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token") or previous_refresh,
        expires_at=time.time() + int(body.get("expires_in", 300)),
    )


async def _token(s: Settings, data: dict[str, str], transport: httpx.AsyncBaseTransport | None) -> dict:
    async with httpx.AsyncClient(base_url=s.issuer, transport=transport, timeout=TIMEOUT_S) as auth:
        try:
            r = await auth.post("/token", data={**data, "client_id": s.harness_client_id})
        except httpx.HTTPError as exc:
            raise OAuthError(f"authorization server unreachable ({type(exc).__name__})") from None
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code != 200:
        raise OAuthError(str(body.get("error", f"HTTP {r.status_code}")))
    return body


async def exchange(
    s: Settings, code: str, verifier: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> Session:
    body = await _token(
        s,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": s.harness_redirect_uri,
            "code_verifier": verifier,
            "resource": s.resource_uri,
        },
        transport,
    )
    return _session(body)


async def refresh(
    s: Settings, refresh_token: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> Session:
    body = await _token(
        s,
        {"grant_type": "refresh_token", "refresh_token": refresh_token, "resource": s.resource_uri},
        transport,
    )
    return _session(body, previous_refresh=refresh_token)


async def revoke(
    s: Settings, refresh_token: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> None:
    """Best effort: signing out must work even when the authorization server does not answer."""
    try:
        async with httpx.AsyncClient(base_url=s.issuer, transport=transport, timeout=TIMEOUT_S) as auth:
            await auth.post(
                "/revoke",
                data={
                    "token": refresh_token,
                    "token_type_hint": "refresh_token",
                    "client_id": s.harness_client_id,
                },
            )
    except httpx.HTTPError:
        pass
