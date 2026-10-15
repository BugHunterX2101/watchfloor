"""The Gemini key (ADR-023): from the environment locally, from Secrets Manager when deployed."""

from __future__ import annotations

import pytest
from watchfloor_core import gemini
from watchfloor_core.settings import Settings


def test_gemini_needs_a_model_and_somewhere_to_read_the_key() -> None:
    assert not gemini.configured(Settings())
    assert not gemini.configured(Settings(gemini_model_id="gemini-flash-lite-latest"))
    assert gemini.configured(Settings(gemini_model_id="g", gemini_api_key="k"))
    assert gemini.configured(Settings(gemini_model_id="g", gemini_secret="wf-dev/models/gemini"))


def test_the_key_comes_from_the_environment_first() -> None:
    assert gemini.api_key(Settings(gemini_api_key="k", gemini_secret="unused")) == "k"
    assert gemini.api_key(Settings()) is None


@pytest.mark.parametrize(
    ("stored", "key"),
    [(" plain-key\n", "plain-key"), ('{"api_key": "json-key"}', "json-key"), ('{"GEMINI": "only"}', "only")],
)
def test_the_secret_may_be_a_plain_string_or_json(
    monkeypatch: pytest.MonkeyPatch, stored: str, key: str
) -> None:
    reads: list[str] = []
    monkeypatch.setattr(gemini, "_fetch", lambda secret_id: reads.append(secret_id) or stored)
    monkeypatch.setattr(gemini, "_cache", {})
    s = Settings(gemini_model_id="g", gemini_secret="wf-dev/models/gemini")
    assert gemini.api_key(s) == key and gemini.api_key(s) == key
    assert reads == ["wf-dev/models/gemini"]  # cached between calls
