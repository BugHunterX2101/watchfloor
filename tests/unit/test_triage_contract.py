"""The output contract is exact: an id that merely ends in a newline is not a play."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from watchfloor_core.models.triage import RecommendedAction


def _action(play_id: str) -> RecommendedAction:
    return RecommendedAction(
        action_type="notify_channel", params={}, rationale="Tell the team.", play_id=play_id, confidence=0.6
    )


@pytest.mark.parametrize("play_id", ["P01", "P09", "P12"])
def test_the_twelve_plays_are_accepted(play_id: str) -> None:
    assert _action(play_id).play_id == play_id


@pytest.mark.parametrize("play_id", ["P00", "P13", "p01", "P01\n", "P01 ", " P01", "P1", ""])
def test_anything_else_is_refused(play_id: str) -> None:
    with pytest.raises(ValidationError):
        _action(play_id)
