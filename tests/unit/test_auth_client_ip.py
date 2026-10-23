"""Which address the auth server rate-limits: the one the proxy in front saw, never the one the caller wrote."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import uvicorn
from auth_server.context import client_ip

CADDYFILE = Path(__file__).resolve().parents[2] / "deploy" / "render" / "Caddyfile"


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


def test_render_caddy_sends_one_resolved_address() -> None:
    """Behind Render's edge Caddy must not append its peer (every caller would share the edge's address)
    nor pass the leftmost hop (the caller's own): strict mode resolves the rightmost untrusted hop and
    header_up replaces the header with it, on every route."""
    text = CADDYFILE.read_text(encoding="utf-8")
    assert re.search(r"^\s*trusted_proxies_strict\s*$", text, re.M)
    proxies = re.findall(r"reverse_proxy 127\.0\.0\.1:(\d+) \{\n(.*?)\n\s*\}\n", text, re.S)
    assert sorted(port for port, _ in proxies) == ["8080", "8081", "8082"]
    for port, body in proxies:
        assert "header_up X-Forwarded-For {client_ip}" in body, port


async def test_uvicorn_makes_the_forwarded_address_the_client_behind_loopback() -> None:
    """start.sh runs uvicorn with its defaults, which trust 127.0.0.1: Caddy's one address becomes
    request.client, and that is what client_ip uses where env is local."""
    seen: list[Any] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope["client"])

    config = uvicorn.Config(app)
    config.load()
    scope = {
        "type": "http",
        "client": ("127.0.0.1", 50000),
        "headers": [(b"x-forwarded-for", b"203.0.113.60")],
    }
    await config.loaded_app(scope, None, None)
    assert seen[-1][0] == "203.0.113.60"
    await config.loaded_app({**scope, "client": ("198.51.100.1", 50000)}, None, None)
    assert seen[-1][0] == "198.51.100.1"  # a caller that is not loopback cannot claim an address
