"""Authorization server application: routes and lifespan (TD §5, §13.1)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from watchfloor_core.clock import Clock
from watchfloor_core.settings import get_settings

from . import __version__
from .authorize import authorize, consent, login
from .context import AuthServer, server
from .jwks import jwks
from .legal import ADDON_ASSETS, privacy, terms
from .metadata import authorization_server_metadata
from .revoke import revoke
from .signer import KmsSigner, Signer, signer_from_settings
from .storage import AuthStore, auth_dsn
from .tickets import Tickets
from .token import token

log = logging.getLogger("watchfloor.auth")


async def healthz(request: Request) -> JSONResponse:
    srv = server(request)
    ok = srv.store is not None and await srv.store.ping()
    return JSONResponse(
        {"ok": ok, "db": "ok" if ok else "down", "version": __version__}, status_code=200 if ok else 503
    )


def _tickets(secret: str | None, env: str) -> Tickets:
    if secret:
        return Tickets(secret.encode())
    if env not in ("local", "test"):
        raise RuntimeError("WF_SESSION_SECRET is required outside local development")
    return Tickets.random()


def create_app(
    *, store: AuthStore | None = None, signer: Signer | None = None, clock: Clock | None = None
) -> Starlette:
    """Tests pass an open store and an in-memory signer; production builds both in the lifespan."""
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = get_settings()
    state = AuthServer(
        settings=settings,
        tickets=_tickets(settings.session_secret, settings.env),
        clock=clock or Clock(),
        signer=signer,
        store=store,
    )

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        owns_store = state.store is None
        if state.signer is None:
            state.signer = signer_from_settings(settings)
        if isinstance(state.signer, KmsSigner):
            await state.signer.load()
        if owns_store:
            state.store = await AuthStore.open(auth_dsn(settings))
        try:
            yield
        finally:
            if owns_store and state.store is not None:
                await state.store.close()
                state.store = None

    app = Starlette(
        routes=[
            Route("/.well-known/oauth-authorization-server", authorization_server_metadata, methods=["GET"]),
            Route("/jwks.json", jwks, methods=["GET"]),
            Route("/authorize", authorize, methods=["GET"]),
            Route("/login", login, methods=["POST"]),
            Route("/consent", consent, methods=["POST"]),
            Route("/token", token, methods=["POST"]),
            Route("/revoke", revoke, methods=["POST"]),
            Route("/healthz", healthz, methods=["GET"]),
            Route("/privacy", privacy, methods=["GET"]),  # addon.json listing links
            Route("/terms", terms, methods=["GET"]),
            Mount("/static/addon", StaticFiles(directory=ADDON_ASSETS), name="addon"),  # listing images
        ],
        lifespan=lifespan,
    )
    app.state.wf = state
    return app


app = create_app()
