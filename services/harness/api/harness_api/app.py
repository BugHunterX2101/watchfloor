"""Harness API (DD §17, TD §13.1): sign-in, one chat turn per request, and the web app.

    GET  /oauth/login      start OAuth 2.1 + PKCE as the public ``wf-harness`` client
    GET  /oauth/callback   exchange the code; the tokens go into an encrypted cookie
    GET  /api/session      {"signed_in": bool, "engine": ...}
    POST /api/logout       revoke the refresh token and clear the cookie
    POST /converse         {"message", "history", "context"?, "profile"?, "unheard"?}
                           → {"reply", "engine", "calls", "ui", "actions", "awaiting"}
    POST /api/tool         {"name", "arguments"} from a ui:// view → the tool result (allowlisted tools)
    POST /api/dismiss      forget the change waiting for an answer (the user said no)
    GET  /api/view?uri=    a ui:// view's page, for the sandboxed iframe, under its own policy
    GET  /healthz          liveness
    GET  /                 the built web app (services/harness/web/dist), when present

Cross-site requests can't use the session: the cookie is SameSite=Lax and the
POST endpoints take JSON only, which a cross-origin page can't send without a
CORS preflight this app never grants. Every response carries a strict Content-Security-Policy, no framing
and no sniffing (headers.py), and a person is held to a number of turns and taps a minute (limits.py), since
a turn runs a language model.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, BeforeValidator, Field, ValidationError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import pkce
from .agent import UI_CACHE_S, ModelFactory, Pending, awaiting, model_chain, next_pending, run_turn
from .config import HarnessConfig, Misconfigured, get_config
from .control import Profile, Screen
from .headers import SecurityHeaders
from .limits import Limiter, subject
from .mcp_http import InvalidParams, McpError, McpHttp, RateLimited, Unauthorized
from .offline import Focus
from .refresh import Refreshes
from .session import (
    FOCUS_COOKIE,
    FOCUS_TTL_S,
    PENDING_COOKIE,
    PENDING_TTL_S,
    PKCE_COOKIE,
    PKCE_TTL_S,
    SESSION_COOKIE,
    SESSION_TTL_S,
    Box,
    Session,
)

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
    # Clipped, not refused: one long model reply must not make every later turn a 400.
    text: Annotated[str, BeforeValidator(lambda v: v[:2000] if isinstance(v, str) else v)]


class ConverseIn(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    history: list[Line] = Field(default_factory=list, max_length=24)
    # What the page shows now (fixed words only), so "this page" and "that one" mean something.
    context: Screen | None = None
    # What the browser knows about the user (name, time of day, reply length, pins): context only.
    profile: Profile | None = None
    # Said before the user heard the previous reply (they spoke up while it was being worked on): it
    # can change what they asked for, but it can never be the "yes" to a change they haven't heard.
    unheard: bool = False


class ToolIn(BaseModel):
    name: str = Field(max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)


class Refused(Exception):
    """The MCP server refused a view's tool arguments (-32602): a 400 for the page, not a 502."""


# The tools that make, renew, finish or take back a change: only these move what waits for an answer.
FLOW_TOOLS = frozenset(
    {"propose_action", "extend_preview", "confirm_action", "undo_action", "log_deal_update"}
)


# What a ui:// view may call through the host (DD §15): reads, starting a review, and the
# two-step action flow with its edges, more time on a preview and undo on the receipt (ADR-021).
# A tap on the sheet's action button is the user's explicit confirmation (DD §15.5).
VIEW_TOOLS = frozenset(
    {
        "get_risk_briefing",
        "explain_deal_risk",
        "get_triage_result",
        "start_deep_triage",
        "propose_action",
        "extend_preview",
        "confirm_action",
        "undo_action",
        # The harness's own Orders page (not a ui:// view): list the user's standing orders and
        # remove one on an explicit tap, after the page's own confirmation step.
        "list_standing_orders",
        "remove_standing_order",
    }
)


# Per signed-in person, per minute. A turn runs a model, so it is the dear one; a tap on a view is a
# single tool call. Both are far above what a person produces by speaking or tapping.
CONVERSE_PER_MIN = 30
TOOL_PER_MIN = 120

VIEW_URI = re.compile(r"ui://watchfloor/[a-z0-9-]{1,40}")
# What a view may do: run its own inline script and style, and nothing that reaches outside itself.
VIEW_CSP = "; ".join(
    (
        "default-src 'none'",
        "script-src 'unsafe-inline'",
        "style-src 'unsafe-inline'",
        "img-src data:",
        "font-src data:",
        "connect-src 'none'",
        "form-action 'none'",
        "base-uri 'none'",
        "frame-ancestors 'self'",
        "sandbox allow-scripts",
    )
)

