"""Slack delivery over an incoming webhook (DD §16), aware of 429 and Retry-After.

Messages are rendered when the outbox row is written
(``watchfloor_core.services.slack_blocks``); this module only delivers them.
An incoming webhook posts to the channel it was created for, so the row's
``channel`` is shown in the console and logs but does not route the post.

Locally, with no webhook configured, messages are printed to the console. In
AWS a missing webhook is a configuration error: the row goes dead and alarms.
"""

from __future__ import annotations

import logging
import sys
from datetime import timedelta
from typing import Any, TextIO

import httpx
from watchfloor_core.settings import Settings

from .errors import GiveUp, RetryLater
from .runtime import secret_field

log = logging.getLogger("watchfloor.slack")
TIMEOUT_S = 10.0
DEFAULT_RETRY_AFTER = timedelta(seconds=30)


class SlackWebhook:
    def __init__(
        self,
        url: str | None,
        *,
        console: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
        out: TextIO | None = None,
    ) -> None:
        self._url = url
        self._console = console
        self._transport = transport
        self._out = out

    async def __call__(self, channel: str, message: dict[str, Any]) -> None:
        if not self._url:
            if not self._console:
                raise GiveUp("no Slack webhook configured (WF_SLACK_SECRET_ARN)")
            _write(self._out or sys.stdout, render_text(channel, message))
            return
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=TIMEOUT_S) as http:
                r = await http.post(self._url, json=message)
        except httpx.HTTPError as exc:
            raise RetryLater(f"slack unreachable: {type(exc).__name__}") from None
        if r.status_code == 429:
            raise RetryLater("slack rate limited", retry_after=_retry_after(r))
        if r.status_code >= 500:
            raise RetryLater(f"slack {r.status_code}")
        if r.status_code >= 400:
            # invalid_blocks, no_service, channel_is_archived…: retrying will not help.
            raise GiveUp(f"slack {r.status_code}: {r.text[:200]}")
        log.info("posted to slack", extra={"channel": channel})


def _write(out: TextIO, text: str) -> None:
    """Console preview that survives any terminal encoding (cp1252 cannot show "·" or "•")."""
    encoding = getattr(out, "encoding", None) or "utf-8"
    out.write(text.encode(encoding, errors="replace").decode(encoding))
    out.flush()


def _retry_after(r: httpx.Response) -> timedelta:
    try:
        return timedelta(seconds=max(1, int(r.headers.get("retry-after", ""))))
    except ValueError:
        return DEFAULT_RETRY_AFTER


def render_text(channel: str, message: dict[str, Any]) -> str:
    """A readable console rendering of a Block Kit message."""
    lines = [f"── Slack {channel} " + "─" * max(4, 50 - len(channel))]
    for block in message.get("blocks", []):
        if block.get("type") in ("header", "section") and block.get("text"):
            lines.append(block["text"]["text"])
        for field in block.get("fields", []):
            lines.append(field["text"].replace("\n", ": ", 1))
        for element in block.get("elements", []):
            lines.append(element.get("text", ""))
    if len(lines) == 1:
        lines.append(message.get("text", ""))
    # Show text as Slack displays it: entities decoded, the mention guard invisible.
    text = "\n".join(lines) + "\n"
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("​", "")


def webhook_from_settings(s: Settings) -> SlackWebhook:
    url = s.slack_webhook_url
    if not url and s.slack_secret_arn:
        url = secret_field(s.slack_secret_arn, "webhook_url")
    return SlackWebhook(url, console=s.env in ("local", "test"))
