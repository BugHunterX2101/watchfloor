"""scripts/demo.py without a database: offsets, the status report, and dev/demo through fake Lambdas."""

from __future__ import annotations

import importlib.util
import json
import os
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from db.seed import ops


def _load() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "demo.py"
    spec = importlib.util.spec_from_file_location("wf_demo_cli", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


demo = _load()


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("+14h", timedelta(hours=14)),
        ("14h", timedelta(hours=14)),
        ("-30m", timedelta(minutes=-30)),
        ("2d", timedelta(days=2)),
        ("0", timedelta(0)),
        ("off", timedelta(0)),
    ],
)
def test_offsets_parse(raw: str, want: timedelta) -> None:
    assert demo.parse_offset(raw) == want


@pytest.mark.parametrize("raw", ["14", "+14x", "1.5h", "31d", "+50000h", ""])
def test_bad_offsets_are_refused(raw: str) -> None:
    with pytest.raises(ValueError):
        demo.parse_offset(raw)


def test_offsets_read_back() -> None:
    assert demo.describe_offset(None) == "none (real time)"
    assert demo.describe_offset(timedelta(hours=14)) == "+14h"
    assert demo.describe_offset(timedelta(days=1, minutes=-30)) == "+23h30m"
    assert demo.describe_offset(timedelta(minutes=-90)) == "-1h30m"


def _status(**over: object) -> dict[str, object]:
    sc = demo.scenarios
    base: dict[str, object] = {
        "workspace": sc.WORKSPACE_ID,
        "name": sc.WORKSPACE_NAME,
        "timezone": sc.TIMEZONE,
        "clock_offset": timedelta(hours=14),
        "bands": {"healthy": 30, "watch": 6, "at_risk": 3},
        "scenarios": {did: (want, want) for did, want in sc.EXPECTED.items()},
        "board": [(sc.KESTREL, "Kestrel Biotech", 71, "at_risk")],
        "pending_proposals": 1,
        "jobs": {"running": 1},
        "outbox": {("slack_message", "dead"): 2},
    }
    return {**base, **over}


def test_status_report_flags_drift_and_ignored_offsets() -> None:
    st = _status()
    text = demo.render_status(st, demo_mode=False)
    assert "+14h  (ignored unless WF_DEMO_MODE=true)" in text
    assert "Kestrel Biotech 71" in text and "2 slack_message dead" in text and "1 running" in text
    assert demo.scenarios_ok(st)

    drifted = _status(scenarios={demo.scenarios.HARBOR: ((61, "watch"), (66, "at_risk"))})
    assert "DRIFT (expected 66 at_risk)" in demo.render_status(drifted, demo_mode=True)
    assert not demo.scenarios_ok(drifted)


def test_status_survives_the_lambda_round_trip() -> None:
    st = _status(scenarios={demo.scenarios.HARBOR: (None, (66, "at_risk"))}, clock_offset=None)
    assert ops.status_from_json(json.loads(json.dumps(ops.status_to_json(st)))) == st
    assert ops.status_from_json(json.loads(json.dumps(ops.status_to_json(_status())))) == _status()


def test_bad_arguments_are_refused_before_any_connection() -> None:
    bad = ["clock", "--offset", "+99x", "--url", "postgresql://x@127.0.0.1:1/x"]
    assert demo.main(bad, env_file=None) == 2
    assert demo.main(["clock", "--env", "demo", "--offset", "+99x"], env_file=None) == 2
    assert demo.main(["outbox-retry", "--env", "demo"], env_file=None) == 2


