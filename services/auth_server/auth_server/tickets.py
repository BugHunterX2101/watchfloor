"""HMAC-signed, short-lived tickets that carry an authorization request from login to consent.

Stateless on purpose: any Lambda instance can verify a ticket another one
issued, as long as they share ``WF_SESSION_SECRET``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Any

import orjson


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Tickets:
    def __init__(self, secret: bytes) -> None:
        if len(secret) < 32:
            raise ValueError("ticket secret must be at least 32 bytes")
        self._secret = secret

    @classmethod
    def random(cls) -> Tickets:
        """Local and test only: tickets then survive only within this process."""
        return cls(secrets.token_bytes(32))

    def _mac(self, body: str) -> str:
        return _b64(hmac.new(self._secret, body.encode(), hashlib.sha256).digest())

    def issue(self, payload: dict[str, Any], *, now: int, ttl_s: int) -> str:
        body = _b64(orjson.dumps({**payload, "exp": now + ttl_s}))
        return f"{body}.{self._mac(body)}"

    def verify(self, ticket: str, *, now: int) -> dict[str, Any] | None:
        body, _, mac = ticket.partition(".")
        if not body or not mac or not hmac.compare_digest(mac, self._mac(body)):
            return None
        try:
            payload = orjson.loads(_unb64(body))
        except (ValueError, orjson.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or int(payload.get("exp", 0)) < now:
            return None
        return payload
