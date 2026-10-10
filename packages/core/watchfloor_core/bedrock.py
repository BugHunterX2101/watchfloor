"""Bedrock model ids and model replies (ADR-022).

Two model families can back the Haiku- and Sonnet-class slots: Claude, through inference-profile
ids such as ``us.anthropic.…``, and Amazon Nova, through in-Region ids such as
``amazon.nova-lite-v1:0`` (what a Free plan account runs). Nova writes its reasoning in
``<thinking>`` tags before a tool call, and AWS's advice is to discard it, so every reply that
reaches a person or a parser goes through :func:`strip_reasoning`.
"""

from __future__ import annotations

import re
from typing import Any

_THINKING = re.compile(r"<thinking>.*?(?:</thinking>|\Z)", re.DOTALL | re.IGNORECASE)


def is_nova(model_id: str | None) -> bool:
    return bool(model_id) and "amazon.nova-" in str(model_id)


def strip_reasoning(text: str) -> str:
    """The reply without ``<thinking>`` blocks (an unclosed one runs to the end)."""
    return _THINKING.sub("", text).strip()


def tool_use_options(model_id: str, temperature: float) -> dict[str, Any]:
    """Sampling settings for a Strands ``BedrockModel`` that may call tools.

    Nova calls tools most reliably with greedy decoding: temperature 0 and top-k 1 (Amazon Nova
    user guide, "Tool usage"). Other models keep the temperature they were given.
    """
    if is_nova(model_id):
        return {"temperature": 0.0, "additional_request_fields": {"inferenceConfig": {"topK": 1}}}
    return {"temperature": temperature}
