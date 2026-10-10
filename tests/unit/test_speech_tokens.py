from __future__ import annotations

from datetime import date

from watchfloor_core import speech, tokens


def test_money_display() -> None:
    assert speech.money_display(8_400_000) == "$84K"
    assert speech.money_display(850_000_000) == "$8.5M"
    assert speech.money_display(100_000_000) == "$1M"
    assert speech.money_display(52_000) == "$520"
    assert speech.money_display(5_250_000) == "$53K"  # half up, not banker's rounding
    assert speech.money_display(250_000) == "$2,500"  # exact under $10K
    assert speech.money_display(999_900) == "$9,999"
    assert speech.money_display(999_950) == "$10K"
    assert speech.money_display(99_960_000) == "$1M"  # $999,600 rounds up to the next unit
    assert speech.money_display(122_500_000) == "$1.2M"
    assert speech.money_display(125_000_000) == "$1.3M"
    assert speech.money_display(1_234_000_000) == "$12.3M"
    assert speech.money_display(0) == "$0"


def test_date_display_is_unambiguous() -> None:
    assert speech.date_display(date(2026, 10, 22)) == "Thursday, October 22"
    assert speech.date_display(date(2026, 11, 20)) == "Friday, November 20"


def test_short_name_and_join() -> None:
    assert speech.short_name("Daniel Reyes") == "Daniel R."
    assert speech.join_words(["a", "b", "c"]) == "a, b and c"
    assert speech.times_word(2) == "twice"


def test_confirmation_token_is_random_and_only_hash_is_kept() -> None:
    t1, h1 = tokens.new_confirmation_token()
    t2, h2 = tokens.new_confirmation_token()
    assert t1.startswith("wf1.") and t1 != t2 and h1 != h2
    assert tokens.hash_token(t1) == h1
    assert len(h1) == 32
    assert tokens.looks_like_token(t1)


def test_params_hash_is_canonical() -> None:
    a = tokens.params_hash({"b": 1, "a": "x"})
    b = tokens.params_hash({"a": "x", "b": 1})
    c = tokens.params_hash({"a": "x", "b": 2})
    assert a == b != c
