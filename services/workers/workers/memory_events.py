"""Outcome events for AgentCore Memory (DD §11), written by the dispatcher.

Compact natural-language records keyed by ``actorId = "{workspace}:{user}"``,
so learned tendencies follow the person, not a session. Best effort: memory is
an enrichment, and a failed write never fails the job it describes. Without
``WF_MEMORY_ID`` (local) nothing is written.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from watchfloor_core.settings import Settings

log = logging.getLogger("watchfloor.memory")

Recorder = Callable[[str, str, str], Awaitable[None]]


def _create_event(memory_id: str, actor_id: str, text: str) -> None:
    import boto3  # lazy: only in AWS

    # ⚠ Verify (S6): event payload shape and session semantics in the pinned SDK.
    boto3.client("bedrock-agentcore").create_event(
        memoryId=memory_id,
        actorId=actor_id,
        sessionId=f"outcomes-{actor_id.replace(':', '-')}",
        eventTimestamp=datetime.fromtimestamp(time.time(), UTC),
        payload=[{"conversational": {"content": {"text": text}, "role": "OTHER"}}],
    )


def recorder_from_settings(s: Settings) -> Recorder:
    memory_id = s.memory_id

    async def record(workspace_id: str, user_id: str, text: str) -> None:
        if not memory_id:
            log.debug("memory event (not stored locally): %s", text)
            return
        try:
            await asyncio.to_thread(_create_event, memory_id, f"{workspace_id}:{user_id}", text)
        except Exception:
            log.warning("memory event failed", exc_info=True)

    return record
