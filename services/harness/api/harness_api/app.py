"""Harness API (DD §17, TD §13.1): sign-in, one chat turn per request, and the web app.

    GET  /oauth/login      start OAuth 2.1 + PKCE as the public ``wf-harness`` client
    GET  /oauth/callback   exchange the code; the tokens go into an encrypted cookie
    GET  /api/session      {"signed_in": bool, "engine": ...}
    POST /api/logout       revoke the refresh token and clear the cookie
    POST /converse         {"message", "history"} → {"reply", "engine", "calls", "ui"}
    POST /api/tool         {"name", "arguments"} from a ui:// view → the tool result (allowlisted tools)
    GET  /healthz          liveness
    GET  /                 the built web app (services/harness/web/dist), when present

Cross-site requests can't use the session: the cookie is SameSite=Lax and the
POST endpoints take JSON only, which a cross-origin page can't send without a
CORS preflight this app never grants.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, Field, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import pkce
from .agent import bedrock_model, run_turn
from .config import HarnessConfig, Misconfigured, get_config
from .mcp_http import McpError, McpHttp, Unauthorized
from .session import PKCE_COOKIE, PKCE_TTL_S, SESSION_COOKIE, SESSION_TTL_S, Box, Session

log = logging.getLogger("watchfloor.harness")

NOT_BUILT = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Watchfloor harness</title></head>
<body style="font-family:system-ui,sans-serif;margin:24px;max-width:40em">
<p><strong>Simulated Alexa+ experience</strong></p>
<p>The harness web app has not been built. Run <code>npm ci &amp;&amp; npm run build</code> in
<code>services/harness/web</code>, or use the Vite dev server on :5173.</p>
<p><a href="/oauth/login">Sign in</a> to try <code>POST /converse</code> directly.</p>
</body></html>"""


class Line(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=2000)


