"""CF-14: RFC 9728 protected-resource metadata (DD §12.3)."""

from __future__ import annotations

import httpx

from tests.conftest import ISSUER, RESOURCE


async def test_cf14_prm_documents_match_canonical_resource(client: httpx.AsyncClient) -> None:
    for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
        r = await client.get(path)
        assert r.status_code == 200, path
        doc = r.json()
        assert doc["resource"] == RESOURCE
        assert doc["authorization_servers"] == [ISSUER]
        assert doc["scopes_supported"] == ["watchfloor.read", "watchfloor.write"]
        assert doc["bearer_methods_supported"] == ["header"]
        assert "watchfloor.agent.read" not in doc["scopes_supported"]
