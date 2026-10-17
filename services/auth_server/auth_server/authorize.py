"""Authorization endpoint with login and consent (DD §12.2, §12.5).

GET /authorize validates the request and shows the login form. POST /login
checks the password (rate-limited) and shows consent with a signed ticket.
POST /consent issues a 60-second, single-use code bound to the client, the
redirect URI, the PKCE challenge and the resource, then 303s back.

A double-submit cookie protects both forms against CSRF. If the client or
redirect URI is not registered we show an error page instead of redirecting.
"""

from __future__ import annotations

import asyncio
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from starlette.datastructures import FormData, QueryParams
from starlette.requests import Request
from starlette.responses import Response

from .context import CODE_TTL_S, SCOPE_TEXT, TICKET_TTL_S, USER_SCOPES, AuthServer, client_ip, server
from .responses import error_page, page, redirect_to_client
from .storage import Client

CSRF_COOKIE = "wf_csrf"
LOGIN_LIMIT_PER_MIN = 5
_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_hasher = PasswordHasher()


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return _hasher.hash(secrets.token_urlsafe(16))


@dataclass(frozen=True)
class AuthRequest:
    client: Client
    redirect_uri: str
    state: str | None
    scope: str
    code_challenge: str
    resource: str

    def hidden(self) -> dict[str, str]:
        fields = {
            "response_type": "code",
            "client_id": self.client.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.scope,
            "code_challenge": self.code_challenge,
            "code_challenge_method": "S256",
            "resource": self.resource,
        }
        if self.state is not None:
            fields["state"] = self.state
        return fields


async def validate(
    srv: AuthServer, request: Request, params: QueryParams | FormData
) -> AuthRequest | Response:
    """Returns the validated request, or the response to send (an error page or an error redirect)."""

    def get(name: str) -> str | None:
        value = params.get(name)
        return value if isinstance(value, str) and value != "" else None

    client_id, redirect_uri = get("client_id"), get("redirect_uri")
    client = await srv.db().client(client_id) if client_id else None
    if client is None or "authorization_code" not in client.grant_types:
        return error_page(request, "This app isn't registered with Watchfloor.")
    if redirect_uri is None or redirect_uri not in client.redirect_uris:
        return error_page(request, "The return address for this app doesn't match its registration.")

    state = get("state")

    def fail(error: str, description: str) -> Response:
        return redirect_to_client(
            redirect_uri,
            {"error": error, "error_description": description, "state": state, "iss": srv.settings.issuer},
        )

    if get("response_type") != "code":
        return fail("unsupported_response_type", "Only response_type=code is supported.")
    challenge = get("code_challenge")
    if get("code_challenge_method") != "S256" or challenge is None or not _CHALLENGE.fullmatch(challenge):
        return fail("invalid_request", "PKCE with code_challenge_method=S256 is required.")
    resource = get("resource") or srv.settings.resource_uri
    if resource != srv.settings.resource_uri:
        return fail("invalid_target", "Unknown resource.")
    allowed = client.scopes & USER_SCOPES
    requested = frozenset((get("scope") or " ".join(sorted(allowed))).split())
    if not requested or not requested <= allowed:
        return fail("invalid_scope", "Requested scope is not available to this app.")
    return AuthRequest(
        client=client,
        redirect_uri=redirect_uri,
        state=state,
        scope=" ".join(sorted(requested)),
        code_challenge=challenge,
        resource=resource,
    )


def _csrf_ok(request: Request, form: FormData) -> str | None:
    cookie = request.cookies.get(CSRF_COOKIE, "")
    sent = form.get("csrf")
    if cookie and isinstance(sent, str) and hmac.compare_digest(cookie, sent):
        return cookie
    return None


def _login_page(
    request: Request, req: AuthRequest, csrf: str, error: str | None = None, status: int = 200
) -> Response:
    return page(
        request,
        "login.html",
        {"client_name": req.client.name, "hidden": req.hidden(), "csrf": csrf, "error": error},
        status,
    )


async def authorize(request: Request) -> Response:
    srv = server(request)
    req = await validate(srv, request, request.query_params)
    if isinstance(req, Response):
        return req
    csrf = secrets.token_urlsafe(24)
    response = _login_page(request, req, csrf)
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=TICKET_TTL_S * 2,
        httponly=True,
        secure=srv.settings.issuer.startswith("https://"),
        samesite="lax",
        path="/",
    )
    return response


async def _verify_password(password_hash: str, password: str) -> bool:
    def check() -> bool:
        try:
            return _hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    return await asyncio.to_thread(check)


async def login(request: Request) -> Response:
    srv = server(request)
    form = await request.form()
    csrf = _csrf_ok(request, form)
    if csrf is None:
        return error_page(request, "Your sign-in page expired. Go back to the app and try again.", 403)
    req = await validate(srv, request, form)
    if isinstance(req, Response):
        return req

    email = str(form.get("email") or "").strip().lower()[:254]  # RFC 5321 limit; it is also a rate-limit key
    password = str(form.get("password") or "")
    store = srv.db()
    over_ip = await store.rate_hit(f"login-ip:{client_ip(request)}") > LOGIN_LIMIT_PER_MIN
    over_account = bool(email) and await store.rate_hit(f"login-acct:{email}") > LOGIN_LIMIT_PER_MIN
    if over_ip or over_account:
        return _login_page(request, req, csrf, "Too many sign-in attempts. Wait a minute and try again.", 429)

    cred = await store.credential(email) if email else None
    # Verify against a dummy hash for unknown emails so timing doesn't reveal which accounts exist.
    ok = await _verify_password(cred["password_hash"] if cred else _dummy_hash(), password)
    if cred is None or not ok:
        return _login_page(request, req, csrf, "Email or password is incorrect.", 401)

    ticket = srv.tickets.issue(
        {"u": cred["user_id"], "ws": cred["workspace_id"], "csrf": csrf, "req": req.hidden()},
        now=srv.now_s(),
        ttl_s=TICKET_TTL_S,
    )
    return page(
        request,
        "consent.html",
        {
            "client_name": req.client.name,
            "scopes": [SCOPE_TEXT.get(s, s) for s in req.scope.split()],
            "ticket": ticket,
            "csrf": csrf,
        },
    )


async def consent(request: Request) -> Response:
    srv = server(request)
    form = await request.form()
    csrf = _csrf_ok(request, form)
    ticket: dict[str, Any] | None = srv.tickets.verify(str(form.get("ticket") or ""), now=srv.now_s())
    if csrf is None or ticket is None or not hmac.compare_digest(str(ticket.get("csrf", "")), csrf):
        return error_page(request, "Your sign-in page expired. Go back to the app and try again.", 403)
    req = await validate(srv, request, QueryParams(ticket["req"]))  # re-check: the client may have changed
    if isinstance(req, Response):
        return req

    if form.get("decision") != "allow":
        response = redirect_to_client(
            req.redirect_uri,
            {"error": "access_denied", "state": req.state, "iss": srv.settings.issuer},
        )
    else:
        code = secrets.token_urlsafe(32)
        await srv.db().save_code(
            code=code,
            client_id=req.client.client_id,
            user_id=ticket["u"],
            workspace_id=ticket["ws"],
            redirect_uri=req.redirect_uri,
            code_challenge=req.code_challenge,
            resource=req.resource,
            scope=req.scope,
            expires_at=srv.clock.now() + timedelta(seconds=CODE_TTL_S),
        )
        response = redirect_to_client(
            req.redirect_uri, {"code": code, "state": req.state, "iss": srv.settings.issuer}
        )
    response.delete_cookie(CSRF_COOKIE, path="/")
    return response
