"""Encrypted cookie sessions (Fernet: AES-128-CBC + HMAC-SHA256, timestamped).

The harness is a public OAuth client with no server-side store (it runs on
Lambda), so the tokens ride in an HttpOnly, SameSite=Lax cookie that only this
API can read. Two cookies:

- ``wf_h``: the session, ``{"at": access, "rt": refresh, "exp": epoch seconds}``;
- ``wf_h_pkce``: one sign-in attempt, ``{"v": verifier, "s": state}``, valid 10 minutes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

SESSION_COOKIE = "wf_h"
PKCE_COOKIE = "wf_h_pkce"
PKCE_TTL_S = 600
SESSION_TTL_S = 30 * 86400  # the refresh token's lifetime; the access token is refreshed inside it


@dataclass
class Session:
    access_token: str
    refresh_token: str | None
    expires_at: float

    def expiring(self, within_s: float = 30) -> bool:
        return self.expires_at - time.time() < within_s


class Box:
    def __init__(self, secret: str) -> None:
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        self._f = Fernet(key)

    def seal(self, data: dict[str, Any]) -> str:
        return self._f.encrypt(json.dumps(data, separators=(",", ":")).encode()).decode()

    def open(self, token: str | None, *, ttl: int) -> dict[str, Any] | None:
        if not token:
            return None
        try:
            data = json.loads(self._f.decrypt(token.encode(), ttl=ttl))
        except (InvalidToken, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def seal_session(self, s: Session) -> str:
        return self.seal({"at": s.access_token, "rt": s.refresh_token, "exp": s.expires_at})

    def open_session(self, token: str | None) -> Session | None:
        data = self.open(token, ttl=SESSION_TTL_S)
        if not data or not isinstance(data.get("at"), str):
            return None
        return Session(data["at"], data.get("rt"), float(data.get("exp", 0)))
