"""A thin MCP 2025-11-25 client over Streamable HTTP, as Alexa+ uses it (stateless, JSON responses).

Plain JSON-RPC with ``httpx``: the server is stateless (ADR-001), so there is no
``initialize`` handshake to keep and no session id. Every call is timed for the
harness's latency drawer, and a 401 surfaces as :class:`Unauthorized` so the
API can refresh the token or sign the user out.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

PROTOCOL_VERSION = "2025-11-25"
TIMEOUT_S = 15.0


class Unauthorized(Exception):
    """The MCP server rejected the access token."""


class McpError(Exception):
    """A transport or JSON-RPC error (not a tool error: those come back as results)."""


class RateLimited(McpError):
    """The MCP server's per-user limit (HTTP 429): busy, not down. ``retry_after`` is in seconds."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"rate limited by the MCP server; try again in {retry_after} seconds")
        self.retry_after = retry_after


def _retry_after(value: str | None) -> int:
    try:
        return min(max(int(value or ""), 1), 60)
    except ValueError:
        return 1


@dataclass
class ToolCall:
    tool: str
    arguments: dict[str, Any]
    ms: int
    ok: bool
    structured: dict[str, Any] | None
    text: str
    ui_uri: str | None = None

    @property
    def summary(self) -> str:
        """What the tool wants said aloud: its ``summary``, else the error message, else the text."""
        data = self.structured or {}
        if isinstance(data.get("summary"), str):
            return data["summary"]
        error = data.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        return self.text

    def public(self) -> dict[str, Any]:
        return {"tool": self.tool, "arguments": self.arguments, "ms": self.ms, "ok": self.ok}


@dataclass
class McpHttp:
    url: str
    token: str
    transport: httpx.AsyncBaseTransport | None = None
    calls: list[ToolCall] = field(default_factory=list)
    _ui: dict[str, str] = field(default_factory=dict)
    _read_only: set[str] = field(default_factory=set)
    _rid: int = 0
    # A shared client from the app (kept alive across requests), or one of our own for this turn.
    # Building an httpx client costs hundreds of milliseconds on Windows (SSL context), so the app shares one.
    http: httpx.AsyncClient | None = None
    _owned: httpx.AsyncClient | None = None

    async def aclose(self) -> None:
        """Close the client this object created; a shared one belongs to the app."""
        if self._owned is not None:
            await self._owned.aclose()
            self._owned = None

    def _client(self) -> httpx.AsyncClient:
        if self.http is not None:
            return self.http
        if self._owned is None:
            self._owned = httpx.AsyncClient(transport=self.transport, timeout=TIMEOUT_S)
        return self._owned

    async def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._rid += 1
        http = self._client()
        try:
            r = await http.post(
                self.url,
                json={"jsonrpc": "2.0", "id": self._rid, "method": method, "params": params},
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": PROTOCOL_VERSION,
                },
            )
        except httpx.HTTPError as exc:
            raise McpError(f"MCP server unreachable ({type(exc).__name__})") from None
        if r.status_code == 401:
            raise Unauthorized(r.headers.get("www-authenticate", ""))
        if r.status_code == 429:
            raise RateLimited(_retry_after(r.headers.get("retry-after")))
        if r.status_code != 200:
            raise McpError(f"{method}: HTTP {r.status_code}")
        try:
            body = r.json()
        except ValueError:
            raise McpError(f"{method}: response is not JSON") from None
        if not isinstance(body, dict):
            raise McpError(f"{method}: response is not a JSON-RPC object")
        if "error" in body:
            err = body["error"]
            message = err.get("message") if isinstance(err, dict) else None
            raise McpError(f"{method}: {message if isinstance(message, str) else 'error'}")
        result = body.get("result")
        if not isinstance(result, dict):
            raise McpError(f"{method}: no result")
        return result

    async def list_tools(self) -> list[dict[str, Any]]:
        tools = (await self._rpc("tools/list", {})).get("tools", [])
        for t in tools:
            uri = ((t.get("_meta") or {}).get("ui") or {}).get("resourceUri")
            if uri:
                self._ui[t["name"]] = uri
            if (t.get("annotations") or {}).get("readOnlyHint") is True:
                self._read_only.add(t["name"])
        return tools

    def writes(self, tool: str) -> bool:
        """Whether a call can change something: any tool not listed as ``readOnlyHint: true``."""
        return tool not in self._read_only

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolCall:
        t0 = time.perf_counter()
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        ms = int((time.perf_counter() - t0) * 1000)
        text = " ".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        structured = result.get("structuredContent")
        call = ToolCall(
            tool=name,
            arguments=arguments,
            ms=ms,
            ok=not result.get("isError", False),
            structured=structured if isinstance(structured, dict) else None,
            text=text.strip(),
            ui_uri=self._ui.get(name),
        )
        self.calls.append(call)
        return call

    async def read_resource(self, uri: str) -> str | None:
        contents = (await self._rpc("resources/read", {"uri": uri})).get("contents", [])
        for c in contents:
            if isinstance(c.get("text"), str):
                return c["text"]
        return None
