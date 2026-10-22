"""The assistant's voice: Amazon Polly reading a reply aloud, with the timing of each word.

Opt-in with ``WF_POLLY_VOICE`` (a Polly neural voice id such as ``Ruth``); without it the browser's own
speech synthesis speaks, as before. The neural engine, not generative: it is the one that returns word
speech marks (the captions follow the voice word by word) and it costs about half as much per character.

What the endpoint will spend is bounded three ways, because the page is public and a signed-in user is
not the same as a trusted one: a reply is at most ``MAX_CHARS`` characters, one person may ask
``SPEAK_PER_MIN`` times a minute, and the whole process at most ``WF_POLLY_DAILY_CHARS`` characters a
UTC day (default 30,000, about 0.9 million a month: inside the neural free tier of one million). Past any
of them the answer is a refusal and the page falls back to the browser's voice. Every refusal is the same
to the page: speak locally.

Polly bills the audio and the speech marks as two requests of the same text, so a reply costs twice its
length against the budget. Speech marks give byte offsets into the UTF-8 text; the page wants offsets
into its own string (UTF-16 units), so they are converted here.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger(__name__)

MAX_CHARS = 1500  # Polly's own limit is 3,000 billed characters a request; a spoken reply is far shorter
SPEAK_PER_MIN = 20
DAILY_CHARS = 30_000
ENGINE = "neural"
TIMEOUT_S = 8.0


class Synthesizer(Protocol):
    """The two Polly calls a spoken reply needs (boto3's ``synthesize_speech``, or a fake in tests)."""

    def synthesize_speech(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Mark:
    """A word: when it starts (milliseconds into the audio) and where it is in the text (UTF-16 units)."""

    time: int
    index: int
    length: int


class Unavailable(RuntimeError):
    """Polly could not speak this one; the page speaks it itself."""


def _utf16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def word_marks(text: str, lines: str) -> list[Mark]:
    """Polly's JSON-lines word marks as offsets into the page's string, in time order."""
    raw = text.encode("utf-8")
    marks: list[Mark] = []
    for line in lines.splitlines():
        if not line.strip():
            continue
        m = json.loads(line)
        if m.get("type") != "word":
            continue
        start, end = int(m["start"]), int(m["end"])
        before = raw[:start].decode("utf-8", errors="ignore")
        word = raw[start:end].decode("utf-8", errors="ignore")
        marks.append(Mark(time=int(m["time"]), index=_utf16(before), length=max(1, _utf16(word))))
    return sorted(marks, key=lambda k: k.time)


class DailyBudget:
    """Characters Polly may be asked for in one UTC day, across everyone this process serves."""

    def __init__(self, chars: int, *, now: Callable[[], float] = time.time) -> None:
        self.chars = chars
        self._now = now
        self._day = -1
        self._used = 0

    def take(self, chars: int) -> bool:
        day = int(self._now() // 86_400)
        if day != self._day:
            self._day, self._used = day, 0
        if self._used + chars > self.chars:
            return False
        self._used += chars
        return True


class Voice:
    def __init__(
        self,
        voice_id: str,
        *,
        client: Synthesizer | None = None,
        budget: DailyBudget | None = None,
        region: str | None = None,
    ) -> None:
        self.voice_id = voice_id
        self._client = client
        self._region = region
        self._lock = threading.Lock()  # the audio and the marks ask for the client at the same moment
        self.budget = budget or DailyBudget(DAILY_CHARS)

    def _polly(self) -> Synthesizer:
        with self._lock:
            if self._client is None:
                import boto3  # only when the voice is on: tests and voiceless deploys never load it
                from botocore.config import Config

                cfg = Config(
                    connect_timeout=3, read_timeout=TIMEOUT_S, retries={"max_attempts": 2, "mode": "standard"}
                )
                self._client = boto3.client("polly", region_name=self._region, config=cfg)
            return self._client

    def warm(self) -> None:
        """Build the client ahead of the first reply (importing boto3 takes about a second). No AWS call."""
        try:
            self._polly()
        except Exception as exc:  # a broken install shows on the first reply instead, which falls back
            log.warning("polly client not built: %s", type(exc).__name__)

    def _call(self, text: str, fmt: str, marks: bool) -> bytes:
        args: dict[str, Any] = {
            "Engine": ENGINE,
            "VoiceId": self.voice_id,
            "OutputFormat": fmt,
            "Text": text,
            "TextType": "text",
        }
        if marks:
            args["SpeechMarkTypes"] = ["word"]
        else:
            args["SampleRate"] = "24000"
        out = self._polly().synthesize_speech(**args)
        stream = out["AudioStream"]
        try:
            return stream.read()
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()

    async def speak(self, text: str) -> dict[str, Any]:
        """The reply as MP3 audio (base64) and its word marks. Raises ``Unavailable`` on any failure."""
        if not self.budget.take(2 * len(text)):
            raise Unavailable("the voice's daily allowance is used up")
        try:
            audio, lines = await asyncio.wait_for(
                asyncio.gather(
                    asyncio.to_thread(self._call, text, "mp3", False),
                    asyncio.to_thread(self._call, text, "json", True),
                ),
                timeout=TIMEOUT_S,
            )
        except Exception as exc:  # any AWS, network or timeout failure: the page speaks for itself
            log.warning("polly failed: %s", type(exc).__name__)
            raise Unavailable("the voice is unavailable") from exc
        marks = word_marks(text, lines.decode("utf-8"))
        return {
            "audio": base64.b64encode(audio).decode("ascii"),
            "type": "audio/mpeg",
            "marks": [[m.time, m.index, m.length] for m in marks],
        }


def from_env() -> Voice | None:
    """The voice, when ``WF_POLLY_VOICE`` names one; else None (the browser speaks)."""
    voice_id = os.environ.get("WF_POLLY_VOICE", "").strip()
    if not voice_id:
        return None
    if not voice_id.isalpha() or len(voice_id) > 32:
        log.warning("WF_POLLY_VOICE=%r is not a Polly voice id; the browser will speak", voice_id[:32])
        return None
    try:
        daily = int(os.environ.get("WF_POLLY_DAILY_CHARS", DAILY_CHARS))
    except ValueError:
        daily = DAILY_CHARS
    region = os.environ.get("WF_POLLY_REGION") or os.environ.get("AWS_REGION") or "us-east-1"
    return Voice(voice_id, budget=DailyBudget(max(0, daily)), region=region)
