"""Which address the auth server rate-limits: the one API Gateway saw, never the one the caller wrote."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from auth_server.context import client_ip


def _request(env: str, forwarded: str | None, peer: str | None = "10.0.0.9") -> Any:
    headers = {"x-forwarded-for": forwarded} if forwarded is not None else {}
    return SimpleNamespace(
        headers=headers,
        client=SimpleNamespace(host=peer) if peer else None,
        app=SimpleNamespace(state=SimpleNamespace(wf=SimpleNamespace(settings=SimpleNamespace(env=env)))),
    )


def test_the_last_hop_is_the_address_api_gateway_saw() -> None:
    assert client_ip(_request("dev", "198.51.100.7")) == "198.51.100.7"
    # A caller who writes its own chain gets nothing from it: API Gateway appended the real address.
    assert client_ip(_request("dev", "1.2.3.4, 5.6.7.8, 203.0.113.50")) == "203.0.113.50"
    assert client_ip(_request("demo", " 9.9.9.9 ,  203.0.113.51 ")) == "203.0.113.51"


@pytest.mark.parametrize("forwarded", ["", " ", "1.2.3.4,", ","])
def test_an_empty_last_hop_falls_back_to_the_connection(forwarded: str) -> None:
    assert client_ip(_request("dev", forwarded)) == "10.0.0.9"


def test_locally_the_connection_is_used_and_a_missing_peer_is_unknown() -> None:
    assert client_ip(_request("local", "203.0.113.50")) == "10.0.0.9"
    assert client_ip(_request("test", "203.0.113.50")) == "10.0.0.9"
    assert client_ip(_request("dev", None, peer=None)) == "unknown"
