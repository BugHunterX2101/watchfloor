"""Confirmation tokens for the propose/confirm loop (DD §8.2).

A token is ``wf1.`` + 32 random bytes (base64url). Only its SHA-256 is stored.
Parameters are bound by hashing their RFC 8785 canonical JSON, so any change
between propose and confirm is detected.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any

import rfc8785

TOKEN_PREFIX = "wf1."  # noqa: S105 - a format marker, not a secret
TOKEN_TTL_SECONDS = 300


def new_confirmation_token() -> tuple[str, bytes]:
    raw = secrets.token_bytes(32)
    token = TOKEN_PREFIX + base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return token, hash_token(token)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def looks_like_token(value: str) -> bool:
    return isinstance(value, str) and value.startswith(TOKEN_PREFIX) and 20 <= len(value) <= 80


def params_hash(params: dict[str, Any]) -> bytes:
    return hashlib.sha256(rfc8785.dumps(params)).digest()


def args_hash(args: dict[str, Any] | None) -> bytes:
    """Hash of tool arguments for the audit log (values themselves are never logged)."""
    return hashlib.sha256(rfc8785.dumps(args or {})).digest()
