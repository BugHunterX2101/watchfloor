"""Specialists as tools for the Triage Lead ("agents as tools", DD §10.4–10.5).

Each call builds a fresh specialist ``Agent`` (Strands agents refuse concurrent
invocations, and the Lead may call two specialists in one turn), runs it with
its step limit and timeout, and returns its text. A specialist that times out
or fails returns an ``{"error": ...}`` JSON string instead of raising, so the
Lead can still finish with what it has. Repeat calls are budgeted: the analyst
and context answers are cached, the strategist and critic get one revision.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from strands import Agent, tool
from watchfloor_core.settings import Settings

from . import analyst, context, critic, strategist

log = logging.getLogger("watchfloor.agents.specialists")


@dataclass(frozen=True)
class Budget:
    turns: int
    timeout_s: float
    calls: int  # beyond this, analyst/context return their cached answer; others an error


BUDGETS = {
    "signal_analyst": Budget(turns=4, timeout_s=20, calls=1),
    "account_context": Budget(turns=4, timeout_s=20, calls=1),
    "playbook_strategist": Budget(turns=3, timeout_s=25, calls=2),
    "critic_review": Budget(turns=1, timeout_s=15, calls=2),
}
CACHED = {"signal_analyst", "account_context"}


async def run_specialist(name: str, build: Callable[[], Agent], prompt: str, budget: Budget) -> str:
    cancel = threading.Event()
    try:
        agent = build()
        result = await asyncio.wait_for(
            agent.invoke_async(prompt, limits={"turns": budget.turns}, cancel_signal=cancel),
            budget.timeout_s,
        )
    except TimeoutError:
        cancel.set()
        return json.dumps({"error": f"{name} ran out of time; continue without it"})
    except Exception as exc:  # one specialist failing must not end the review
        log.warning("%s failed: %s", name, type(exc).__name__, exc_info=True)
        return json.dumps({"error": f"{name} failed; continue without it"})
    return str(result).strip()


def make_specialist_tools(data_tools: dict[str, Any], *, recall: Any, settings: Settings) -> list[Any]:
    calls: dict[str, int] = {}
    answers: dict[str, str] = {}

    async def run(name: str, build: Callable[[], Agent], prompt: str) -> str:
        budget = BUDGETS[name]
        if calls.get(name, 0) >= budget.calls:
            if name in CACHED and name in answers:
                return answers[name]
            return json.dumps({"error": f"{name} budget spent; finalize with what you have"})
        calls[name] = calls.get(name, 0) + 1
        answers[name] = await run_specialist(name, build, prompt, budget)
        return answers[name]

    @tool
    async def signal_analyst() -> str:
        """Analyze the deal under review: risk reasons, activity timeline and field history.

        Returns Evidence JSON: signals plus factual claims, each citing activity or history ids.
        """
        return await run(
            "signal_analyst",
            lambda: analyst.build(data_tools, settings),
            "Analyze the deal under review. Return Evidence JSON only.",
        )

    @tool
    async def account_context() -> str:
        """Facts about the deal's account: contacts and economic buyer, other deals, earlier reviews
        and their outcomes, and this user's learned preferences. Returns AccountFacts JSON."""
        return await run(
            "account_context",
            lambda: context.build(data_tools, settings, recall),
            "Build the context for the deal under review. Return AccountFacts JSON only.",
        )

    @tool
    async def playbook_strategist(evidence_json: str, facts_json: str) -> str:
        """Map evidence and facts to at most 3 playbook actions with params, rationale and evidence ids.

        Args:
            evidence_json: The signal_analyst result.
            facts_json: The account_context result, plus any critic issues to address.
        """
        return await run(
            "playbook_strategist",
            lambda: strategist.build(data_tools, settings),
            f"EVIDENCE:\n{evidence_json[:12000]}\n\nFACTS:\n{facts_json[:8000]}\n\nReturn Actions JSON only.",
        )

    @tool
    async def critic_review(draft_json: str, evidence_json: str) -> str:
        """Verify a draft triage against the evidence. Returns {"ok": bool, "issues": [...]}.

        Args:
            draft_json: The draft: voice_summary, risk_drivers, recommended_actions, confidence.
            evidence_json: The signal_analyst result.
        """
        return await run(
            "critic_review",
            lambda: critic.build(settings),
            f"DRAFT:\n{draft_json[:8000]}\n\nEVIDENCE:\n{evidence_json[:12000]}",
        )

    return [signal_analyst, account_context, playbook_strategist, critic_review]
