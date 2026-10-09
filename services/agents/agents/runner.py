"""One deep-triage job, as a stream of events (DD §10.2–10.3).

Contract with ``workers.agentcore_client.final_output``:

- ``{"type": "progress", "step": ...}`` while working;
- exactly one terminal event, either
  ``{"type": "result", "engine": "strands"|"offline", "output": TriageOutput}`` or
  ``{"type": "error", "code": ..., "error": "..."}`` where ``code`` is one of
  ``invalid_payload``, ``misconfigured``, ``timed_out`` (the 90 s budget),
  ``rejected`` (nothing survived the gate), ``data_unavailable`` (an MCP read
  failed) or ``agent_failed``.

The work runs in its own task and reports through a queue, so the 90 s
deadline cancels the work, never the code consuming the stream.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractContextManager
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from watchfloor_core.settings import Settings, get_settings

from .data_tools import Caller, JobData, ToolFailed, mcp_connect
from .gate import Rejected, finalize
from .memory import actor_id, make_recall
from .models import Misconfigured
from .offline import offline_triage
from .schemas import LeadDraft, TriageOutput

log = logging.getLogger("watchfloor.agents")

TIMEOUT_S = 90.0
Engine = Literal["strands", "offline"]
Connector = Callable[[str, str], AbstractContextManager[Caller]]
Lead = Callable[[JobData, Settings, Any], Awaitable[LeadDraft]]
Emit = Callable[[dict[str, Any] | None], None]


class JobIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    job_id: str = Field(pattern=r"^job_[0-9A-Za-z]{1,40}$")
    deal_id: str = Field(pattern=r"^dl_[0-9A-Za-z]{1,40}$")
    mcp_token: str = Field(min_length=10, max_length=4096)
    workspace_id: str | None = None
    user_ref: str | None = None
    trace_id: str | None = None


def pick_engine(s: Settings) -> Engine | None:
    if s.sonnet_model_id and s.haiku_model_id:
        return "strands"
    if s.env in ("local", "test"):
        return "offline"
    return None  # dev and demo never fall back to the offline engine


def _error(code: str, message: str) -> dict[str, Any]:
    return {"type": "error", "code": code, "error": message[:300]}


async def _default_lead(job: JobData, s: Settings, recall: Any) -> LeadDraft:
    from .lead import run_lead  # lazy: offline runs never build a Bedrock model

    return await run_lead(job, s, recall)


async def _triage(
    job_in: JobIn, s: Settings, engine: Engine, connect: Connector, lead: Lead, emit: Emit
) -> TriageOutput:
    try:
        session = connect(s.mcp_url, job_in.mcp_token)
        call = await asyncio.to_thread(session.__enter__)
    except Exception as exc:
        log.warning("MCP session failed", exc_info=True)
        raise ToolFailed(f"could not open the MCP session ({type(exc).__name__})") from None
    try:
        job = JobData(deal_id=job_in.deal_id, call=call)
        await asyncio.to_thread(job.read, "explain_deal_risk")
        await asyncio.to_thread(job.read, "get_playbook")
        emit({"type": "progress", "step": "analyzing", "engine": engine})
        if engine == "offline":
            draft = await asyncio.to_thread(offline_triage, job)
        else:
            recall = make_recall(s.memory_id, actor_id(job_in.workspace_id, job_in.user_ref))
            draft = await lead(job, s, recall)
        emit({"type": "progress", "step": "checking"})
        return finalize(draft, job)
    finally:
        await asyncio.to_thread(session.__exit__, None, None, None)


async def _work(
    job_in: JobIn, s: Settings, engine: Engine, connect: Connector, lead: Lead, emit: Emit, timeout_s: float
) -> None:
    deadline = asyncio.timeout(timeout_s)
    try:
        async with deadline:
            out = await _triage(job_in, s, engine, connect, lead, emit)
        emit({"type": "result", "engine": engine, "output": out.model_dump(mode="json")})
    except TimeoutError:
        if deadline.expired():
            emit(_error("timed_out", f"no result within {timeout_s:.0f} seconds"))
        else:
            log.exception("triage %s failed", job_in.job_id)
            emit(_error("agent_failed", "a call timed out"))
    except Rejected as exc:
        emit(_error("rejected", str(exc)))
    except ToolFailed as exc:
        emit(_error("data_unavailable", str(exc)))
    except Misconfigured as exc:
        emit(_error("misconfigured", str(exc)))
    except Exception as exc:
        log.exception("triage %s failed", job_in.job_id)
        emit(_error("agent_failed", type(exc).__name__))
    finally:
        emit(None)


async def triage_events(
    payload: Any,
    *,
    settings: Settings | None = None,
    connect: Connector = mcp_connect,
    lead: Lead = _default_lead,
    timeout_s: float = TIMEOUT_S,
) -> AsyncIterator[dict[str, Any]]:
    s = settings or get_settings()
    try:
        job_in = JobIn.model_validate(payload)
    except ValidationError as exc:
        fields = ", ".join(sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]})) or "payload"
        yield _error("invalid_payload", f"invalid {fields}")
        return
    engine = pick_engine(s)
    if engine is None:
        yield _error("misconfigured", f"WF_SONNET_MODEL_ID and WF_HAIKU_MODEL_ID must be set in {s.env}")
        return

    yield {"type": "progress", "step": "started", "job_id": job_in.job_id}
    events: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    task = asyncio.create_task(_work(job_in, s, engine, connect, lead, events.put_nowait, timeout_s))
    try:
        while (event := await events.get()) is not None:
            yield event
    finally:
        if not task.done():
            task.cancel()  # the caller went away: stop the work too
