"""One chat turn (DD §17): a Strands agent with an Alexa-style prompt, over the user's MCP token.

Every MCP tool from ``tools/list`` becomes a Strands tool that calls the server
through :class:`McpHttp`, so each call is timed and any ``ui://`` view it points
to can be shown in the browser. The models are tried in order, Bedrock then Gemini
(ADR-023). Without one, or when none answers, the offline keyword router answers
instead, and the turn says so. A model that failed after changing something is not
followed by another, which would make the change again: the turn reports what was
done and stops.

Between turns only ``{role, text}`` lines travel, so a preview's confirmation token
would be lost before the user can say yes. :class:`Pending` carries it to the next
turn (the app keeps it in an encrypted cookie), and code, not the prompt, decides
when ``confirm_action`` may run: never in the turn that made the preview, and only
with the token of the preview waiting for the user's answer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any, Literal

import httpx
from watchfloor_core import gemini
from watchfloor_core.bedrock import strip_reasoning, tool_use_options
from watchfloor_core.usage import Tally

from .config import HarnessConfig, Misconfigured
from .control import (
    ASK_NOTE,
    CONTROL_DESCRIPTION,
    GATED_WRITES,
    Screen,
    TurnState,
    clean,
    control_result,
    control_schema,
    deadline,
    describe_write,
    fingerprint,
    screen_note,
)
from .mcp_http import McpError, McpHttp, ToolCall, Unauthorized
from .offline import offline_turn

log = logging.getLogger(__name__)

PROMPTS = Path(__file__).resolve().parent / "prompts"
TURN_TIMEOUT_S = 25.0  # under API Gateway's 30 s limit, so a slow turn gets our 504, not the gateway's
UI_CACHE_S = 300.0  # a view's HTML only changes with a deploy: keep it rather than refetch ~280 KB a turn
MAX_TURNS = 6
MAX_HISTORY = 12
MAX_TOKENS = 1024  # room for Nova's <thinking> before a tool call; the prompt keeps replies short
GEMINI_MAX_TOKENS = 2048  # Gemini counts any thinking against the output budget
NO_REPLY = "I couldn't answer that just now. Please try again."
STOPPED = "I couldn't finish the rest of that, so I stopped rather than make the change twice."

PREVIEW_TOOLS = frozenset({"propose_action", "log_deal_update"})
SAME_TURN = (
    "The user hasn't answered this preview yet. Read it back, ask whether to go ahead, "
    "and confirm only after they say yes."
)
NOT_PENDING = "That isn't the preview waiting for the user's answer. Propose the change again."

Role = Literal["user", "assistant"]


@dataclass
class Pending:
    """A preview waiting for the user's answer, or an action they just confirmed (its undo window)."""

    token: str
    summary: str
    state: Literal["preview", "confirmed", "ask"]
    until: float  # epoch seconds: the preview's expiry, or the end of the undo window
    # "ask" only: a direct write held back until the user says yes (control.GATED_WRITES).
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)

    def live(self) -> bool:
        return self.until > time.time()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"t": self.token, "s": self.summary, "k": self.state, "u": self.until}
        if self.state == "ask":
            out |= {"n": self.tool, "a": self.args}
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Pending | None:
        try:
            p = cls(str(data["t"]), str(data["s"]), data["k"], float(data["u"]))  # type: ignore[index]
            if p.state == "ask":
                p.tool, p.args = str(data["n"]), dict(data["a"])  # type: ignore[index]
        except (TypeError, KeyError, ValueError):
            return None
        if p.state not in ("preview", "confirmed", "ask") or not p.live():
            return None
        # A sealed cookie is ours, but a held write is only ever one of the gated tools, with
        # the fingerprint of exactly these arguments.
        if p.state == "ask" and (p.tool not in GATED_WRITES or p.token != fingerprint(p.tool, p.args)):
            return None
        return p

    def note(self) -> str:
        """What the model is told at the start of the turn.

        The summary comes from a tool result, so it can carry CRM text: it goes in as one quoted line,
        with quotes and line breaks flattened, never as a new instruction.
        """
        summary = " ".join(self.summary.replace('"', "'").split())
        if self.state == "ask":
            return (
                f'The user was just asked to confirm this change: "{summary}". If their latest '
                f"message clearly says yes, call {self.tool} again with exactly these arguments: "
                f"{json.dumps(self.args, ensure_ascii=False)}. If they say no, change the subject or "
                "want something different, don't call it."
            )
        if self.state == "preview":
            return (
                f'A change is waiting for the user\'s answer. Its preview: "{summary}" '
                "If their latest message clearly says yes, call confirm_action with "
                f'confirmation_token "{self.token}". If they want more time, call extend_preview '
                "with that token. If they say no or move on, leave it."
            )
        return (
            f'The user just confirmed this: "{summary}" '
            f'If they ask to undo it, call undo_action with confirmation_token "{self.token}".'
        )


