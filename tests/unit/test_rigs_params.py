"""A mistyped rigs.yaml fails when it is loaded, not as a ZeroDivisionError in the watcher."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest
import yaml
from watchfloor_core.rigs_params import CONFIG_PATH, parse_params


def _doc() -> dict[str, Any]:
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_the_shipped_file_is_valid() -> None:
    assert parse_params(_doc()).model_version


def _zero_norm(d: dict[str, Any]) -> None:
    d["stages"]["demo"]["norm_days"] = 0


def _zero_baseline(d: dict[str, Any]) -> None:
    d["stages"]["demo"]["intent_baseline"] = 0


def _inverted_bands(d: dict[str, Any]) -> None:
    d["bands"]["at_risk"]["enter"] = 30


def _exit_above_enter(d: dict[str, Any]) -> None:
    d["bands"]["watch"]["exit_below"] = 45


def _negative_weight(d: dict[str, Any]) -> None:
    d["weights"].update(r=-0.1, s=0.6)  # still sums to 1.0, so only the sign is wrong


def _no_stages(d: dict[str, Any]) -> None:
    d["stages"] = {}


def _cut_everything(d: dict[str, Any]) -> None:
    d["slippage"]["amount_cut_threshold"] = 1.0


@pytest.mark.parametrize(
    "break_it",
    [
        _zero_norm,
        _zero_baseline,
        _inverted_bands,
        _exit_above_enter,
        _negative_weight,
        _no_stages,
        _cut_everything,
    ],
)
def test_nonsense_parameters_are_refused(break_it: Callable[[dict[str, Any]], None]) -> None:
    doc = copy.deepcopy(_doc())
    break_it(doc)
    with pytest.raises(ValueError, match="RIGS"):
        parse_params(doc)
