"""One chat turn (DD §17): a Strands agent with an Alexa-style prompt, over the user's MCP token.

Every MCP tool from ``tools/list`` becomes a Strands tool that calls the server
through :class:`McpHttp`, so each call is timed and any ``ui://`` view it points
to can be shown in the browser. Without a model id (local and test only) the
offline keyword router answers instead, and the turn says so.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Literal

import httpx

from .config import HarnessConfig, Misconfigured
from .mcp_http import McpError, McpHttp, ToolCall, Unauthorized
from .offline import offline_turn

PROMPTS = Path(__file__).resolve().parent / "prompts"
TURN_TIMEOUT_S = 45.0
UI_CACHE_S = 300.0  # a view's HTML only changes with a deploy: keep it rather than refetch ~280 KB a turn
MAX_TURNS = 6
MAX_HISTORY = 12

Role = Literal["user", "assistant"]


@dataclass
class TurnResult:
    reply: str
    engine: str
    calls: list[ToolCall]
    ui: dict[str, str] | None = None

    def public(self) -> dict[str, Any]:
        return {
            "reply": self.reply,
            "engine": self.engine,
            "calls": [c.public() for c in self.calls],
            "ui": self.ui,
        }


@cache
def system_prompt() -> str:
    return (PROMPTS / "alexa_style.md").read_text(encoding="utf-8")


def to_messages(history: list[tuple[Role, str]]) -> list[dict[str, Any]]:
    """Strands messages from the browser's transcript: user first, roles alternating."""
    out: list[dict[str, Any]] = []
    for role, text in history[-MAX_HISTORY:]:
        if not text.strip() or (not out and role != "user"):
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"][0]["text"] += "\n" + text
        else:
            out.append({"role": role, "content": [{"text": text}]})
    if out and out[-1]["role"] == "user":
        out.pop()  # the new message is the next user turn
    return out


@dataclass
class _Guard:
    """Remembers a 401 seen inside a tool, which Strands would otherwise turn into a tool error."""

    unauthorized: bool = False
    errors: list[str] = field(default_factory=list)


def mcp_tool(mcp: McpHttp, spec: dict[str, Any], guard: _Guard) -> Any:
    from strands.tools.tools import PythonAgentTool

    name = spec["name"]

    async def run(tool_use: dict[str, Any], **_: Any) -> dict[str, Any]:
        def result(status: str, text: str) -> dict[str, Any]:
            return {"toolUseId": tool_use["toolUseId"], "status": status, "content": [{"text": text}]}

        try:
            call = await mcp.call_tool(name, dict(tool_use.get("input") or {}))
        except Unauthorized:
            guard.unauthorized = True
            return result("error", "The user's session has expired.")
        except McpError as exc:
            guard.errors.append(str(exc))
            return result("error", str(exc))
        payload = call.structured if call.structured is not None else call.text
        return result("success" if call.ok else "error", json.dumps(payload, ensure_ascii=False))

    run.__name__ = name
    tool_spec = {
        "name": name,
        "description": spec.get("description", ""),
        "inputSchema": {"json": spec.get("inputSchema") or {"type": "object", "properties": {}}},
    }
    return PythonAgentTool(name, tool_spec, run)


def bedrock_model(cfg: HarnessConfig) -> Any:
    from strands.models import BedrockModel

    if not cfg.core.haiku_model_id:
        raise Misconfigured("WF_HAIKU_MODEL_ID is not set")
    return BedrockModel(model_id=cfg.core.haiku_model_id, temperature=0.5, max_tokens=512)


async def strands_turn(
    mcp: McpHttp,
    cfg: HarnessConfig,
    history: list[tuple[Role, str]],
    message: str,
    *,
    model_factory: Callable[[HarnessConfig], Any] = bedrock_model,
) -> str:
    from strands import Agent

    guard = _Guard()
    tools = [mcp_tool(mcp, spec, guard) for spec in await mcp.list_tools()]
    agent = Agent(
        model=model_factory(cfg),
        tools=tools,
        system_prompt=system_prompt(),
        messages=to_messages(history),
        callback_handler=None,
        name="alexa-sim",
    )
    result = await asyncio.wait_for(agent.invoke_async(message, limits={"turns": MAX_TURNS}), TURN_TIMEOUT_S)
    if guard.unauthorized:
        raise Unauthorized("token rejected during the turn")
    return str(result).strip()


async def run_turn(
    cfg: HarnessConfig,
    token: str,
    message: str,
    history: list[tuple[Role, str]],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    http: httpx.AsyncClient | None = None,
    model_factory: Callable[[HarnessConfig], Any] = bedrock_model,
    ui_cache: dict[str, tuple[float, str]] | None = None,
) -> TurnResult:
    engine = cfg.engine
    if engine is None:
        raise Misconfigured(f"WF_HAIKU_MODEL_ID must be set in {cfg.core.env}")
    mcp = McpHttp(cfg.core.mcp_url, token, transport=transport, http=http)
    try:
        if engine == "offline":
            await mcp.list_tools()  # learns which tools have a ui:// view, like the agent path
            reply = await offline_turn(mcp, message, history)
        else:
            reply = await strands_turn(mcp, cfg, history, message, model_factory=model_factory)
        return TurnResult(reply=reply, engine=engine, calls=mcp.calls, ui=await _view(mcp, ui_cache))
    finally:
        await mcp.aclose()


async def _view(mcp: McpHttp, cache: dict[str, tuple[float, str]] | None = None) -> dict[str, str] | None:
    """The ui:// view of the turn's last successful call that has one, for the sandboxed iframe.

    ``cache`` (the app's, per process) keeps each view's HTML for UI_CACHE_S, keyed by server and URI.
    """
    for call in reversed(mcp.calls):
        if call.ok and call.ui_uri:
            key = f"{mcp.url} {call.ui_uri}"
            hit = cache.get(key) if cache is not None else None
            if hit is not None and hit[0] > time.monotonic():
                html: str | None = hit[1]
            else:
                try:
                    html = await mcp.read_resource(call.ui_uri)
                except (McpError, Unauthorized):
                    return None
                if html is not None and cache is not None:
                    cache[key] = (time.monotonic() + UI_CACHE_S, html)
            if html is None:
                return None
            data = json.dumps(call.structured or {}, ensure_ascii=False)
            return {"uri": call.ui_uri, "html": html, "tool": call.tool, "data": data}
    return None
