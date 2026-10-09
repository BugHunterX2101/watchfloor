"""Process-wide state shared by the route modules, kept on ``app.state.wf``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from starlette.requests import Request
from starlette.templating import Jinja2Templates
from watchfloor_core.clock import Clock
from watchfloor_core.settings import Settings

from .signer import Signer
from .storage import AuthStore
from .tickets import Tickets

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

USER_SCOPES = frozenset({"watchfloor.read", "watchfloor.write"})
AGENT_SCOPE = "watchfloor.agent.read"
SCOPE_TEXT = {
    "watchfloor.read": "See your pipeline risk",
    "watchfloor.write": "Create tasks and alerts after you confirm",
}

CODE_TTL_S = 60
JOB_TOKEN_TTL_S = 300
TICKET_TTL_S = 300


@dataclass
class AuthServer:
    settings: Settings
    tickets: Tickets
    clock: Clock
    signer: Signer | None = None
    store: AuthStore | None = None

    def db(self) -> AuthStore:
        if self.store is None:
            raise RuntimeError("auth store is not open")
        return self.store

    def keys(self) -> Signer:
        if self.signer is None:
            raise RuntimeError("signer is not loaded")
        return self.signer

    def now_s(self) -> int:
        return int(self.clock.now().timestamp())


def server(request: Request) -> AuthServer:
    return request.app.state.wf


def client_ip(request: Request) -> str:
    """Behind API Gateway and the Lambda Web Adapter the caller is the first X-Forwarded-For hop."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and server(request).settings.env not in ("local", "test"):
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
