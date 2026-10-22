"""POST /api/speak: Amazon Polly's voice, bounded, and never a dead end for the page. No AWS is called."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import FakeAuth, FakeMcp, make_config, sign_in
from harness_api.app import create_app
from harness_api.voice import MAX_CHARS, SPEAK_PER_MIN, DailyBudget, Voice, from_env, word_marks

AUDIO = b"ID3fake-mp3"


class FakePolly:
    """``synthesize_speech`` as boto3 answers it: audio, or JSON-lines word marks with byte offsets."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def synthesize_speech(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        if self.fail:
            raise RuntimeError("ThrottlingException")
        if kw["OutputFormat"] == "json":
            at, lines = 0, []
            for i, word in enumerate(kw["Text"].split(" ")):
                size = len(word.encode())
                mark = {"time": 100 * i, "type": "word", "start": at, "end": at + size, "value": word}
                lines.append(json.dumps(mark))
                at += size + 1
            return {"AudioStream": io.BytesIO("\n".join(lines).encode())}
        return {"AudioStream": io.BytesIO(AUDIO)}


def client_for(tmp_path: Path, voice: Voice | None) -> httpx.AsyncClient:
    app = create_app(
        make_config(tmp_path),
        auth_transport=httpx.MockTransport(FakeAuth()),
        mcp_transport=httpx.MockTransport(FakeMcp()),
        voice=voice,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://harness.test")


async def test_a_reply_comes_back_as_audio_with_its_words_timed(tmp_path: Path) -> None:
    polly = FakePolly()
    async with client_for(tmp_path, Voice("Ruth", client=polly)) as c:
        assert (await c.get("/api/session")).json()["voice"] == "polly"
        await sign_in(c)
        r = await c.post("/api/speak", json={"text": "Kestrel is at risk."})
    assert r.status_code == 200
    body = r.json()
    assert base64.b64decode(body["audio"]) == AUDIO and body["type"] == "audio/mpeg"
    assert body["marks"] == [[0, 0, 7], [100, 8, 2], [200, 11, 2], [300, 14, 5]]
    assert {k["Engine"] for k in polly.calls} == {"neural"} and {k["VoiceId"] for k in polly.calls} == {
        "Ruth"
    }
    assert sorted(k["OutputFormat"] for k in polly.calls) == ["json", "mp3"]


def test_marks_are_offsets_into_the_pages_string_not_utf8_bytes() -> None:
    text = "Harbor’s café is at risk"  # ’ is 3 bytes and é is 2; the page counts each as one
    lines = FakePolly().synthesize_speech(OutputFormat="json", Text=text)["AudioStream"].read().decode()
    marks = word_marks(text, lines)
    assert [text[m.index : m.index + m.length] for m in marks] == text.split(" ")


async def test_the_voice_needs_a_session_and_a_sensible_request(tmp_path: Path) -> None:
    async with client_for(tmp_path, Voice("Ruth", client=FakePolly())) as c:
        assert (await c.post("/api/speak", json={"text": "hi"})).status_code == 401
        await sign_in(c)
        assert (await c.post("/api/speak", json={"text": ""})).status_code == 400
        assert (await c.post("/api/speak", json={"text": "x" * (MAX_CHARS + 1)})).status_code == 400
        plain = await c.post("/api/speak", content=b"text", headers={"content-type": "text/plain"})
        assert plain.status_code == 415


async def test_without_a_voice_configured_the_page_is_told_to_speak_itself(tmp_path: Path) -> None:
    async with client_for(tmp_path, None) as c:
        assert (await c.get("/api/session")).json()["voice"] is None
        await sign_in(c)
        assert (await c.post("/api/speak", json={"text": "hi"})).status_code == 404


async def test_polly_failing_is_a_503_the_page_falls_back_from(tmp_path: Path) -> None:
    async with client_for(tmp_path, Voice("Ruth", client=FakePolly(fail=True))) as c:
        await sign_in(c)
        r = await c.post("/api/speak", json={"text": "Kestrel is at risk."})
    assert r.status_code == 503 and r.json()["error"] == "voice_unavailable"


async def test_one_person_is_held_to_a_number_of_replies_a_minute(tmp_path: Path) -> None:
    async with client_for(tmp_path, Voice("Ruth", client=FakePolly())) as c:
        await sign_in(c)
        codes = [
            (await c.post("/api/speak", json={"text": "ok"})).status_code for _ in range(SPEAK_PER_MIN + 1)
        ]
    assert codes[:-1] == [200] * SPEAK_PER_MIN and codes[-1] == 429


async def test_the_days_allowance_is_shared_and_counts_audio_and_marks(tmp_path: Path) -> None:
    clock = [0.0]
    budget = DailyBudget(40, now=lambda: clock[0])
    async with client_for(tmp_path, Voice("Ruth", client=FakePolly(), budget=budget)) as c:
        await sign_in(c)
        text = "a b c d e f g h i j"  # 19 characters, billed twice
        assert (await c.post("/api/speak", json={"text": text})).status_code == 200
        assert (await c.post("/api/speak", json={"text": text})).status_code == 503
        clock[0] += 86_400  # a new UTC day
        assert (await c.post("/api/speak", json={"text": text})).status_code == 200


def test_the_voice_is_on_only_when_a_voice_id_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WF_POLLY_VOICE", raising=False)
    assert from_env() is None
    monkeypatch.setenv("WF_POLLY_VOICE", "Ruth; rm -rf")
    assert from_env() is None
    monkeypatch.setenv("WF_POLLY_VOICE", "Ruth")
    monkeypatch.setenv("WF_POLLY_DAILY_CHARS", "1000")
    voice = from_env()
    assert voice is not None and voice.voice_id == "Ruth" and voice.budget.chars == 1000


async def test_the_page_may_play_blob_audio_and_nothing_else_new(tmp_path: Path) -> None:
    async with client_for(tmp_path, None) as c:
        csp = (await c.get("/healthz")).headers["content-security-policy"]
    assert "media-src 'self' blob:" in csp and "default-src 'none'" in csp
