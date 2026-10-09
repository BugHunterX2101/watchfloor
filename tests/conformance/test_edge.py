"""Edge security cases CF-12 and CF-13 plus token validation (DD §5.2, §22.3). No database needed."""

from __future__ import annotations

import httpx
from cryptography.hazmat.primitives.asymmetric import ec

from tests.conftest import AGENT_SCOPE, TokenFactory
from tests.mcp_client import MCP_HEADERS, Mcp

LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


def _no_www_authenticate(r: httpx.Response) -> bool:
    return "www-authenticate" not in {k.lower() for k in r.headers}


async def test_cf12_disallowed_origin_is_403(mcp: Mcp) -> None:
    r = await mcp.post(LIST, Origin="https://evil.example")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == -32600


async def test_cf12_allowlisted_origin_passes(mcp: Mcp) -> None:
    r = await mcp.post(LIST, Origin="https://alexa.amazon.com")
    assert r.status_code == 200


async def test_cf12_bad_host_is_421(mcp: Mcp) -> None:
    r = await mcp.post(LIST, Host="mcp.evil.example")
    assert r.status_code == 421


async def test_cf13_missing_token_is_401_without_www_authenticate(client: httpx.AsyncClient) -> None:
    r = await client.post("/mcp", json=LIST, headers=MCP_HEADERS)
    assert r.status_code == 401
    assert _no_www_authenticate(r)


async def test_invalid_tokens_are_401(client: httpx.AsyncClient, token_factory: TokenFactory) -> None:
    stranger = ec.generate_private_key(ec.SECP256R1())
    bad = {
        "expired": token_factory(expires_in=-120),
        "wrong audience": token_factory(aud="https://other.example/mcp"),
        "wrong issuer": token_factory(iss="https://evil.example"),
        "unknown kid": token_factory(kid="nope"),
        "forged signature": token_factory(key=stranger),
        "no usable scope": token_factory(scope="openid profile"),
        "job token without agent scope": token_factory(scope="watchfloor.read", job="job_x"),
        "garbage": "not.a.jwt",
    }
    for label, token in bad.items():
        r = await client.post("/mcp", json=LIST, headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"})
        assert r.status_code == 401, label
        assert _no_www_authenticate(r), label


async def test_user_token_cannot_claim_agent_scope(
    client: httpx.AsyncClient, token_factory: TokenFactory
) -> None:
    token = token_factory(scope=f"watchfloor.read {AGENT_SCOPE}")
    tools = await Mcp(client, token).tools()
    assert "get_deal_timeline" not in {t["name"] for t in tools}


async def test_body_over_64_kib_is_413(mcp: Mcp) -> None:
    big = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"pad": "x" * 70_000}}
    r = await mcp.post(big)
    assert r.status_code == 413


async def test_public_endpoints_need_no_token(client: httpx.AsyncClient) -> None:
    r = await client.get("/.well-known/oauth-protected-resource")
    assert r.status_code == 200
