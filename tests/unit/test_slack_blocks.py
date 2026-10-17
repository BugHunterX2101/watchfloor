"""The plain-text fallback of every Slack message is what notifications show, so it must not mention anyone."""

from __future__ import annotations

from typing import Any

import pytest
from watchfloor_core.services import slack_blocks as sb

NASTY = "<!channel> @here <http://x|y> & co"


def _messages() -> list[dict[str, Any]]:
    return [
        sb.risk_alert(
            account=NASTY,
            deal_name=NASTY,
            score=40,
            band="red",
            amount_display="$1",
            stage="proposal",
            owner=NASTY,
            reasons=[NASTY],
            rule_description=NASTY,
        ),
        sb.task_created(
            assignee=NASTY, mention=None, title=NASTY, account=NASTY, due_display=None, requester=NASTY
        ),
        sb.owner_reassigned(
            account=NASTY, deal_name=NASTY, old_owner=NASTY, new_owner=NASTY, requester=NASTY
        ),
        sb.team_note(account=NASTY, score=40, band="red", message=NASTY, requester=NASTY),
        sb.triage_plan(account=NASTY, score=40, band="red", voice_summary=NASTY, actions=[]),
        sb.draft_message(account=NASTY, draft=NASTY, requester=NASTY),
    ]


@pytest.mark.parametrize("message", _messages(), ids=lambda m: m["text"][:12])
def test_the_fallback_text_neutralises_mentions_and_links(message: dict[str, Any]) -> None:
    text = message["text"]
    assert "<" not in text
    assert ">" not in text
    assert "@here" not in text
    assert "&lt;!channel&gt;" in text
