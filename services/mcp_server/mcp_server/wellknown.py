"""Public endpoints: RFC 9728 protected-resource metadata and /healthz (DD §5.1, §12.3)."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from watchfloor_core import db
from watchfloor_core.principal import Scope
from watchfloor_core.settings import get_settings

from . import __version__


async def protected_resource_metadata(request: Request) -> JSONResponse:
    s = get_settings()
    return JSONResponse(
        {
            "resource": s.resource_uri,
            "authorization_servers": [s.issuer],
            "scopes_supported": [Scope.READ, Scope.WRITE],
            "bearer_methods_supported": ["header"],
            "resource_name": "Watchfloor",
        },
        headers={"cache-control": "public, max-age=300"},
    )


async def healthz(request: Request) -> JSONResponse:
    ok = await db.ping()
    return JSONResponse(
        {"ok": ok, "db": "ok" if ok else "down", "version": __version__}, status_code=200 if ok else 503
    )
