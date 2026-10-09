"""Critic: Sonnet, temp 0, no tools: pure verification (DD §10.4, §10.6)."""

from __future__ import annotations

from strands import Agent
from watchfloor_core.settings import Settings

from ..models import sonnet
from ..prompts import load_prompt


def build(settings: Settings) -> Agent:
    return Agent(
        model=sonnet(max_tokens=1024, settings=settings),
        system_prompt=load_prompt("critic"),
        tools=[],
        callback_handler=None,
        name="critic",
    )