def _epoch(value: Any, default_s: float) -> float:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return time.time() + default_s


def next_pending(
    pending: Pending | None, calls: list[ToolCall], asked: Pending | None = None
) -> Pending | None:
    """What waits for the next turn, after this turn's successful calls, in order.

    ``asked`` is a direct write this turn held back for a yes. A write that was asked about gets one
    turn to be answered: it is gone after the next turn whether or not the user said yes.
    """
    if pending is not None and pending.state == "ask":
        pending = None
    for c in calls:
        data = c.structured or {}
        token = data.get("confirmation_token") or c.arguments.get("confirmation_token")
        if not c.ok or not isinstance(token, str):
            continue
        if c.tool in PREVIEW_TOOLS:
            pending = Pending(token, c.summary[:300], "preview", _epoch(data.get("expires_at"), 300))
        elif c.tool == "extend_preview" and pending and pending.token == token:
            pending = Pending(token, pending.summary, "preview", _epoch(data.get("expires_at"), 300))
        elif c.tool == "confirm_action":
            undo = data.get("undo_until")
            pending = Pending(token, c.summary[:300], "confirmed", _epoch(undo, 30)) if undo else None
        elif c.tool == "undo_action":
            pending = None
    if asked is not None:
        pending = asked
    return pending if pending and pending.live() else None


