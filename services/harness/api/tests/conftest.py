"""Fakes for the harness tests: an authorization server, an MCP server and a scripted model."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from harness_api.app import create_app
from harness_api.config import HarnessConfig
from starlette.applications import Starlette
from strands.models.model import Model
from watchfloor_core.clock import Clock
from watchfloor_core.settings import Settings

ISSUER = "http://auth.test"
MCP_URL = "http://mcp.test/mcp"
REDIRECT = "http://harness.test/oauth/callback"
HARBOR = "dl_01J9HARBORPINE00000000000"
BOARD_HTML = "<!doctype html><title>Triage board</title>"
BRIEFING = "Three deals need you today. Kestrel Biotech is At Risk at 71."
EXPLAIN = "Harbor & Pine is At Risk at 66. No two-way contact for 11 days."

TOOLS = [
    {
        "name": "get_risk_briefing",
        "description": "Use when the user asks what needs attention.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "_meta": {"ui": {"resourceUri": "ui://watchfloor/triage-board"}},
    },
    {
        "name": "lookup_deal",
        "description": "Find a deal by name.",
        "inputSchema": {"type": "object", "properties": {"name_query": {"type": "string"}}},
    },
    {
        "name": "explain_deal_risk",
        "description": "Why a deal is at risk.",
        "inputSchema": {"type": "object", "properties": {"deal_id": {"type": "string"}}},
    },
    {
        "name": "start_deep_triage",
        "description": "Start a deep review.",
        "inputSchema": {"type": "object", "properties": {"deal_id": {"type": "string"}}},
    },
    {
        "name": "list_standing_orders",
        "description": "List standing orders.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_standing_order",
        "description": "Keep watching for something.",
        "inputSchema": {"type": "object", "properties": {"min_score": {"type": "integer"}}},
        "annotations": {"destructiveHint": False},
    },
    {
        "name": "propose_action",
        "description": "Preview a change.",
        "inputSchema": {"type": "object", "properties": {"deal_id": {"type": "string"}}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "confirm_action",
        "description": "Carry out a previewed change.",
        "inputSchema": {"type": "object", "properties": {"confirmation_token": {"type": "string"}}},
        "annotations": {"destructiveHint": True, "idempotentHint": True},
    },
    {
        "name": "undo_action",
        "description": "Reverse a change just confirmed.",
        "inputSchema": {"type": "object", "properties": {"confirmation_token": {"type": "string"}}},
        "annotations": {"destructiveHint": True, "idempotentHint": True},
    },
]
for _read in ("get_risk_briefing", "lookup_deal", "explain_deal_risk", "list_standing_orders"):
    next(t for t in TOOLS if t["name"] == _read)["annotations"] = {"readOnlyHint": True}
PREVIEW = "I'll create a task for Maya to call the CFO by Friday. Should I go ahead?"
DONE = "Done. Maya has a task to call the CFO by Friday."


def _in(seconds: int) -> str:
    """An ISO time with its offset, as the server's ``local_iso`` writes it."""
    return (Clock().now() + timedelta(seconds=seconds)).isoformat(timespec="seconds")


@dataclass
class FakeAuth:
    """The parts of the authorization server the harness calls: /token and /revoke."""

    tokens: dict[str, str] = field(default_factory=dict)  # form fields of the last /token call
    revoked: list[str] = field(default_factory=list)
    refusals: set[str] = field(default_factory=set)  # grant types to refuse
    issued: int = 0
    expires_in: int = 3600

    def __call__(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if request.url.path == "/revoke":
            self.revoked.append(form["token"])
            return httpx.Response(200)
        assert request.url.path == "/token"
        self.tokens = form
        if form["grant_type"] in self.refusals:
            return httpx.Response(400, json={"error": "invalid_grant"})
        self.issued += 1
        return httpx.Response(
            200,
            json={
                "access_token": f"at{self.issued}",
                "token_type": "Bearer",
                "expires_in": self.expires_in,
                "refresh_token": f"rt{self.issued}",
            },
        )


@dataclass
class FakeMcp:
    valid: set[str] = field(default_factory=lambda: {"at1", "at2", "at3"})
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    headers: list[httpx.Headers] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.headers.append(request.headers)
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if token not in self.valid:
            return httpx.Response(401, headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})
        rpc = json.loads(request.content)
        method, params = rpc["method"], rpc.get("params", {})
        if method == "tools/list":
            result: dict[str, Any] = {"tools": TOOLS}
        elif method == "resources/read":
            result = {"contents": [{"uri": params["uri"], "mimeType": "text/html", "text": BOARD_HTML}]}
        else:
            name, args = params["name"], params.get("arguments", {})
            self.calls.append((name, args))
            result = {
                "content": [{"type": "text", "text": "ok"}],
                "structuredContent": self.answer(name, args),
            }
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": rpc["id"], "result": result})

    def answer(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "get_risk_briefing":
            return {"summary": BRIEFING, "deals": []}
        if name == "lookup_deal":
            if "twin" in args["name_query"].lower():  # two deals, no clear winner
                return {
                    "summary": "I found 2 matching deals: Twin Alpha; Twin Beta. Which one do you mean?",
                    "candidates": [
                        {"deal_id": HARBOR, "name": "Twin Alpha", "account": "Twin"},
                        {"deal_id": "dl_other", "name": "Twin Beta", "account": "Twin"},
                    ],
                }
            hit = "harbor" in args["name_query"].lower()
            cands = [{"deal_id": HARBOR, "name": "Harbor & Pine Platform", "account": "Harbor & Pine"}]
            return {
                "summary": "Found Harbor & Pine." if hit else "No match.",
                "candidates": cands if hit else [],
            }
        if name == "explain_deal_risk":
            return {"summary": EXPLAIN}
        if name == "start_deep_triage":
            return {"summary": "I've started a deep review of Harbor & Pine.", "job_id": "job_01X"}
        if name == "propose_action":
            return {"summary": PREVIEW, "confirmation_token": "tok_1", "expires_at": _in(300)}
        if name == "confirm_action":
            return {"summary": DONE, "status": "executed", "undo_until": _in(30)}
        if name == "undo_action":
            return {"summary": "Undone. The task is gone.", "status": "undone"}
        if name == "set_standing_order":
            return {"summary": "Saved. I'll watch for deals under health 35.", "rule_id": "rule_1"}
        return {"summary": "You have one standing order."}

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]


