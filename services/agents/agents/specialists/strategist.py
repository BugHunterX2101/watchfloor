"""Playbook Strategist: Sonnet, temp 0.3, playbook only (DD §10.4)."""

from __future__ import annotations

from typing import Any

from strands import Agent
from watchfloor_core.settings import Settings

from ..models import sonnet
from ..prompts import load_prompt


def build(data_tools: dict[str, Any], settings: Settings) -> Agent:
    return Agent(
        model=sonnet(temperature=0.3, max_tokens=1024, settings=settings),
        system_prompt=load_prompt("strategist"),
        tools=[data_tools["get_playbook"]],
        callback_handler=None,
        name="playbook_strategist",
    )
