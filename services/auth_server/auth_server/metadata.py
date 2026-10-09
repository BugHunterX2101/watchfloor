"""RFC 8414 authorization-server metadata (DD §12.3)."""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from .context import USER_SCOPES, server


def document(issuer: str) -> dict[str, Any]:
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "jwks_uri": f"{issuer}/jwks.json",
        "revocation_endpoint": f"{issuer}/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token", "client_credentials"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
        "revocation_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
        "scopes_supported": sorted(USER_SCOPES),
        "authorization_response_iss_parameter_supported": True,
    }


async def authorization_server_metadata(request: Request) -> JSONResponse:
    return JSONResponse(
        document(server(request).settings.issuer), headers={"cache-control": "public, max-age=300"}
    )
