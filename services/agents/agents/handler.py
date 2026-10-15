"""Deep triage as a plain Lambda function, where AgentCore Runtime is refused (ADR-023).

The dispatcher invokes it synchronously with the job payload. It runs the same
``triage_events`` stream as the AgentCore entrypoint and returns every event as
``{"events": [...]}``, so the dispatcher reads the terminal one as it reads the
runtime's SSE stream. There is no AgentCore Memory here: ``recall_preferences``
returns an empty list.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .runner import triage_events


async def collect(payload: Any) -> list[dict[str, Any]]:
    return [event async for event in triage_events(payload)]


def handler(event: Any, _context: Any) -> dict[str, Any]:
    return {"events": asyncio.run(collect(event))}
