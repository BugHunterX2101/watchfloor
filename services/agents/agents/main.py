"""AgentCore Runtime entrypoint (DD §10.3, TD §10.3).

``BedrockAgentCoreApp`` serves ``POST /invocations`` and ``GET /ping``. The
entrypoint is an async generator, so each event goes out as an SSE
``data: {json}`` line; the dispatcher keeps the terminal ``result`` or
``error`` event (see ``runner``).

Locally: ``WF_AGENT_PORT=8083 uv run python -m agents.main``, then point the
workers at it with ``WF_AGENT_URL=http://127.0.0.1:8083/invocations``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from .runner import triage_events

app = BedrockAgentCoreApp()


@app.entrypoint
async def invoke(payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    async for event in triage_events(payload):
        yield event


if __name__ == "__main__":
    app.run(port=int(os.environ.get("WF_AGENT_PORT", "8080")))
