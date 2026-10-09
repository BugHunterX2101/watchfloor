"""Public signing keys for resource servers (cached by the MCP server for 10 minutes)."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from .context import server


async def jwks(request: Request) -> JSONResponse:
    return JSONResponse(server(request).keys().jwks(), headers={"cache-control": "public, max-age=600"})
