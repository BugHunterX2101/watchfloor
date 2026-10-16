"""The ECS runtime pieces (ADR-025) that need no database or AWS: the gateway host header, the worker
service's SQS loop and schedules, metrics through the API, and the one-off task runner."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from mcp_server.middleware.edge_guard import EdgeGuard
from starlette.types import Receive, Scope, Send
from watchfloor_core.settings import get_settings, reset_settings
from workers import runtime, service

ROOT = Path(__file__).resolve().parents[2]


def _script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"wf_{name}", ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- the gateway host header


async def _ok(scope: Scope, receive: Receive, send: Send) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def _get(guard: EdgeGuard, headers: dict[str, str]) -> int:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=guard), base_url="http://x") as c:
        return (await c.get("/healthz", headers=headers)).status_code


async def test_on_ecs_the_host_check_reads_the_header_the_api_sets() -> None:
    """Through a VPC link Host names the integration; the API's own domain arrives in x-wf-host."""
    api = "k04yht6ng0.execute-api.us-east-1.amazonaws.com"
    guard = EdgeGuard(_ok, hosts=[api], origins=[], host_header="x-wf-host")
    assert await _get(guard, {"x-wf-host": api, "Host": "10.20.8.15:8080"}) == 200
    assert await _get(guard, {"Host": api}) == 421
    assert await _get(guard, {"x-wf-host": "evil.example"}) == 421


async def test_by_default_the_host_check_reads_host() -> None:
    guard = EdgeGuard(_ok, hosts=["mcp.example"], origins=[])
    assert await _get(guard, {"Host": "mcp.example"}) == 200
    assert await _get(guard, {"Host": "evil.example", "x-wf-host": "mcp.example"}) == 421


def test_host_header_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WF_HOST_HEADER", " X-WF-Host ")
    reset_settings()
    try:
        assert get_settings().host_header == "x-wf-host"
    finally:
        monkeypatch.delenv("WF_HOST_HEADER")
        reset_settings()
    assert get_settings().host_header == "host"


# ---------------------------------------------------------------- the worker service


class FakeSqs:
    """receive_message hands out ``messages`` one at a time, then long-polls empty."""

    def __init__(self, messages: list[dict[str, Any]], *, delete_fails: bool = False) -> None:
        self.messages = list(messages)
        self.deleted: list[str] = []
        self.delete_fails = delete_fails

    def receive_message(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["MaxNumberOfMessages"] == 1 and kwargs["WaitTimeSeconds"] == service.WAIT_S
        if self.messages:
            return {"Messages": [self.messages.pop(0)]}
        return {}

    def delete_message(self, **kwargs: Any) -> None:
        if self.delete_fails:
            raise RuntimeError("throttled")
        self.deleted.append(kwargs["ReceiptHandle"])


def _msg(n: int) -> dict[str, Any]:
    return {
        "MessageId": f"m{n}",
        "ReceiptHandle": f"r{n}",
        "Body": json.dumps({"ws": "ws_1", "outbox_id": n}),
    }


async def _ok_batch(event: dict[str, Any], deps: Any) -> dict[str, Any]:
    return {"batchItemFailures": []}


async def _until(predicate: Any, *, tries: int = 300) -> None:
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0.01)


async def test_a_delivered_message_is_deleted_and_a_failed_one_is_left_for_sqs() -> None:
    seen: list[dict[str, Any]] = []

    async def handle(event: dict[str, Any], deps: Any) -> dict[str, Any]:
        seen.append(event)
        record = event["Records"][0]
        if record["messageId"] == "m2":
            return {"batchItemFailures": [{"itemIdentifier": "m2"}]}
        if record["messageId"] == "m3":
            raise RuntimeError("boom")
        return {"batchItemFailures": []}

    sqs = FakeSqs([])
    assert await service.deliver(sqs, "url", _msg(1), None, handle) is True
    assert await service.deliver(sqs, "url", _msg(2), None, handle) is False
    assert await service.deliver(sqs, "url", _msg(3), None, handle) is False
    assert sqs.deleted == ["r1"]
    assert seen[0] == {"Records": [{"messageId": "m1", "body": _msg(1)["Body"]}]}  # as the mapping hands it
    assert await service.deliver(FakeSqs([], delete_fails=True), "url", _msg(4), None, handle) is False


async def test_consume_delivers_everything_within_the_in_flight_limit_then_stops() -> None:
    active = peak = 0

    async def handle(event: dict[str, Any], deps: Any) -> dict[str, Any]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {"batchItemFailures": []}

    sqs = FakeSqs([_msg(n) for n in range(8)])
    gate, stop = asyncio.Semaphore(2), asyncio.Event()
    in_flight: set[asyncio.Task[bool]] = set()
    task = asyncio.create_task(
        service.consume(sqs, "url", None, gate=gate, stop=stop, in_flight=in_flight, handle=handle)
    )
    await _until(lambda: len(sqs.deleted) == 8)
    stop.set()
    await asyncio.wait_for(task, 1)
    await _until(lambda: not in_flight)
    assert sorted(sqs.deleted) == sorted(f"r{n}" for n in range(8))
    assert peak <= 2 and not in_flight
    for _ in range(2):  # every slot is back
        await asyncio.wait_for(gate.acquire(), 0.1)


