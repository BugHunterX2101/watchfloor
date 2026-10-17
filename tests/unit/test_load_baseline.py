"""The load baseline's arithmetic and its gate: how a call is judged, summed up and failed. No I/O."""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str) -> ModuleType:
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(f"wf_{name}", SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lb = _load("load_baseline")


def _tool_error(code: str | None) -> dict:
    error = {"error": {"code": code}} if code else {}
    return {"result": {"isError": True, "structuredContent": error}}


@pytest.mark.parametrize(
    ("status", "body", "label"),
    [
        (200, {"result": {"structuredContent": {"ok": 1}}}, None),
        (503, {}, "http_503"),
        (429, {}, "http_429"),
        (200, ["not", "an", "object"], "bad_body"),
        (200, None, "bad_body"),
        (200, {"error": {"code": -32600}}, "rpc_-32600"),
        (200, _tool_error("TEMPORARILY_UNAVAILABLE"), "tool_TEMPORARILY_UNAVAILABLE"),
        (200, _tool_error("RATE_LIMITED"), "tool_RATE_LIMITED"),
        (200, _tool_error(None), "tool_unknown"),
    ],
)
def test_a_call_is_good_or_has_one_failure_label(status: int, body: object, label: str | None) -> None:
    assert lb.classify(status, body) == label


def test_summary_reports_throughput_percentiles_and_every_failure() -> None:
    samples = {"a": [float(ms) for ms in range(1, 101)], "b": []}
    errors = Counter({"http_503": 3, "ReadTimeout": 1})
    row = lb.summarize(samples, errors, seconds=10.0)
    assert (row["ok"], row["failed"], row["rps"]) == (100, 4, 10.0)
    assert (row["p50"], row["p95"], row["p99"], row["max"]) == (50.0, 95.0, 99.0, 100.0)
    assert row["errors"] == {"ReadTimeout": 1, "http_503": 3}
    assert list(row["tools"]) == ["a"], "a tool with no good calls has no row to report"


def test_a_level_with_no_good_call_has_no_percentiles_and_fails() -> None:
    row = lb.summarize({"a": []}, Counter({"http_503": 5}), seconds=5.0)
    assert "p95" not in row
    assert row["rps"] == 0.0
    assert "no successful calls" in lb.misses(row, p95_ms=None, p99_ms=None)


def test_the_gate_fails_on_any_failed_call_even_with_no_latency_limit() -> None:
    row = lb.summarize({"a": [5.0] * 20}, Counter({"http_503": 1}), seconds=1.0)
    assert lb.misses(row, p95_ms=None, p99_ms=None) == ["1 failed calls"]


def test_the_latency_limits_apply_only_when_given() -> None:
    row = lb.summarize({"a": [10.0] * 98 + [200.0, 400.0]}, Counter(), seconds=1.0)
    assert lb.misses(row, p95_ms=None, p99_ms=None) == []
    assert lb.misses(row, p95_ms=150, p99_ms=None) == []
    assert lb.misses(row, p95_ms=None, p99_ms=150) == ["p99 200.0 ms > 150"]
    assert len(lb.misses(row, p95_ms=5, p99_ms=5)) == 2


def test_the_report_names_the_top_level_and_lists_failures() -> None:
    levels = {
        1: lb.summarize({"get_risk_briefing": [4.0, 6.0]}, Counter(), seconds=1.0),
        8: lb.summarize({"get_risk_briefing": [9.0, 11.0]}, Counter({"http_503": 2}), seconds=1.0),
    }
    meta = {
        "when": "2026-10-17",
        "commit": "abc1234",
        "machine": "TestOS 1, 4 logical CPUs, Python 3.12",
        "seconds": "15",
        "pool_max": 2,
        "acquire_s": 2.0,
    }
    text = lb.render(meta, levels)
    assert "Per tool at 8 callers" in text
    assert '"8": {"http_503": 2}' in text
    assert "not AWS numbers" in text
    assert "| 1 | 2 | 0 |" in text and "| 8 | 2 | 2 |" in text