class FakeLambdas:
    """``demo.invoke`` stand-in: records each call, answers as the seeder and the watcher would."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.demo_mode, self.fail = True, ""

    def __call__(self, function: str, event: dict[str, Any], *, region: str | None, timeout: int = 60) -> Any:
        assert region == "us-east-1"  # infra/config/<env>.yaml, not the AWS CLI's default
        self.calls.append((function, event))
        if function == self.fail:
            raise demo.RemoteError(f"{function}: AccessDeniedException")
        if function.endswith("-watcher"):
            return {"rescored": 39, "fired": 1}
        base = {"workspace": demo.scenarios.WORKSPACE_ID, "demo_mode": self.demo_mode}
        if event.get("demo") == "clock":
            return {**base, "clock_offset": demo.describe_offset(demo.parse_offset(event["offset"]))}
        if event.get("demo") == "status":
            return {**base, "status": ops.status_to_json(_status()), "scenarios_ok": True}
        if event.get("demo") == "outbox-retry":
            return {**base, "rearmed": [42]}
        return {**base, "bands": {"healthy": 30, "watch": 6, "at_risk": 3}}


@pytest.fixture
def lambdas(monkeypatch: pytest.MonkeyPatch) -> FakeLambdas:
    fake = FakeLambdas()
    monkeypatch.setattr(demo, "invoke", fake)
    return fake


def test_remote_clock_sets_the_offset_through_the_seeder_then_runs_the_watcher(
    lambdas: FakeLambdas, capsys: pytest.CaptureFixture[str]
) -> None:
    assert demo.main(["clock", "--env", "demo", "--offset", "+14h"], env_file=None) == 0
    assert lambdas.calls == [("wf-demo-seeder", {"demo": "clock", "offset": "+14h"}), ("wf-demo-watcher", {})]
    out = capsys.readouterr()
    assert f"clock offset for {demo.scenarios.WORKSPACE_ID}: +14h" in out.out
    assert "WF_DEMO_MODE" not in out.err


def test_remote_clock_warns_where_offsets_are_ignored(
    lambdas: FakeLambdas, capsys: pytest.CaptureFixture[str]
) -> None:
    lambdas.demo_mode = False
    assert demo.main(["clock", "--env", "dev", "--offset", "+14h", "--no-watch"], env_file=None) == 0
    assert [f for f, _ in lambdas.calls] == ["wf-dev-seeder"]
    assert "infra/config/dev.yaml" in capsys.readouterr().err


def test_remote_status_renders_the_seeders_report(
    lambdas: FakeLambdas, capsys: pytest.CaptureFixture[str]
) -> None:
    assert demo.main(["status", "--env", "demo"], env_file=None) == 0
    out = capsys.readouterr().out
    assert "Kestrel Biotech 71" in out and "+14h" in out and "ignored" not in out


def test_remote_reset_retry_and_watch_reach_the_right_functions(lambdas: FakeLambdas) -> None:
    assert demo.main(["reset", "--env", "dev"], env_file=None) == 0
    assert demo.main(["outbox-retry", "--env", "dev", "--id", "42"], env_file=None) == 0
    assert demo.main(["watch-now", "--env", "dev"], env_file=None) == 0
    assert lambdas.calls == [
        ("wf-dev-seeder", {"bootstrap": False}),
        ("wf-dev-seeder", {"demo": "outbox-retry", "ids": [42], "all_dead": False}),
        ("wf-dev-watcher", {}),
    ]


def test_remote_failures_exit_1(lambdas: FakeLambdas, capsys: pytest.CaptureFixture[str]) -> None:
    lambdas.fail = "wf-demo-seeder"
    assert demo.main(["status", "--env", "demo"], env_file=None) == 1
    assert "AccessDeniedException" in capsys.readouterr().err


def test_env_file_fills_only_unset_wf_variables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# comment\nWF_DEMO_MODE=true\nWF_SONNET_MODEL_ID=\nWF_ENV=dev\nOTHER=1\nWF_SLACK_WEBHOOK_URL='x'\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("WF_DEMO_MODE", raising=False)
    monkeypatch.delenv("WF_SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("WF_ENV", "test")
    assert demo.load_env_file(env) == ["WF_DEMO_MODE", "WF_SLACK_WEBHOOK_URL"]
    assert demo._demo_mode()
    assert os.environ["WF_ENV"] == "test" and os.environ["WF_SLACK_WEBHOOK_URL"] == "x"
    monkeypatch.delenv("WF_DEMO_MODE")
    monkeypatch.delenv("WF_SLACK_WEBHOOK_URL")
    from watchfloor_core.settings import reset_settings

    reset_settings()
    assert demo.load_env_file(tmp_path / "missing") == []
