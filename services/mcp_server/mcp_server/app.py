"""Starlette application: routes, lifespan and middleware wiring (TD §6.2).

``/mcp`` is routed to an ASGI endpoint (not a Mount) so there is no
trailing-slash redirect; GET and DELETE answer 405 because the server is
stateless and opens no SSE stream.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.types import Receive, Scope, Send
from watchfloor_core.db import close_pool, open_pool
from watchfloor_core.settings import get_settings

from .middleware import JwksCache, build_middleware
from .middleware._http import respond
from .server import build_server
from .session import build_session_manager
from .wellknown import healthz, protected_resource_metadata

PRM_PATH = "/.well-known/oauth-protected-resource"


class McpEndpoint:
    """ASGI endpoint for /mcp: POST goes to the SDK manager, everything else is 405."""

    def __init__(self, manager: StreamableHTTPSessionManager) -> None:
        self.manager = manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["method"] != "POST":
            await respond(send, 405, headers=[(b"allow", b"POST")])
            return
        await self.manager.handle_request(scope, receive, send)


def _configure_logging() -> None:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO, format="%(message)s")


def create_app(*, jwks: JwksCache | None = None, manage_pool: bool = True) -> Starlette:
    """Build the app. Tests pass static JWKS and may manage the DB pool themselves."""
    _configure_logging()
    settings = get_settings()
    manager = build_session_manager(build_server())

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        if manage_pool:
            await open_pool()
        try:
            async with manager.run():
                yield
        finally:
            if manage_pool:
                await close_pool()

    return Starlette(
        routes=[
            Route(PRM_PATH, protected_resource_metadata, methods=["GET"]),
            Route(PRM_PATH + "/mcp", protected_resource_metadata, methods=["GET"]),
            Route("/healthz", healthz, methods=["GET"]),
            Route("/mcp", endpoint=McpEndpoint(manager)),
        ],
        middleware=build_middleware(settings, jwks or JwksCache(settings.jwks_url)),
        lifespan=lifespan,
    )


app = create_app()
