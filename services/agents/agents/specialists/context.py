"""Account Context: Haiku-class, temp 0, account + prior triage + memory (DD §10.4, §11)."""

from __future__ import annotations

from typing import Any

from strands import Agent
from watchfloor_core.settings import Settings

from ..models import haiku
from ..prompts import load_prompt

TOOLS = ("get_account_context", "get_prior_triage")


def build(data_tools: dict[str, Any], settings: Settings, recall: Any) -> Agent:
    return Agent(
        model=haiku(settings=settings),
        system_prompt=load_prompt("context"),
        tools=[*(data_tools[n] for n in TOOLS), recall],
        callback_handler=None,
        name="account_context",
    )