# RFC 6749 §4.1.2.1. Anything else in ``?error=`` is someone's text, and the page would print it.
OAUTH_ERRORS = frozenset(
    {
        "invalid_request",
        "unauthorized_client",
        "access_denied",
        "unsupported_response_type",
        "invalid_scope",
        "server_error",
        "temporarily_unavailable",
    }
)


def create_app(
    cfg: HarnessConfig | None = None,
    *,
    auth_transport: httpx.AsyncBaseTransport | None = None,
    mcp_transport: httpx.AsyncBaseTransport | None = None,
    model_factory: ModelFactory | None = None,
) -> Starlette:
    conf = cfg or get_config()
    box = Box(conf.session_secret)
    s = conf.core
    shared: dict[str, httpx.AsyncClient] = {}
    ui_cache: dict[str, tuple[float, str]] = {}  # view HTML by server and URI (agent._view)
    converse_limit, tool_limit = Limiter(CONVERSE_PER_MIN), Limiter(TOOL_PER_MIN)
    refreshes = Refreshes()

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

    def read_pending(request: Request) -> Pending | None:
        return Pending.from_dict(box.open(request.cookies.get(PENDING_COOKIE), ttl=PENDING_TTL_S))

    def keep_pending(request: Request, resp: Response, pending: Pending | None) -> None:
        """Seal what waits for the next turn into the cookie, or clear it. Only after a turn that ran."""
        if resp.status_code != 200:
            return
        if pending is not None:
            set_cookie(resp, PENDING_COOKIE, box.seal(pending.to_dict()), PENDING_TTL_S)
        elif PENDING_COOKIE in request.cookies:
            resp.delete_cookie(PENDING_COOKIE, path="/")

    def read_focus(request: Request) -> Focus | None:
        return Focus.from_dict(box.open(request.cookies.get(FOCUS_COOKIE), ttl=FOCUS_TTL_S))

    def keep_focus(request: Request, resp: Response, focus: Focus | None) -> None:
        if resp.status_code != 200:
            return
        if focus is not None and (focus.deal or focus.listed or focus.segment):
            set_cookie(resp, FOCUS_COOKIE, box.seal(focus.to_dict()), FOCUS_TTL_S)
        elif FOCUS_COOKIE in request.cookies:
            resp.delete_cookie(FOCUS_COOKIE, path="/")

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
            resp = back_to_web(signin=q["error"] if q["error"] in OAUTH_ERRORS else "oauth_error")
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
        resp.delete_cookie(PENDING_COOKIE, path="/")
        resp.delete_cookie(FOCUS_COOKIE, path="/")
        return resp

    def signed_out() -> Response:
        resp = JSONResponse({"error": "signed_out", "message": "Sign in to continue."}, status_code=401)
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp

    async def fresh(session: Session) -> Session | None:
        token = session.refresh_token
        if not token:
            return None
        try:
            # Coalesced: a second request with the same refresh token would read as token theft.
            return await refreshes.run(token, lambda: pkce.refresh(s, token, transport=auth_transport))
        except pkce.OAuthError:
            return None

    def too_many(wait: int) -> Response:
        return JSONResponse(
            {
                "error": "rate_limited",
                "message": f"That's a lot of requests at once. Try again in {wait} seconds.",
            },
            status_code=429,
            headers={"Retry-After": str(wait)},
        )

    async def with_session(
        request: Request, work: Callable[[str], Awaitable[dict[str, Any]]], limit: Limiter
    ) -> Response:
        """Run ``work`` with the user's access token: refresh it when due or rejected, once."""
        session = box.open_session(request.cookies.get(SESSION_COOKIE))
        if session is None:
            return signed_out()
        wait = limit.hit(subject(session.access_token))
        if wait:
            return too_many(wait)
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
        except RateLimited as exc:  # the MCP server's own per-user limit: busy, not down
            return too_many(exc.retry_after)
        except Refused as exc:  # a view sent arguments the MCP server refused: its to fix, not an outage
            return JSONResponse({"error": "invalid_request", "message": str(exc)}, status_code=400)
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
        pending = read_pending(request)
        focus = read_focus(request)
        after: dict[str, Any] = {}

        async def turn(token: str) -> dict[str, Any]:
            result = await run_turn(
                conf,
                token,
                body.message,
                history,
                http=mcp_client(),
                model_factory=model_factory,
                ui_cache=ui_cache,
                pending=pending,
                screen=body.context,
                profile=body.profile,
                unheard=body.unheard,
                focus=focus,
            )
            after["pending"] = result.pending
            after["focus"] = result.focus
            return result.public()

        resp = await with_session(request, turn, converse_limit)
        keep_pending(request, resp, after.get("pending"))
        keep_focus(request, resp, after.get("focus"))
        return resp

    async def view_tool(request: Request) -> Response:
        """A tool call from a ui:// view (row tap, Propose, the sheet's action, Undo), proxied with the user's token."""
        body = await json_body(request, ToolIn)
        if isinstance(body, Response):
            return body
        assert isinstance(body, ToolIn)
        if body.name not in VIEW_TOOLS:
            return JSONResponse(
                {"error": "not_allowed", "message": f"{body.name} is not available to views"}, 403
            )

        pending = read_pending(request)
        after: dict[str, Pending | None] = {}

        async def call(token: str) -> dict[str, Any]:
            try:
                c = await McpHttp(s.mcp_url, token, http=mcp_client()).call_tool(body.name, body.arguments)
            except InvalidParams as exc:
                raise Refused(str(exc)) from None
            # A preview made in a view can be confirmed by voice. A plain read (a poll of the board, a
            # row opened) leaves whatever waits for the user's answer exactly as it was.
            after["pending"] = next_pending(pending, [c]) if body.name in FLOW_TOOLS else pending
            return {
                "structuredContent": c.structured,
                "content": [{"type": "text", "text": c.text}] if c.text else [],
                "isError": not c.ok,
                "call": c.public(),
                "awaiting": awaiting(after["pending"], pending),
            }

        resp = await with_session(request, call, tool_limit)
        keep_pending(request, resp, after.get("pending"))
        return resp

    async def dismiss(request: Request) -> Response:
        """Drop whatever waits for the user's answer (a preview, a held write): they said no, or never mind.

        The model is told to leave an unanswered preview alone, so this only makes "no" certain: a
        later "yes" cannot confirm a change the user turned down.
        """
        if box.open_session(request.cookies.get(SESSION_COOKIE)) is None:
            return signed_out()
        resp = JSONResponse({"awaiting": None})
        resp.delete_cookie(PENDING_COOKIE, path="/")
        return resp

    async def view_page(request: Request) -> Response:
        """A ``ui://`` view as a page of its own, for the sandboxed iframe on Watch.

        The view is one self-contained file with inline script and styles, which the app's own policy
        rightly refuses, so it gets a policy of its own: inline code, and nothing else. It cannot make a
        request, submit a form, load an image from anywhere or be framed by another site, and ``sandbox``
        keeps it in an opaque origin (no cookies, no way to call this API) even if its URL is opened
        directly. It talks to the page by ``postMessage`` only, through the host (apphost.ts).
        """
        uri = request.query_params.get("uri", "")
        if not VIEW_URI.fullmatch(uri):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        session = box.open_session(request.cookies.get(SESSION_COOKIE))
        if session is None:
            return signed_out()
        key = f"{s.mcp_url} {uri}"
        hit = ui_cache.get(key)
        html: str | None = hit[1] if hit is not None and hit[0] > time.monotonic() else None
        if html is None:  # the turn that showed it normally cached it; an old entry may have lapsed
            if tool_limit.hit(subject(session.access_token)):
                return JSONResponse({"error": "rate_limited"}, status_code=429)
            try:
                html = await McpHttp(s.mcp_url, session.access_token, http=mcp_client()).read_resource(uri)
            except Unauthorized:
                return signed_out()
            except RateLimited as exc:
                return too_many(exc.retry_after)
            except McpError:
                return JSONResponse({"error": "mcp_unavailable"}, status_code=502)
            if html is None:
                return JSONResponse({"error": "not_found"}, status_code=404)
            ui_cache[key] = (time.monotonic() + UI_CACHE_S, html)
        return HTMLResponse(
            html, headers={"content-security-policy": VIEW_CSP, "x-frame-options": "SAMEORIGIN"}
        )

    async def healthz(request: Request) -> Response:
        models = [name for name, _ in model_chain(conf)]  # names only, never a key
        return JSONResponse({"ok": True, "engine": conf.engine, "models": models})

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
        Route("/api/dismiss", dismiss, methods=["POST"]),
        Route("/api/view", view_page, methods=["GET"]),
    ]
    if (conf.web_dist / "index.html").is_file():
        routes.append(Mount("/", StaticFiles(directory=conf.web_dist, html=True)))
    else:
        routes.append(Route("/", not_built, methods=["GET"]))
    return Starlette(
        routes=routes,
        lifespan=lifespan,
        middleware=[Middleware(SecurityHeaders, hsts=conf.secure_cookies)],
    )


def __getattr__(name: str) -> Any:
    # `uvicorn harness_api.app:app` builds the app on first access, after the environment is read.
    if name == "app":
        return create_app()
    raise AttributeError(name)
