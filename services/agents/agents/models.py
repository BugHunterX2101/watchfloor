"""Model factories (TD §6.5, DD §10.4): Bedrock where it runs, else Gemini (ADR-023).

Bedrock ids come from ``WF_SONNET_MODEL_ID`` and ``WF_HAIKU_MODEL_ID``: Claude
inference profiles, or Amazon Nova model ids on a Free plan account (ADR-022).
Only ``temperature`` is set: some recent Claude models reject ``temperature``
and ``top_p`` together. Nova runs greedy (``tool_use_options``).

Where Bedrock is off (``models_enabled: false``) both tiers run on the one
Gemini model (``WF_GEMINI_MODEL_ID``) at its default temperature, which Google
advises keeping for tool use.
"""

from __future__ import annotations

from typing import Any

from strands.models import BedrockModel
from watchfloor_core import gemini
from watchfloor_core.bedrock import tool_use_options
from watchfloor_core.settings import Settings, get_settings

GEMINI_TIMEOUT_MS = 30_000  # one call; the specialists' own budgets are shorter


class Misconfigured(Exception):
    """A model id the agents need is not set."""


def _gemini(s: Settings, max_tokens: int) -> Any:
    from strands.models.gemini import GeminiModel  # lazy: Bedrock runs never import google-genai

    key = gemini.api_key(s)
    if not key or not s.gemini_model_id:
        raise Misconfigured("no Gemini model id or API key")
    return GeminiModel(
        client_args={"api_key": key, "http_options": {"timeout": GEMINI_TIMEOUT_MS}},
        model_id=s.gemini_model_id,
        params={"max_output_tokens": max_tokens},
    )


def _model(s: Settings, model_id: str | None, name: str, temperature: float, max_tokens: int) -> Any:
    if s.models_enabled and model_id:
        return BedrockModel(
            model_id=model_id, max_tokens=max_tokens, **tool_use_options(model_id, temperature)
        )
    if gemini.configured(s):
        return _gemini(s, max_tokens)
    raise Misconfigured(f"{name} is not set")


def sonnet(temperature: float = 0.0, max_tokens: int = 2048, *, settings: Settings | None = None) -> Any:
    s = settings or get_settings()
    return _model(s, s.sonnet_model_id, "WF_SONNET_MODEL_ID", temperature, max_tokens)


def haiku(temperature: float = 0.0, max_tokens: int = 1024, *, settings: Settings | None = None) -> Any:
    s = settings or get_settings()
    return _model(s, s.haiku_model_id, "WF_HAIKU_MODEL_ID", temperature, max_tokens)
