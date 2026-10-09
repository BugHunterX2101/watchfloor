"""Signal Analyst: Haiku-class, temp 0, timeline + history + risk reasons (DD §10.4)."""

from __future__ import annotations

from typing import Any

from strands import Agent
from watchfloor_core.settings import Settings

from ..models import haiku
from ..prompts import load_prompt

TOOLS = ("explain_deal_risk", "get_deal_timeline", "get_deal_history")


def build(data_tools: dict[str, Any], settings: Settings) -> Agent:
    return Agent(
        model=haiku(settings=settings),
        system_prompt=load_prompt("analyst"),
        tools=[data_tools[n] for n in TOOLS],
        callback_handler=None,
        name="signal_analyst",
    )
