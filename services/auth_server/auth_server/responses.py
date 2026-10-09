"""Response helpers: OAuth error JSON, no-store token responses and hardened HTML pages."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from .context import TEMPLATES

NO_STORE = {"cache-control": "no-store", "pragma": "no-cache"}
HTML_HEADERS = {
    **NO_STORE,
    "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
}


def oauth_error(error: str, description: str, status: int = 400, *, basic_auth: bool = False) -> JSONResponse:
    headers = dict(NO_STORE)
    if basic_auth and status == 401:
        headers["www-authenticate"] = 'Basic realm="watchfloor"'  # RFC 6749 §5.2 (token endpoint only)
    return JSONResponse(
        {"error": error, "error_description": description}, status_code=status, headers=headers
    )


def token_json(payload: dict[str, Any]) -> JSONResponse:
    return JSONResponse(payload, headers=NO_STORE)


def page(request: Request, template: str, context: dict[str, Any], status: int = 200) -> Response:
    response = TEMPLATES.TemplateResponse(request, template, context, status_code=status)
    response.headers.update(HTML_HEADERS)
    return response


def error_page(request: Request, message: str, status: int = 400) -> Response:
    """Shown when the client or redirect URI can't be trusted, so we must not redirect (RFC 6749 §4.1.2.1)."""
    return page(request, "error.html", {"message": message}, status)


def redirect_to_client(redirect_uri: str, params: dict[str, str | None]) -> RedirectResponse:
    """303 so the browser follows with GET after a form POST; never 307, which would re-POST."""
    query = urlencode({k: v for k, v in params.items() if v is not None})
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}{query}", status_code=303, headers=NO_STORE)
