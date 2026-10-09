"""Tiny JSON-RPC-over-HTTP helper for driving the in-process MCP server in tests."""

from __future__ import annotations

from itertools import count
from typing import Any

import httpx

PROTOCOL_VERSION = "2025-11-25"
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": PROTOCOL_VERSION,
}


class Mcp:
    """One caller (one bearer token) talking to ``POST /mcp``."""

    def __init__(self, client: httpx.AsyncClient, token: str | None) -> None:
        self.client = client
        self.token = token
        self._ids = count(1)

    def headers(self, **extra: str) -> dict[str, str]:
        h = dict(MCP_HEADERS)
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        h.update(extra)
        return h

    async def post(self, payload: Any, **headers: str) -> httpx.Response:
        return await self.client.post("/mcp", json=payload, headers=self.headers(**headers))

    async def rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a request and return the JSON-RPC envelope (``result`` or ``error``)."""
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": next(self._ids), "method": method}
        if params is not None:
            msg["params"] = params
        r = await self.post(msg)
        assert r.status_code == 200, f"{method}: HTTP {r.status_code} {r.text}"
        return r.json()

    async def tools(self) -> list[dict[str, Any]]:
        env = await self.rpc("tools/list")
        return env["result"]["tools"]

    async def call_raw(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.rpc("tools/call", {"name": name, "arguments": arguments or {}})

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call a tool and return its CallToolResult, failing on JSON-RPC errors."""
        env = await self.call_raw(name, arguments)
        assert "result" in env, f"{name} returned a JSON-RPC error: {env.get('error')}"
        return env["result"]

    async def ok(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call a tool that must succeed and return its structuredContent."""
        result = await self.call(name, arguments)
        assert not result.get("isError"), f"{name} failed: {result['structuredContent']}"
        return result["structuredContent"]