async def test_a_receive_error_is_retried_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class Flaky(FakeSqs):
        def receive_message(self, **kwargs: Any) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("network")
            return super().receive_message(**kwargs)

    async def no_wait(seconds: float, stop: asyncio.Event) -> None:
        await asyncio.sleep(0)

    monkeypatch.setattr(service, "_sleep", no_wait)
    sqs = Flaky([_msg(1)])
    stop = asyncio.Event()
    task = asyncio.create_task(
        service.consume(
            sqs, "url", None, gate=asyncio.Semaphore(1), stop=stop, in_flight=set(), handle=_ok_batch
        )
    )
    await _until(lambda: bool(sqs.deleted))
    stop.set()
    await asyncio.wait_for(task, 1)
    assert sqs.deleted == ["r1"] and calls >= 2


async def test_every_runs_on_schedule_and_survives_failures_and_timeouts() -> None:
    runs: list[str] = []
    outcomes = iter(["ok", "fail", "slow", "ok"])

    async def job() -> None:
        outcome = next(outcomes, "ok")
        runs.append(outcome)
        if outcome == "fail":
            raise RuntimeError("db down")
        if outcome == "slow":
            await asyncio.sleep(1)

    stop = asyncio.Event()
    task = asyncio.create_task(service.every("sweeper", 0.01, 0.0, 0.05, job, stop))
    await _until(lambda: len(runs) >= 4)
    stop.set()
    await asyncio.wait_for(task, 1)
    assert runs[:4] == ["ok", "fail", "slow", "ok"]


def test_the_canary_runs_only_where_it_can_sign_in(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("WF_CANARY_SECRET_ARN", "WF_CANARY_EMAIL", "WF_CANARY_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    reset_settings()
    assert set(service.jobs()) == {"sweeper", "watcher"}
    monkeypatch.setenv(
        "WF_CANARY_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:1:secret:wf-dev/canary/login"
    )
    reset_settings()
    try:
        assert set(service.jobs()) == {"sweeper", "watcher", "canary"}
    finally:
        monkeypatch.delenv("WF_CANARY_SECRET_ARN")
        reset_settings()


# ---------------------------------------------------------------- metrics through the API


def test_metrics_go_through_the_api_on_ecs_and_never_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, Any]] = []

    class Cw:
        def put_metric_data(self, **kwargs: Any) -> None:
            sent.append(kwargs)

    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.setenv("WF_METRICS_API", "true")
    monkeypatch.setattr(runtime, "_cloudwatch", lambda: Cw())
    runtime.emit_metrics("canary", {"CanarySuccess": 1, "CanaryLatencyMs": 840})
    (call,) = sent
    dims = [{"Name": "service", "Value": "canary"}]
    assert call["Namespace"] == "Watchfloor"
    assert call["MetricData"] == [
        {"MetricName": "CanarySuccess", "Dimensions": dims, "Value": 1.0, "Unit": "Count"},
        {"MetricName": "CanaryLatencyMs", "Dimensions": dims, "Value": 840.0, "Unit": "Milliseconds"},
    ]

    class Broken:
        def put_metric_data(self, **kwargs: Any) -> None:
            raise RuntimeError("AccessDenied")

    monkeypatch.setattr(runtime, "_cloudwatch", lambda: Broken())
    runtime.emit_metrics("sweeper", {"OutboxDead": 0})  # logged, not raised


# ---------------------------------------------------------------- the one-off task runner


def test_run_task_reads_the_result_from_the_last_json_line() -> None:
    rt = _script("run_task")
    lines = ["INFO applying 0007", '{"applied": ["0007"], "logins": ["wf_app"]}']
    assert rt.parse_result(lines) == {"applied": ["0007"], "logins": ["wf_app"]}
    assert rt.parse_result(["{'workspaces': 1, 'alerts': 0}"]) == {
        "workspaces": 1,
        "alerts": 0,
    }  # watcher's print
    assert rt.parse_result(["no result here"]) is None


def test_run_task_follows_the_configured_compute(monkeypatch: pytest.MonkeyPatch) -> None:
    rt = _script("run_task")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(rt, "compute_of", lambda env: "ecs" if env == "dev" else "lambda")
    monkeypatch.setattr(
        rt, "run_ecs", lambda env, job, event, *, region, timeout: calls.append(("ecs", env, job))
    )
    monkeypatch.setattr(
        rt, "invoke_lambda", lambda fn, event, *, region, timeout: calls.append(("lambda", fn))
    )
    rt.run("dev", "seeder", {"seed": False})
    rt.run("demo", "migrator")
    assert calls == [("ecs", "dev", "seeder"), ("lambda", "wf-demo-migrator")]
    with pytest.raises(ValueError, match="unknown job"):
        rt.run("dev", "dispatcher")


def test_the_shipped_configs_run_on_ecs() -> None:
    rt = _script("run_task")
    assert rt.compute_of("dev") == "ecs" and rt.compute_of("demo") == "ecs"


def test_demo_cli_reaches_the_jobs_on_ecs(monkeypatch: pytest.MonkeyPatch) -> None:
    demo = _script("demo")
    calls: list[tuple[str, str, dict[str, Any], int]] = []

    def fake_ecs(env: str, job: str, event: dict[str, Any], *, region: str | None, timeout: int) -> Any:
        calls.append((env, job, event, timeout))
        return {"rescored": 39}

    monkeypatch.setattr(demo.run_task, "compute_of", lambda env: "ecs")
    monkeypatch.setattr(demo.run_task, "run_ecs", fake_ecs)
    assert demo.invoke("wf-dev-watcher", {}, region="us-east-1", timeout=90) == {"rescored": 39}
    assert calls == [("dev", "watcher", {}, 300)]  # a Fargate start needs more than a Lambda read timeout
    with pytest.raises(demo.RemoteError):
        demo.invoke("wf-dev-dispatcher", {}, region="us-east-1")
