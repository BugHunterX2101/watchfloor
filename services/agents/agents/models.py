"""Model factories (TD §6.5, DD §10.4): Bedrock where it runs, else Gemini (ADR-023).

Bedrock ids come from ``WF_SONNET_MODEL_ID`` and ``WF_HAIKU_MODEL_ID``: Claude
inference profiles, or Amazon Nova model ids on a Free plan account (ADR-022).
Only ``temperature`` is set: some recent Claude models reject ``temperature``
and ``top_p`` together. Nova runs greedy (``tool_use_options``).

Where Bedrock is off (``models_enabled: false``) both tiers run on Gemini at its
default temperature, which Google advises keeping for tool use: the reasoning
tier on ``WF_GEMINI_MODEL_ID``, the fast tier on ``WF_GEMINI_HAIKU_MODEL_ID``
when set, else on the same model.
"""

from __future__ import annotations

from typing import Any, Literal

from strands.models import BedrockModel
from watchfloor_core import gemini
from watchfloor_core.bedrock import tool_use_options
from watchfloor_core.settings import Settings, get_settings

GEMINI_TIMEOUT_MS = 30_000  # one call; the specialists' own budgets are shorter


class Misconfigured(Exception):
    """A model id the agents need is not set."""


Tier = Literal["sonnet", "haiku"]
ENV_NAMES: dict[Tier, str] = {"sonnet": "WF_SONNET_MODEL_ID", "haiku": "WF_HAIKU_MODEL_ID"}


def _gemini(s: Settings, model_id: str | None, max_tokens: int) -> Any:
    from strands.models.gemini import GeminiModel  # lazy: Bedrock runs never import google-genai

    key = gemini.api_key(s)
    if not key or not model_id:
        raise Misconfigured("no Gemini model id or API key")
    return GeminiModel(
        client_args={"api_key": key, "http_options": {"timeout": GEMINI_TIMEOUT_MS}},
        model_id=model_id,
        params={"max_output_tokens": max_tokens},
    )


def _bedrock_id(s: Settings, tier: Tier) -> str | None:
    model_id = s.sonnet_model_id if tier == "sonnet" else s.haiku_model_id
    return model_id if s.models_enabled and model_id else None


def tier_model_id(s: Settings, tier: Tier) -> str | None:
    """The model a tier runs on: its Bedrock id while models are on, else its Gemini model."""
    return _bedrock_id(s, tier) or (gemini.model_id(s, tier) if gemini.configured(s) else None)


def model_names(s: Settings) -> str:
    """Both tiers' model ids, for the usage log ("nova-pro,nova-lite"; one name when they match)."""
    return ",".join(dict.fromkeys(m for m in (tier_model_id(s, "sonnet"), tier_model_id(s, "haiku")) if m))


def _model(s: Settings, tier: Tier, temperature: float, max_tokens: int) -> Any:
    model_id = _bedrock_id(s, tier)
    if model_id:
        return BedrockModel(
            model_id=model_id, max_tokens=max_tokens, **tool_use_options(model_id, temperature)
        )
    if gemini.configured(s):
        return _gemini(s, gemini.model_id(s, tier), max_tokens)
    raise Misconfigured(f"{ENV_NAMES[tier]} is not set")


def sonnet(temperature: float = 0.0, max_tokens: int = 2048, *, settings: Settings | None = None) -> Any:
    return _model(settings or get_settings(), "sonnet", temperature, max_tokens)


def haiku(temperature: float = 0.0, max_tokens: int = 1024, *, settings: Settings | None = None) -> Any:
    return _model(settings or get_settings(), "haiku", temperature, max_tokens)