def make_config(tmp_path: Path, **core: Any) -> HarnessConfig:
    settings = Settings(
        **{
            "env": "test",
            "issuer": ISSUER,
            "mcp_url": MCP_URL,
            "resource_uri": MCP_URL,
            "harness_redirect_uri": REDIRECT,
            **core,
        }
    )
    return HarnessConfig(core=settings, session_secret="s" * 40, web_url="/", web_dist=tmp_path / "dist")


@pytest.fixture
def auth() -> FakeAuth:
    return FakeAuth()


@pytest.fixture
def mcp() -> FakeMcp:
    return FakeMcp()


@pytest.fixture
def app(tmp_path: Path, auth: FakeAuth, mcp: FakeMcp) -> Starlette:
    return create_app(
        make_config(tmp_path),
        auth_transport=httpx.MockTransport(auth),
        mcp_transport=httpx.MockTransport(mcp),
        voice=None,  # the browser speaks: a WF_POLLY_VOICE in the developer's environment never reaches AWS
    )


@pytest.fixture
async def client(app: Starlette) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://harness.test") as c:
        yield c


async def sign_in(client: httpx.AsyncClient) -> None:
    """Login → (the browser signs in at the auth server) → callback with a code."""
    r = await client.get("/oauth/login")
    state = parse_qs(r.headers["location"].split("?", 1)[1])["state"][0]
    r = await client.get("/oauth/callback", params={"code": "c1", "state": state, "iss": ISSUER})
    assert r.status_code == 303 and r.headers["location"] == "/"


class ScriptedModel(Model):
    """A Strands model that calls one tool, then speaks the tool's summary.

    ``thinking`` prefixes the reply with a ``<thinking>`` block, the way Amazon Nova answers.
    """

    def __init__(
        self, tool: str, arguments: dict[str, Any] | None = None, thinking: str = "", silent: bool = False
    ) -> None:
        """``silent``: end the turn with an empty message after the tool result, as Gemini sometimes does."""
        self.tool, self.arguments, self.thinking, self.silent = tool, arguments or {}, thinking, silent
        self.seen: list[list[dict[str, Any]]] = []

    def update_config(self, **_: Any) -> None:
        pass

    def get_config(self) -> dict[str, Any]:
        return {}

    async def structured_output(self, *a: Any, **k: Any) -> Any:  # pragma: no cover - unused
        raise NotImplementedError
        yield

    async def stream(
        self, messages: list[dict[str, Any]], *a: Any, **k: Any
    ) -> AsyncIterator[dict[str, Any]]:
        self.seen.append(messages)
        results = [b["toolResult"] for b in messages[-1]["content"] if "toolResult" in b]
        yield {"messageStart": {"role": "assistant"}}
        if not results:
            start = {"toolUse": {"toolUseId": "t1", "name": self.tool}}
            yield {"contentBlockStart": {"start": start}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(self.arguments)}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
            return
        if self.silent:
            yield {"messageStop": {"stopReason": "end_turn"}}
            return
        payload = json.loads(results[0]["content"][0]["text"])
        prefix = f"<thinking>{self.thinking}</thinking>\n" if self.thinking else ""
        yield {"contentBlockDelta": {"delta": {"text": prefix + payload["summary"]}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}
