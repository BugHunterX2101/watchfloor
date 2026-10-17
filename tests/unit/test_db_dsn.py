"""The connection URL built from an RDS secret keeps every part of it in its place."""

from __future__ import annotations

from urllib.parse import unquote, urlparse

from watchfloor_core.db import dsn_from_parts


def test_a_password_with_reserved_characters_stays_a_password() -> None:
    password = "p@ss/w:rd#1?%"  # noqa: S105 - a test value
    dsn = dsn_from_parts(
        {"username": "wf_app", "password": password, "host": "db.example.internal", "dbname": "watchfloor"}
    )
    url = urlparse(dsn)
    assert url.hostname == "db.example.internal" and url.port == 5432 and url.path == "/watchfloor"
    assert url.username == "wf_app" and unquote(url.password or "") == password
    assert url.query == "sslmode=require"


def test_url_safe_values_come_through_unchanged() -> None:
    secret = {"username": "u", "password": "aB3-_x", "host": "h"}  # noqa: S105
    assert dsn_from_parts(secret) == "postgresql://u:aB3-_x@h:5432/watchfloor?sslmode=require"
