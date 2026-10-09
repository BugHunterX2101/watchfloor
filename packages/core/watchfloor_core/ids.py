"""Prefixed, sortable identifiers (TD §20.1), e.g. ``dl_01J9...``.

IDs are never spoken or shown in UI copy; they live only in structured content
and logs.
"""

from __future__ import annotations

from datetime import datetime

from ulid import ULID

PREFIXES = frozenset({"ws", "us", "ac", "ct", "dl", "act", "hist", "pr", "tk", "job", "rule", "al", "aud"})


def new_id(prefix: str, at: datetime | None = None) -> str:
    if prefix not in PREFIXES:
        raise ValueError(f"unknown id prefix: {prefix}")
    ulid = ULID.from_datetime(at) if at is not None else ULID()
    return f"{prefix}_{ulid}"


def has_prefix(value: str, prefix: str) -> bool:
    return isinstance(value, str) and value.startswith(prefix + "_") and len(value) > len(prefix) + 1