@dataclass
class TurnResult:
    reply: str
    engine: str
    calls: list[ToolCall]
    ui: dict[str, str] | None = None
    pending: Pending | None = None  # for the app's cookie, never sent to the browser
    actions: list[dict[str, Any]] = field(default_factory=list)  # for the page (control.py)

    def public(self) -> dict[str, Any]:
        return {
            "reply": self.reply,
            "engine": self.engine,
            "calls": [c.public() for c in self.calls],
            "ui": self.ui,
            "actions": self.actions,
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
    """Remembers a 401 seen inside a tool (Strands would make it a tool error), and gates confirmation."""

    unauthorized: bool = False
    errors: list[str] = field(default_factory=list)
    pending_token: str | None = None  # the preview the user was asked about in an earlier turn
    issued: set[str] = field(default_factory=set)  # tokens of previews made in this turn
    ask: Pending | None = None  # a direct write the user was asked about in an earlier turn
    state: TurnState = field(default_factory=TurnState)

    def refuse_confirm(self, token: Any) -> str | None:
        if token in self.issued:
            return SAME_TURN
        if not token or token != self.pending_token:
            return NOT_PENDING
        return None

    def hold_write(self, tool: str, arguments: dict[str, Any]) -> str | None:
        """What to tell the model when a direct write must wait for a yes; None when it may run.

        It may run only with the arguments of the write the user was asked about in an earlier turn
        (one use), so a model that asks and "confirms" in the same breath is held too.
        """
        fp = fingerprint(tool, arguments)
        if self.ask is not None and self.ask.token == fp and fp not in self.issued:
            self.ask = None
            return None
        self.issued.add(fp)
        self.state.asked = Pending(fp, describe_write(tool, arguments), "ask", deadline(), tool, arguments)
        return ASK_NOTE


def mcp_tool(mcp: McpHttp, spec: dict[str, Any], guard: _Guard) -> Any:
    from strands.tools.tools import PythonAgentTool

    name = spec["name"]

    async def run(tool_use: dict[str, Any], **_: Any) -> dict[str, Any]:
        def result(status: str, text: str) -> dict[str, Any]:
            return {"toolUseId": tool_use["toolUseId"], "status": status, "content": [{"text": text}]}

        arguments = dict(tool_use.get("input") or {})
        if name == "confirm_action" and (
            refusal := guard.refuse_confirm(arguments.get("confirmation_token"))
        ):
            log.warning("confirm_action refused before reaching the server: %s", refusal[:40])
            return result("error", refusal)
        if name in GATED_WRITES and (held := guard.hold_write(name, arguments)):
            log.info("%s held for the user's yes", name)
            return result("success", json.dumps({"summary": held, "status": "needs_confirmation"}))
        try:
            call = await mcp.call_tool(name, arguments)
        except Unauthorized:
            guard.unauthorized = True
            return result("error", "The user's session has expired.")
        except McpError as exc:
            guard.errors.append(str(exc))
            return result("error", str(exc))
        token = (call.structured or {}).get("confirmation_token")
        if call.ok and name in PREVIEW_TOOLS and isinstance(token, str):
            guard.issued.add(token)
        payload = call.structured if call.structured is not None else call.text
        return result("success" if call.ok else "error", json.dumps(payload, ensure_ascii=False))

    run.__name__ = name
    tool_spec = {
        "name": name,
        "description": spec.get("description", ""),
        "inputSchema": {"json": spec.get("inputSchema") or {"type": "object", "properties": {}}},
    }
    return PythonAgentTool(name, tool_spec, run)


def control_tool(guard: _Guard) -> Any:
    """``control_app``: the page actions the model asks for are checked, collected, and sent to the page."""
    from strands.tools.tools import PythonAgentTool

    async def run(tool_use: dict[str, Any], **_: Any) -> dict[str, Any]:
        def result(status: str, payload: dict[str, Any]) -> dict[str, Any]:
            text = json.dumps(payload, ensure_ascii=False)
            return {"toolUseId": tool_use["toolUseId"], "status": status, "content": [{"text": text}]}

        act = clean(tool_use.get("input"))
        if isinstance(act, str):
            return result("error", {"summary": act})
        if (full := guard.state.sink.add(act)) is not None:
            return result("error", {"summary": full})
        return result("success", control_result(act))

    run.__name__ = "control_app"
    spec = {
        "name": "control_app",
        "description": CONTROL_DESCRIPTION,
        "inputSchema": {"json": control_schema()},
    }
    return PythonAgentTool("control_app", spec, run)


def bedrock_model(cfg: HarnessConfig) -> Any:
    from botocore.config import Config as BotocoreConfig
    from strands.models import BedrockModel

    model_id = cfg.core.haiku_model_id
    if not model_id:
        raise Misconfigured("WF_HAIKU_MODEL_ID is not set")
    # Two quick retries (standard mode, jittered, about 3 s at most) ride out a brief throttle.
    client = BotocoreConfig(
        retries={"mode": "standard", "total_max_attempts": 3}, connect_timeout=5, read_timeout=TURN_TIMEOUT_S
    )
    return BedrockModel(
        model_id=model_id,
        max_tokens=MAX_TOKENS,
        boto_client_config=client,
        **tool_use_options(model_id, 0.5),
    )


def gemini_model(cfg: HarnessConfig) -> Any:
    from strands.models.gemini import GeminiModel

    key = gemini.api_key(cfg.core)
    model_id = gemini.model_id(cfg.core, "haiku")  # the fast tier (WF_GEMINI_HAIKU_MODEL_ID, if set)
    if not key or not model_id:
        raise Misconfigured("no Gemini model id or API key")
    # Gemini keeps its default temperature: Google advises against lowering it for tool use.
    return GeminiModel(
        client_args={"api_key": key, "http_options": {"timeout": int(TURN_TIMEOUT_S * 1000)}},
        model_id=model_id,
        params={"max_output_tokens": GEMINI_MAX_TOKENS},
    )


ModelFactory = Callable[[HarnessConfig], Any]


def model_chain(cfg: HarnessConfig) -> list[tuple[str, ModelFactory]]:
    """The models a turn tries, in order: Bedrock while models are on, then Gemini (ADR-023)."""
    chain: list[tuple[str, ModelFactory]] = []
    if cfg.core.models_enabled and cfg.core.haiku_model_id:
        chain.append(("bedrock", bedrock_model))
    if gemini.configured(cfg.core):
        chain.append(("gemini", gemini_model))
    return chain


def _model_id(cfg: HarnessConfig, name: str) -> str:
    """For the usage log: the id behind a chain entry (no key, ever)."""
    if name == "bedrock":
        return cfg.core.haiku_model_id or name
    if name == "gemini":
        return gemini.model_id(cfg.core, "haiku") or name
    return name


async def strands_turn(
    mcp: McpHttp,
    cfg: HarnessConfig,
    history: list[tuple[Role, str]],
    message: str,
    *,
    model_factory: Callable[[HarnessConfig], Any] = bedrock_model,
    pending: Pending | None = None,
    usage: Tally | None = None,
    screen: Screen | None = None,
    state: TurnState | None = None,
) -> str:
    """``state`` collects what the model asks of the page and any write held for a yes (control.py)."""
    from strands import Agent

    preview = pending if pending and pending.state == "preview" else None
    guard = _Guard(
        pending_token=preview.token if preview else None,
        ask=pending if pending and pending.state == "ask" else None,
        state=state if state is not None else TurnState(),
    )
    tools = [mcp_tool(mcp, spec, guard) for spec in await mcp.list_tools()]
    tools.append(control_tool(guard))
    prompt = system_prompt() + screen_note(screen) + (f"\n\n{pending.note()}" if pending else "")
    agent = Agent(
        model=model_factory(cfg),
        tools=tools,
        system_prompt=prompt,
        messages=to_messages(history),
        callback_handler=None,
        name="alexa-sim",
        # Strands' own backoff (4 s, doubling to 64 s) outlasts the turn. Without it, a throttled
        # model fails at once and the offline engine answers (run_turn).
        retry_strategy=None,
    )
    try:
        result = await asyncio.wait_for(
            agent.invoke_async(message, limits={"turns": MAX_TURNS}), TURN_TIMEOUT_S
        )
    finally:  # a failed or slow model still spent tokens
        if usage is not None:
            usage.add(agent.event_loop_metrics)
    if guard.unauthorized:
        raise Unauthorized("token rejected during the turn")
    return strip_reasoning(str(result)) or NO_REPLY


async def run_turn(
    cfg: HarnessConfig,
    token: str,
    message: str,
    history: list[tuple[Role, str]],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    http: httpx.AsyncClient | None = None,
    model_factory: ModelFactory | None = None,
    ui_cache: dict[str, tuple[float, str]] | None = None,
    pending: Pending | None = None,
    screen: Screen | None = None,
) -> TurnResult:
    """``model_factory`` replaces the model chain (tests). ``pending`` comes from the previous turn.

    ``screen`` is what the page shows now; the model sees it, and its ``control_app`` actions come
    back in ``TurnResult.actions`` for the page to run.
    """
    engine = cfg.engine
    if engine is None:
        raise Misconfigured(
            f"a model (WF_HAIKU_MODEL_ID or WF_GEMINI_MODEL_ID) must be set in {cfg.core.env}"
        )
    mcp = McpHttp(cfg.core.mcp_url, token, transport=transport, http=http)
    usage = Tally()
    tried: list[str] = []
    state = TurnState()
    try:
        reply: str | None = None
        if engine == "strands":
            chain = [("model", model_factory)] if model_factory else model_chain(cfg)
            for name, factory in chain:
                tried.append(_model_id(cfg, name))
                start = len(mcp.calls)
                state = TurnState()  # a model that failed midway leaves no page changes behind
                try:
                    reply = await strands_turn(
                        mcp,
                        cfg,
                        history,
                        message,
                        model_factory=factory,
                        pending=pending,
                        usage=usage,
                        screen=screen,
                        state=state,
                    )
                    log.info("the %s model answered", name)
                    break
                except (Unauthorized, McpError):
                    raise
                except Exception as exc:  # throttled, refused, no key or out of time
                    log.warning("%s model turn failed (%s)", name, type(exc).__name__)
                    wrote = [c for c in mcp.calls[start:] if c.ok and mcp.writes(c.tool)]
                    if wrote:  # another model would make the same change again: say what was done
                        reply = f"{wrote[-1].summary} {STOPPED}"
                        break
                    if isinstance(exc, TimeoutError):
                        raise  # the turn's time is spent: no other model gets a fresh budget
                    # otherwise the next model, then the router
            if reply is None:
                engine = "offline"
                state = TurnState()
                reply = await offline_turn(mcp, message, history, pending)
        else:
            await mcp.list_tools()  # learns which tools have a ui:// view, like the agent path
            reply = await offline_turn(mcp, message, history, pending)
        return TurnResult(
            reply=reply,
            engine=engine,
            calls=mcp.calls,
            ui=await _view(mcp, ui_cache),
            pending=next_pending(pending, mcp.calls, state.asked),
            actions=state.sink.actions,
        )
    finally:
        usage.log(scope="harness", engine=engine, model=",".join(tried))
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
            # The HTML itself stays here, in the cache: the page's iframe loads it from /api/view, where
            # it gets its own policy, so ~280 KB no longer rides along with every reply.
            data = json.dumps(call.structured or {}, ensure_ascii=False)
            return {"uri": call.ui_uri, "tool": call.tool, "data": data}
    return None
