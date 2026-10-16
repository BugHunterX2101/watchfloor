"""Model usage per unit of work: one log line per harness turn and per deep-triage job.

    llm_usage scope=triage engine=strands model=gemini-flash-lite-latest calls=7 input_tokens=9120
        output_tokens=860 job=job_… outcome=result          (one line in the log)

Plain ``key=value`` pairs, so CloudWatch Logs Insights can add them up without a custom metric
(the query is in docs/implementation-notes.md, "Update (2026-10-16)").

The counts come from each Strands agent's ``event_loop_metrics``: ``cycle_count`` (one model call
per cycle) and ``accumulated_usage``. The line carries no prompt, reply or key.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("watchfloor.usage")
log.setLevel(logging.INFO)  # Lambda's root logger drops INFO; this line must reach CloudWatch


@dataclass
class Tally:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, metrics: Any) -> None:
        """Add one agent's ``event_loop_metrics``; anything without usage (a test fake) adds nothing."""
        usage = getattr(metrics, "accumulated_usage", None)
        if not isinstance(usage, dict):
            return
        self.calls += int(getattr(metrics, "cycle_count", 0) or 0)
        self.input_tokens += int(usage.get("inputTokens") or 0)
        self.output_tokens += int(usage.get("outputTokens") or 0)

    def log(self, *, scope: str, engine: str, model: str, **extra: str) -> None:
        if not logging.getLogger().handlers and not log.handlers:  # a bare process (uvicorn locally)
            log.addHandler(logging.StreamHandler(sys.stderr))
        fields = {
            "scope": scope,
            "engine": engine,
            "model": model or "none",
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            **extra,
        }
        log.info("llm_usage %s", " ".join(f"{k}={str(v).replace(' ', '_')}" for k, v in fields.items()))
