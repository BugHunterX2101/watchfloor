"""Deal-name matching rules that need no database (lookup_deal)."""

from __future__ import annotations

from watchfloor_core.repos.deals import clear_winner, spoken_name


def test_ampersands_read_as_spoken() -> None:
    assert spoken_name("Harbor & Pine") == spoken_name("harbor  and pine") == "harbor and pine"
    assert spoken_name("Harbor&Pine Logistics") == "harbor and pine logistics"


def test_an_exact_name_beats_weaker_matches() -> None:
    rows = [{"match_score": 1.0, "deal_id": "a"}, {"match_score": 0.4, "deal_id": "b"}]
    assert clear_winner(rows) == rows[:1]


def test_close_matches_still_ask() -> None:
    same_account = [{"match_score": 1.0}, {"match_score": 1.0}]
    assert clear_winner(same_account) == same_account
    fuzzy = [{"match_score": 0.6}, {"match_score": 0.3}]
    assert clear_winner(fuzzy) == fuzzy
    assert clear_winner([]) == []
