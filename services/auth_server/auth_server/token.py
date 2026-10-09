"""Token endpoint (DD §12.4): authorization_code, refresh_token and client_credentials.

Access tokens are ES256 JWTs (RFC 9068 ``at+jwt``) with ``aud`` set to the
canonical resource URI. Job tokens for deep-triage agents last five minutes and
carry ``ws`` and ``job``; the MCP server binds them to a live job in that
workspace, so the dispatcher is trusted here only to name the job it runs.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import re
import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import unquote_plus

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import Response

from .context import AGENT_SCOPE, JOB_TOKEN_TTL_S, AuthServer, client_ip, server
from .responses import oauth_error, token_json
from .storage import Client

TOKEN_LIMIT_PER_MIN = 120
_VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
_WS = re.compile(r"^ws_[0-9A-Za-z_]{1,40}$")
_JOB = re.compile(r"^job_[0-9A-Za-z]{1,40}$")
_hasher = PasswordHasher()


class ClientAuthError(Exception):
    def __init__(self, description: str, *, basic: bool) -> None:
        super().__init__(description)
        self.description = description
        self.basic = basic


def _field(form: FormData, name: str) -> str | None:
    value = form.get(name)
    return value if isinstance(value, str) and value != "" else None


async def _secret_ok(secret_hash: str, secret: str) -> bool:
    def check() -> bool:
        try:
            return _hasher.verify(secret_hash, secret)
        except (VerificationError, InvalidHashError):
            return False

    return await asyncio.to_thread(check)


async def authenticate_client(srv: AuthServer, request: Request, form: FormData) -> Client:
    """client_secret_basic, client_secret_post, or none (public + PKCE). Raises ClientAuthError."""
    header = request.headers.get("authorization", "")
    basic = header[:6].lower() == "basic "
    if basic:
        try:
            raw_id, _, raw_secret = base64.b64decode(header[6:], validate=True).decode().partition(":")
        except ValueError:
            raise ClientAuthError("Malformed Basic credentials.", basic=True) from None
        client_id, secret = unquote_plus(raw_id), unquote_plus(raw_secret)
        if _field(form, "client_id") not in (None, client_id):
            raise ClientAuthError("client_id does not match the credentials.", basic=True)
    else:
        client_id, secret = _field(form, "client_id"), _field(form, "client_secret")
    client = await srv.db().client(client_id) if client_id else None
    if client is None:
        raise ClientAuthError("Unknown client.", basic=basic)
    if client.confidential:
        if not secret or not await _secret_ok(client.secret_hash or "", secret):
            raise ClientAuthError("Client authentication failed.", basic=basic)
    elif secret:
        raise ClientAuthError("Public clients do not have a secret.", basic=basic)
    return client


async def mint_access(
    srv: AuthServer,
    *,
    sub: str,
    ws: str,
    scope: str,
    client_id: str,
    ttl_s: int,
    extra: dict[str, Any] | None = None,
) -> str:
    s = srv.settings
    now = srv.now_s()
    claims: dict[str, Any] = {
        "iss": s.issuer,
        "sub": sub,
        "aud": s.resource_uri,
        "ws": ws,
        "scope": scope,
        "client_id": client_id,
        "iat": now,
        "exp": now + ttl_s,
        "jti": secrets.token_urlsafe(12),
        **(extra or {}),
    }
    return await srv.keys().sign(claims)


async def _user_tokens(
    srv: AuthServer, client: Client, *, user_id: str, ws: str, scope: str, refresh_token: str | None
) -> Response:
    access = await mint_access(
        srv, sub=user_id, ws=ws, scope=scope, client_id=client.client_id, ttl_s=srv.settings.access_ttl_s
    )
    body: dict[str, Any] = {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": srv.settings.access_ttl_s,
        "scope": scope,
    }
    if refresh_token:
        body["refresh_token"] = refresh_token
    return token_json(body)


async def _authorization_code(srv: AuthServer, client: Client, form: FormData) -> Response:
    code, redirect_uri, verifier = (
        _field(form, "code"),
        _field(form, "redirect_uri"),
        _field(form, "code_verifier"),
    )
    if not code or not redirect_uri or not verifier:
        return oauth_error("invalid_request", "code, redirect_uri and code_verifier are required.")
    if not _VERIFIER.match(verifier):
        return oauth_error("invalid_grant", "Malformed code_verifier.")
    now = srv.clock.now()
    status, row = await srv.db().take_code(code, now)
    if status != "ok" or row is None:
        return oauth_error("invalid_grant", "The authorization code is invalid, expired or already used.")
    if row["client_id"] != client.client_id or row["redirect_uri"] != redirect_uri:
        return oauth_error("invalid_grant", "The code was issued to a different client or redirect URI.")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    if not hmac.compare_digest(challenge, row["code_challenge"]):
        return oauth_error("invalid_grant", "PKCE verification failed.")
    resource = _field(form, "resource")
    if resource is not None and resource != row["resource"]:
        return oauth_error("invalid_target", "resource does not match the authorization request.")

    refresh = None
    if "refresh_token" in client.grant_types:
        refresh = secrets.token_urlsafe(32)
        await srv.db().save_refresh(
            token=refresh,
            family_id=secrets.token_hex(16),
            client_id=client.client_id,
            user_id=row["user_id"],
            workspace_id=row["workspace_id"],
            scope=row["scope"],
            resource=row["resource"],
            expires_at=now + timedelta(days=srv.settings.refresh_ttl_days),
            at=now,
        )
    return await _user_tokens(
        srv, client, user_id=row["user_id"], ws=row["workspace_id"], scope=row["scope"], refresh_token=refresh
    )


async def _refresh_token(srv: AuthServer, client: Client, form: FormData) -> Response:
    presented = _field(form, "refresh_token")
    if not presented:
        return oauth_error("invalid_request", "refresh_token is required.")
    store = srv.db()
    current = await store.get_refresh(presented)
    if current is None or current["client_id"] != client.client_id:
        return oauth_error("invalid_grant", "The refresh token is invalid.")
    granted = frozenset(current["scope"].split())
    requested = frozenset((_field(form, "scope") or current["scope"]).split())
    if not requested or not requested <= granted:
        return oauth_error("invalid_scope", "A refresh can only narrow the original scope.")
    resource = _field(form, "resource")
    if resource is not None and resource != current["resource"]:
        return oauth_error("invalid_target", "resource does not match the original grant.")

    now = srv.clock.now()
    new_refresh = secrets.token_urlsafe(32)
    status, row = await store.rotate_refresh(
        presented,
        new_token=new_refresh,
        now=now,
        expires_at=now + timedelta(days=srv.settings.refresh_ttl_days),
    )
    if status != "ok" or row is None:
        return oauth_error("invalid_grant", "The refresh token is invalid, expired or was already used.")
    return await _user_tokens(
        srv,
        client,
        user_id=row["user_id"],
        ws=row["workspace_id"],
        scope=" ".join(sorted(requested)),
        refresh_token=new_refresh,
    )


async def _client_credentials(srv: AuthServer, client: Client, form: FormData) -> Response:
    if not client.confidential:
        return oauth_error("unauthorized_client", "client_credentials needs a confidential client.")
    requested = frozenset((_field(form, "scope") or " ".join(sorted(client.scopes))).split())
    if requested != {AGENT_SCOPE} or not requested <= client.scopes:
        return oauth_error("invalid_scope", f"Only {AGENT_SCOPE} is issued with client_credentials.")
    ws, job = _field(form, "ws"), _field(form, "job")
    if not ws or not _WS.match(ws) or not job or not _JOB.match(job):
        return oauth_error("invalid_request", "Job tokens need ws and job parameters.")
    access = await mint_access(
        srv,
        sub="svc:triage",
        ws=ws,
        scope=AGENT_SCOPE,
        client_id=client.client_id,
        ttl_s=JOB_TOKEN_TTL_S,
        extra={"job": job},
    )
    return token_json(
        {"access_token": access, "token_type": "Bearer", "expires_in": JOB_TOKEN_TTL_S, "scope": AGENT_SCOPE}
    )


GRANTS = {
    "authorization_code": _authorization_code,
    "refresh_token": _refresh_token,
    "client_credentials": _client_credentials,
}


async def token(request: Request) -> Response:
    srv = server(request)
    form = await request.form()
    try:
        client = await authenticate_client(srv, request, form)
    except ClientAuthError as exc:
        return oauth_error("invalid_client", exc.description, 401, basic_auth=exc.basic)
    if await srv.db().rate_hit(f"token:{client.client_id}:{client_ip(request)}") > TOKEN_LIMIT_PER_MIN:
        return oauth_error("slow_down", "Too many token requests. Try again in a minute.", 429)
    grant_type = _field(form, "grant_type")
    handler = GRANTS.get(grant_type or "")
    if handler is None:
        return oauth_error("unsupported_grant_type", "Unsupported grant_type.")
    if grant_type not in client.grant_types:
        return oauth_error("unauthorized_client", f"This client may not use {grant_type}.")
    return await handler(srv, client, form)
