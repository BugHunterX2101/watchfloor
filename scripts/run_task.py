"""Run a one-off job in a deployed environment: the migrator, the seeder or one watcher run.

    uv run python scripts/run_task.py --env dev migrator
    uv run python scripts/run_task.py --env dev seeder '{"seed": true, "bootstrap": true}'
    uv run python scripts/run_task.py --env dev watcher

Where the environment runs (``compute`` in ``infra/config/<env>.yaml``, ADR-025):

- ``lambda``: invokes ``wf-<env>-<job>`` synchronously, once (a reset must not repeat on a retry);
- ``ecs``: starts the job's task definition on Fargate in the private subnets (the Net stack's
  outputs), waits for it to stop, prints its log to stderr and reads the result from the last
  line, which the job prints as JSON. The watcher runs in the workers task definition with its own
  command.

Either way the result goes to stdout as JSON and a failure exits 1. ``scripts/demo.py`` uses the
same calls for its remote commands. Credentials: the caller's (CI: the GitHub deploy role).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
JOBS = ("migrator", "seeder", "watcher")
# job → (task definition family suffix, container name, command); the seeder takes its event as an argument
TASKS: dict[str, tuple[str, str, list[str]]] = {
    "migrator": ("migrator", "migrator", ["python", "-m", "workers.migrator"]),
    "seeder": ("seeder", "seeder", ["python", "-m", "auth_server.seeder"]),
    "watcher": ("workers", "workers", ["python", "-m", "workers.watcher"]),
}
TIMEOUT_S = {"migrator": 600, "seeder": 900, "watcher": 300}  # a Fargate start is ~1 minute of it


class RemoteError(Exception):
    """An invoke or a task that failed, or a job that raised."""


def config_value(env: str, key: str) -> str | None:
    """A top-level scalar from ``infra/config/<env>.yaml`` (no YAML parser in the dev environment)."""
    path = ROOT / "infra" / "config" / f"{env}.yaml"
    if not path.is_file():
        return None
    match = re.search(rf"^{key}:[ \t]*([^\s#]+)", path.read_text(encoding="utf-8"), re.MULTILINE)
    value = match.group(1).strip("'\"") if match else None
    return None if value in ("null", "~") else value


def compute_of(env: str) -> str:
    return config_value(env, "compute") or "lambda"


def invoke_lambda(function: str, event: dict[str, Any], *, region: str | None, timeout: int) -> Any:
    """Invoke ``function`` synchronously, once."""
    import boto3
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError

    config = Config(region_name=region, read_timeout=timeout, retries={"max_attempts": 0})
    client = boto3.client("lambda", config=config)
    try:
        r = client.invoke(FunctionName=function, Payload=json.dumps(event).encode())
    except (BotoCoreError, ClientError) as exc:
        raise RemoteError(f"{function}: {exc}") from None
    body = json.loads(r["Payload"].read() or b"null")
    if r.get("FunctionError"):
        raise RemoteError(f"{function}: {body.get('errorType')}: {body.get('errorMessage')}")
    return body


def parse_result(lines: list[str]) -> Any:
    """The job's result: its last line that reads as JSON (or, from an older image, a Python literal)."""
    for line in reversed(lines):
        text = line.strip()
        if not text.startswith(("{", "[")):
            continue
        try:
            return json.loads(text)
        except ValueError:
            pass
        try:
            return ast.literal_eval(text)
        except (ValueError, SyntaxError):
            continue
    return None


def _log_lines(session: Any, task_definition: dict[str, Any], container: str, task_id: str) -> list[str]:
    options = next(c for c in task_definition["containerDefinitions"] if c["name"] == container)[
        "logConfiguration"
    ]["options"]
    logs = session.client("logs")
    stream = f"{options['awslogs-stream-prefix']}/{container}/{task_id}"
    for _ in range(5):  # the last lines can land a moment after the task stops
        lines: list[str] = []
        token: str | None = None
        try:
            while True:
                kwargs: dict[str, Any] = {
                    "logGroupName": options["awslogs-group"],
                    "logStreamName": stream,
                    "startFromHead": True,
                }
                if token:
                    kwargs["nextToken"] = token
                page = logs.get_log_events(**kwargs)
                lines += [e["message"] for e in page["events"]]
                if page.get("nextForwardToken") in (None, token):
                    break
                token = page["nextForwardToken"]
        except logs.exceptions.ResourceNotFoundException:
            lines = []
        if lines:
            return lines
        time.sleep(3)
    return []


