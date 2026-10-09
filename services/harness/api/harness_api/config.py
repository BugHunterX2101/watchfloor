"""Harness settings: the shared ``WF_*`` values from core plus three of its own.

- ``WF_HARNESS_SESSION_SECRET``: encrypts the session cookie (32+ characters). Required
  outside local and test; locally a random per-process secret is used, so a restart
  signs everyone out.
- ``WF_HARNESS_WEB_URL``: where to send the browser after sign-in (default ``/``, the
  bundled web app; ``http://localhost:5173/`` for the Vite dev server).
- ``WF_HARNESS_WEB_DIST``: the built web app to serve (default ``../web/dist``).
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from watchfloor_core.settings import Settings, get_settings

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


class Misconfigured(RuntimeError):
    pass


@dataclass(frozen=True)
class HarnessConfig:
    core: Settings
    session_secret: str
    web_url: str
    web_dist: Path

    @property
    def secure_cookies(self) -> bool:
        return self.core.harness_redirect_uri.startswith("https://")

    @property
    def engine(self) -> str | None:
        """``strands`` with a Haiku-class model id, ``offline`` locally without one, else None."""
        if self.core.haiku_model_id:
            return "strands"
        if self.core.env in ("local", "test"):
            return "offline"
        return None


def load(core: Settings | None = None) -> HarnessConfig:
    s = core or get_settings()
    secret = os.environ.get("WF_HARNESS_SESSION_SECRET", "")
    if len(secret) < 32:
        if s.env not in ("local", "test"):
            raise Misconfigured("WF_HARNESS_SESSION_SECRET (32+ characters) is required outside local")
        secret = secrets.token_urlsafe(32)
    return HarnessConfig(
        core=s,
        session_secret=secret,
        web_url=os.environ.get("WF_HARNESS_WEB_URL", "/"),
        web_dist=Path(os.environ.get("WF_HARNESS_WEB_DIST", str(WEB_DIST))),
    )


@lru_cache(maxsize=1)
def get_config() -> HarnessConfig:
    return load()