class ConverseIn(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    history: list[Line] = Field(default_factory=list, max_length=24)


class ToolIn(BaseModel):
    name: str = Field(max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)


# What a ui:// view may call through the host (DD §15): reads, starting a review, and the
# two-step action flow. A tap on Approve is the user's explicit confirmation (DD §15.5).
VIEW_TOOLS = frozenset(
    {
        "get_risk_briefing",
        "explain_deal_risk",
        "get_triage_result",
        "start_deep_triage",
        "propose_action",
        "confirm_action",
    }
)


def create_app(
    cfg: HarnessConfig | None = None,
    *,
    auth_transport: httpx.AsyncBaseTransport | None = None,
    mcp_transport: httpx.AsyncBaseTransport | None = None,
    model_factory: Callable[[HarnessConfig], Any] = bedrock_model,
) -> Starlette:
    conf = cfg or get_config()
    box = Box(conf.session_secret)
    s = conf.core
    shared: dict[str, httpx.AsyncClient] = {}

    def mcp_client() -> httpx.AsyncClient:
        """One kept-alive client for every MCP call this process makes (see McpHttp.http)."""
        if "mcp" not in shared:
            shared["mcp"] = httpx.AsyncClient(transport=mcp_transport, timeout=15.0)
        return shared["mcp"]

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        yield
        if "mcp" in shared:
            await shared.pop("mcp").aclose()

    def set_cookie(resp: Response, name: str, value: str, max_age: int) -> None:
        resp.set_cookie(
            name, value, max_age=max_age, httponly=True, samesite="lax", secure=conf.secure_cookies, path="/"
        )

    def back_to_web(**query: str) -> RedirectResponse:
        sep = "&" if "?" in conf.web_url else "?"
        return RedirectResponse(conf.web_url + (sep + urlencode(query) if query else ""), status_code=303)

    async def login(request: Request) -> Response:
        verifier, state = pkce.new_attempt()
        resp = RedirectResponse(pkce.authorize_url(s, verifier, state), status_code=302)
        set_cookie(resp, PKCE_COOKIE, box.seal({"v": verifier, "s": state}), PKCE_TTL_S)
        return resp

    async def callback(request: Request) -> Response:
        q = request.query_params
        attempt = box.open(request.cookies.get(PKCE_COOKIE), ttl=PKCE_TTL_S)
        if q.get("error"):
            resp = back_to_web(signin=q["error"][:60])
        elif not attempt or q.get("state") != attempt.get("s"):
            resp = back_to_web(signin="state_mismatch")
        elif q.get("iss") not in (None, s.issuer):  # RFC 9207: a mix-up attack names another issuer
            resp = back_to_web(signin="issuer_mismatch")
        elif not q.get("code"):
            resp = back_to_web(signin="no_code")
        else:
            try:
                session = await pkce.exchange(s, q["code"], attempt["v"], transport=auth_transport)
            except pkce.OAuthError as exc:
                log.warning("harness code exchange failed: %s", exc)
                resp = back_to_web(signin="token_error")
            else:
                resp = back_to_web()
                set_cookie(resp, SESSION_COOKIE, box.seal_session(session), SESSION_TTL_S)
        resp.delete_cookie(PKCE_COOKIE, path="/")
        return resp

    async def session_info(request: Request) -> Response:
        signed_in = box.open_session(request.cookies.get(SESSION_COOKIE)) is not None
        return JSONResponse({"signed_in": signed_in, "engine": conf.engine})

    async def logout(request: Request) -> Response:
        session = box.open_session(request.cookies.get(SESSION_COOKIE))
        if session and session.refresh_token:
            await pkce.revoke(s, session.refresh_token, transport=auth_transport)
        resp = JSONResponse({"signed_in": False})
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp

    def signed_out() -> Response:
        resp = JSONResponse({"error": "signed_out", "message": "Sign in to continue."}, status_code=401)
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp

    async def fresh(session: Session) -> Session | None:
        if not session.refresh_token:
            return None
        try:
            return await pkce.refresh(s, session.refresh_token, transport=auth_transport)
        except pkce.OAuthError:
            return None

    async def with_session(request: Request, work: Callable[[str], Awaitable[dict[str, Any]]]) -> Response:
        """Run ``work`` with the user's access token: refresh it when due or rejected, once."""
        session = box.open_session(request.cookies.get(SESSION_COOKIE))
        if session is None:
            return signed_out()
        renewed = False
        if session.expiring():
            session, renewed = await fresh(session), True
            if session is None:
                return signed_out()
        try:
            try:
                result = await work(session.access_token)
            except Unauthorized:
                session, renewed = await fresh(session), True
                if session is None:
                    return signed_out()
                result = await work(session.access_token)
        except Unauthorized:
            return signed_out()
        except Misconfigured as exc:
            return JSONResponse({"error": "misconfigured", "message": str(exc)}, status_code=503)
        except McpError as exc:
            return JSONResponse({"error": "mcp_unavailable", "message": str(exc)}, status_code=502)
        except TimeoutError:
            return JSONResponse({"error": "timeout", "message": "That took too long."}, status_code=504)
        resp = JSONResponse(result)
        if renewed:
            set_cookie(resp, SESSION_COOKIE, box.seal_session(session), SESSION_TTL_S)
        return resp

    async def json_body(request: Request, model: type[BaseModel]) -> BaseModel | Response:
        if "application/json" not in request.headers.get("content-type", ""):
            return JSONResponse({"error": "unsupported_media_type"}, status_code=415)
        try:
            return model.model_validate(await request.json())
        except (ValidationError, ValueError):
            return JSONResponse({"error": "invalid_request"}, status_code=400)

    async def converse(request: Request) -> Response:
        body = await json_body(request, ConverseIn)
        if isinstance(body, Response):
            return body
        assert isinstance(body, ConverseIn)
        history = [(line.role, line.text) for line in body.history]

        async def turn(token: str) -> dict[str, Any]:
            result = await run_turn(
                conf, token, body.message, history, http=mcp_client(), model_factory=model_factory
            )
            return result.public()

        return await with_session(request, turn)

    async def view_tool(request: Request) -> Response:
        """A tool call from a ui:// view (row tap, Propose, Approve), proxied with the user's token."""
        body = await json_body(request, ToolIn)
        if isinstance(body, Response):
            return body
        assert isinstance(body, ToolIn)
        if body.name not in VIEW_TOOLS:
            return JSONResponse(
                {"error": "not_allowed", "message": f"{body.name} is not available to views"}, 403
            )

        async def call(token: str) -> dict[str, Any]:
            c = await McpHttp(s.mcp_url, token, http=mcp_client()).call_tool(body.name, body.arguments)
            return {
                "structuredContent": c.structured,
                "content": [{"type": "text", "text": c.text}] if c.text else [],
                "isError": not c.ok,
                "call": c.public(),
            }

        return await with_session(request, call)

    async def healthz(request: Request) -> Response:
        return JSONResponse({"ok": True, "engine": conf.engine})

    async def not_built(request: Request) -> Response:
        return HTMLResponse(NOT_BUILT)

    routes: list[Any] = [
        Route("/healthz", healthz, methods=["GET"]),
        Route("/oauth/login", login, methods=["GET"]),
        Route("/oauth/callback", callback, methods=["GET"]),
        Route("/api/session", session_info, methods=["GET"]),
        Route("/api/logout", logout, methods=["POST"]),
        Route("/converse", converse, methods=["POST"]),
        Route("/api/tool", view_tool, methods=["POST"]),
    ]
    if (conf.web_dist / "index.html").is_file():
        routes.append(Mount("/", StaticFiles(directory=conf.web_dist, html=True)))
    else:
        routes.append(Route("/", not_built, methods=["GET"]))
    return Starlette(routes=routes, lifespan=lifespan)


def __getattr__(name: str) -> Any:
    # `uvicorn harness_api.app:app` builds the app on first access, after the environment is read.
    if name == "app":
        return create_app()
    raise AttributeError(name)