def run_ecs(env: str, job: str, event: dict[str, Any] | None, *, region: str | None, timeout: int) -> Any:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError, WaiterError

    family, container, command = TASKS[job]
    if job == "seeder":
        command = [*command, json.dumps(event or {})]
    session = boto3.session.Session(region_name=region)
    try:
        stack = session.client("cloudformation").describe_stacks(StackName=f"wf-{env}-net")["Stacks"][0]
        out = {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}
        cluster, subnets, sg = out["ClusterName"], out["TaskSubnets"].split(","), out["TaskSecurityGroup"]
    except (BotoCoreError, ClientError, KeyError) as exc:
        raise RemoteError(f"wf-{env}-net has no ECS outputs (compute: ecs deployed?): {exc}") from None
    ecs = session.client("ecs")
    try:
        definition = ecs.describe_task_definition(taskDefinition=f"wf-{env}-{family}")["taskDefinition"]
        started = ecs.run_task(
            cluster=cluster,
            taskDefinition=definition["taskDefinitionArn"],
            launchType="FARGATE",
            count=1,
            startedBy="run_task.py",
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": subnets,
                    "securityGroups": [sg],
                    "assignPublicIp": "DISABLED",
                }
            },
            overrides={"containerOverrides": [{"name": container, "command": command}]},
        )
    except (BotoCoreError, ClientError) as exc:
        raise RemoteError(f"wf-{env}-{family}: {exc}") from None
    if started.get("failures") or not started.get("tasks"):
        raise RemoteError(f"wf-{env}-{family}: run_task failed: {started.get('failures')}")
    arn = started["tasks"][0]["taskArn"]
    task_id = arn.rsplit("/", 1)[-1]
    print(f"{job}: task {task_id} started on {cluster}", file=sys.stderr, flush=True)
    try:
        ecs.get_waiter("tasks_stopped").wait(
            cluster=cluster, tasks=[arn], WaiterConfig={"Delay": 6, "MaxAttempts": max(1, timeout // 6)}
        )
    except WaiterError as exc:
        raise RemoteError(f"{job}: task {task_id} still running after {timeout} s ({exc})") from None
    task = ecs.describe_tasks(cluster=cluster, tasks=[arn])["tasks"][0]
    lines = _log_lines(session, definition, container, task_id)
    for line in lines:
        print(f"  | {line}", file=sys.stderr)
    code = next((c.get("exitCode") for c in task["containers"] if c["name"] == container), None)
    if code != 0:
        reason = task.get("stoppedReason", "")
        raise RemoteError(f"{job}: task {task_id} exited {code} ({reason})")
    return parse_result(lines)


def run(env: str, job: str, event: dict[str, Any] | None = None, *, timeout: int | None = None) -> Any:
    """Run ``job`` in ``env`` on its compute and return its result."""
    if job not in JOBS:
        raise ValueError(f"unknown job {job!r} (one of {', '.join(JOBS)})")
    region = config_value(env, "region")  # the stacks' region, not the CLI's default
    timeout = timeout or TIMEOUT_S[job]
    if compute_of(env) == "ecs":
        return run_ecs(env, job, event, region=region, timeout=timeout)
    return invoke_lambda(f"wf-{env}-{job}", event or {}, region=region, timeout=timeout)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", required=True, choices=("dev", "demo"))
    ap.add_argument("job", choices=JOBS)
    ap.add_argument("event", nargs="?", default="{}", help="the seeder's event, as JSON")
    opts = ap.parse_args(argv)
    try:
        event = json.loads(opts.event)
        result = run(opts.env, opts.job, event if isinstance(event, dict) else {})
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except RemoteError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
