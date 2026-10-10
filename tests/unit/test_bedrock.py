"""Bedrock model ids and replies (ADR-022): Nova's reasoning never reaches a person."""

from __future__ import annotations

from watchfloor_core.bedrock import is_nova, strip_reasoning, tool_use_options


def test_nova_reasoning_is_dropped_from_replies() -> None:
    reply = "<thinking>The user wants the briefing, so call get_briefing.</thinking>\nThree deals need you."
    assert strip_reasoning(reply) == "Three deals need you."
    assert strip_reasoning("Done. <THINKING>cut off by max tokens") == "Done."
    assert strip_reasoning("<thinking>a</thinking>One.<thinking>b</thinking> Two.") == "One. Two."
    assert strip_reasoning("No tags here.") == "No tags here."


def test_nova_calls_tools_greedily_and_claude_keeps_its_temperature() -> None:
    assert is_nova("amazon.nova-lite-v1:0") and is_nova("us.amazon.nova-pro-v1:0")
    assert not is_nova("us.anthropic.claude-haiku-4-5-20251001-v1:0") and not is_nova(None)
    assert tool_use_options("amazon.nova-lite-v1:0", 0.5) == {
        "temperature": 0.0,
        "additional_request_fields": {"inferenceConfig": {"topK": 1}},
    }
    assert tool_use_options("us.anthropic.claude-sonnet-5-5", 0.3) == {"temperature": 0.3}
