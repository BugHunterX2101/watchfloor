"""RFC 7009 revocation. Revoking a refresh token revokes its whole family.

Access tokens are self-contained JWTs and simply expire (60 min); the
response is 200 either way so callers learn nothing about token validity.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response

from .context import client_ip, server
from .responses import NO_STORE, oauth_error
from .token import TOKEN_IP_LIMIT_PER_MIN, ClientAuthError, authenticate_client


async def revoke(request: Request) -> Response:
    srv = server(request)
    # The same per-address count as /token, taken before any secret is hashed: this endpoint checks
    # client secrets too, and an Argon2 check per guess is work a stranger could otherwise demand for free.
    if await srv.db().rate_hit(f"token-ip:{client_ip(request)}") > TOKEN_IP_LIMIT_PER_MIN:
        return oauth_error("slow_down", "Too many requests. Try again in a minute.", 429)
    form = await request.form()
    try:
        client = await authenticate_client(srv, request, form)
    except ClientAuthError as exc:
        return oauth_error("invalid_client", exc.description, 401, basic_auth=exc.basic)
    presented = form.get("token")
    if isinstance(presented, str) and presented:
        await srv.db().revoke_refresh(presented, client.client_id, srv.clock.now())
    return Response(status_code=200, headers=NO_STORE)
