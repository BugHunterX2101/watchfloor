"""Alexa+ Tier 1: an mcp:service token discovers the tools of this private server and reads no data."""

from __future__ import annotations

import pytest

from tests.conformance.test_tools_list import USER_TOOLS
from tests.conftest import WS, TokenFactory
from tests.mcp_client import Mcp


def _service(mcp: Mcp, token_factory: TokenFactory, **claims: str) -> Mcp:
    return Mcp(
        mcp.client, token_factory(**{"sub": "svc:wf-alexa", "ws": "", "scope": "mcp:service", **claims})
    )


async def test_a_service_token_lists_what_a_linked_user_sees(mcp: Mcp, token_factory: TokenFactory) -> None:
    service = _service(mcp, token_factory)
    assert [t["name"] for t in await service.tools()] == USER_TOOLS
    assert "result" in await service.rpc("ping")
    assert "result" in await service.rpc("resources/list")


async def test_a_service_token_calls_no_tool(mcp: Mcp, token_factory: TokenFactory) -> None:
    """403, which starts account linking in Alexa+; never a result built from user data."""
    service = _service(mcp, token_factory)
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_risk_briefing"}}
    r = await service.post(msg)
    assert r.status_code == 403 and r.content == b""


@pytest.mark.parametrize(
    "claims",
    [{"ws": WS}, {"scope": "mcp:service watchfloor.read"}, {"job": "job_any"}],
)
async def test_a_service_token_carries_nothing_else(
    mcp: Mcp, token_factory: TokenFactory, claims: dict[str, str]
) -> None:
    r = await _service(mcp, token_factory, **claims).post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
