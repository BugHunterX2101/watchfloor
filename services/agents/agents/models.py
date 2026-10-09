"""Bedrock model factories (TD §6.5, DD §10.4).

Ids are inference-profile ids from ``WF_SONNET_MODEL_ID`` and
``WF_HAIKU_MODEL_ID``. Only ``temperature`` is set: some recent Claude models
reject ``temperature`` and ``top_p`` together.
"""

from __future__ import annotations

from strands.models import BedrockModel
from watchfloor_core.settings import Settings, get_settings


class Misconfigured(Exception):
    """A model id the agents need is not set."""


def _model(model_id: str | None, name: str, temperature: float, max_tokens: int) -> BedrockModel:
    if not model_id:
        raise Misconfigured(f"{name} is not set")
    return BedrockModel(model_id=model_id, temperature=temperature, max_tokens=max_tokens)


def sonnet(
    temperature: float = 0.0, max_tokens: int = 2048, *, settings: Settings | None = None
) -> BedrockModel:
    s = settings or get_settings()
    return _model(s.sonnet_model_id, "WF_SONNET_MODEL_ID", temperature, max_tokens)


def haiku(
    temperature: float = 0.0, max_tokens: int = 1024, *, settings: Settings | None = None
) -> BedrockModel:
    s = settings or get_settings()
    return _model(s.haiku_model_id, "WF_HAIKU_MODEL_ID", temperature, max_tokens)
