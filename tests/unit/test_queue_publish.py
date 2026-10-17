"""Hand-off to SQS: ten to a batch, and a partly refused batch is not silent."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from watchfloor_core import queue


class _Sqs:
    def __init__(self, failed: list[dict[str, str]] | None = None) -> None:
        self.failed = failed or []
        self.sent: list[dict[str, Any]] = []

    def send_message_batch(self, **kwargs: Any) -> dict[str, Any]:
        self.sent.append(kwargs)
        return {"Failed": self.failed}


def test_ids_go_out_ten_to_a_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    sqs = _Sqs()
    monkeypatch.setattr(queue, "_sqs", lambda: sqs)
    queue._send_batches("https://queue.example/wf", "ws_x", list(range(25)))
    assert [len(call["Entries"]) for call in sqs.sent] == [10, 10, 5]


def test_a_partly_refused_batch_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(queue, "_sqs", lambda: _Sqs([{"Id": "2"}]))
    with caplog.at_level(logging.WARNING, logger="watchfloor.queue"):
        queue._send_batches("https://queue.example/wf", "ws_x", [1, 2, 3])
    assert "1 of 3 messages refused" in caplog.text
