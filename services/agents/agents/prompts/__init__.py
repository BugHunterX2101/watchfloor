"""System prompts, one Markdown file per agent."""

from __future__ import annotations

from functools import cache
from pathlib import Path

HERE = Path(__file__).resolve().parent


@cache
def load_prompt(name: str) -> str:
    return (HERE / f"{name}.md").read_text(encoding="utf-8").strip()
