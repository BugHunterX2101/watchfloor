"""A standing order's read-back says what the rule does: the amount is matched at or above, so it is "or more"."""

from __future__ import annotations

from watchfloor_core.services.rules import describe_rule


def test_the_amount_reads_back_as_at_or_above() -> None:
    assert (
        describe_rule({"min_amount_cents": 5_000_000, "quiet_days": 5})
        == "Flag deals of $50K or more with no reply for 5 days."
    )


def test_the_other_conditions_read_back_unchanged() -> None:
    text = describe_rule({"segment": "enterprise", "stages": ["negotiation"], "min_score": 70})
    assert text == "Flag enterprise Negotiation deals with health of 30 or lower."
