"""Response headers for the harness: what the browser may load and embed, and what it may keep.

The page is a confirm-by-voice product, so it must not be framed (a hidden frame could click Approve)
and it loads nothing from anywhere else: scripts, styles, fonts and requests are all this origin's.
The only inline allowance is ``style=""`` attributes (the bars on Pipeline and Activity set a width).
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSP = "; ".join(
    (
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "style-src-attr 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "manifest-src 'self'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "object-src 'none'",
    )
)

# Microphone for this page only (hands-free listening); nothing else the page has no use for.
PERMISSIONS = "microphone=(self), camera=(), geolocation=(), payment=(), usb=(), interest-cohort=()"

FIXED = {
    "content-security-policy": CSP,
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "permissions-policy": PERMISSIONS,
    "cross-origin-opener-policy": "same-origin",
}

ONE_YEAR_S = 31_536_000


def cache_control(path: str, status: int, content_type: str) -> str:
    """Built assets carry a hash in their name, so they never change; everything else is private and fresh."""
    if status < 400 and path.startswith("/assets/"):
        return f"public, max-age={ONE_YEAR_S}, immutable"
    if status < 400 and content_type.startswith("text/html"):
        return "no-cache"  # revalidate, so a new build is picked up at once
    return "no-store"  # the API, sign-in and health answers are about one person, at one moment


class SecurityHeaders:
    def __init__(self, app: ASGIApp, *, hsts: bool = False) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in FIXED.items():
                    headers.setdefault(name, value)
                if self.hsts:
                    headers.setdefault("strict-transport-security", f"max-age={ONE_YEAR_S}")
                if "cache-control" not in headers:
                    headers["cache-control"] = cache_control(
                        path, message["status"], headers.get("content-type", "")
                    )
            await send(message)

        await self.app(scope, receive, send_with_headers)
