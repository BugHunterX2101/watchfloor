"""Invoke the deep-triage agent and collect its final structured output (DD §10.2).

Contract with ``services/agents``: the runtime streams Server-Sent Events whose
``data:`` lines are JSON objects, and the last one carrying an ``output`` key
holds the ``TriageOutput`` (``{"type": "result", "output": {...}}``). A plain
``application/json`` body of ``{"output": {...}}``, or the bare output, is
accepted too. A stream that ends on an ``error`` event instead fails the job
(``"code": "timed_out"`` is the agent's own 90 s stop and marks it timed out).
Whatever comes back is only *raw* output: the schema gate and
evidence check run afterwards in ``watchfloor_core.services.triage``.

In AWS the dispatcher calls AgentCore Runtime (``WF_AGENT_RUNTIME_ARN``) or,
where AgentCore is refused, the agents' Lambda function (``WF_AGENT_FUNCTION``,
ADR-023), which returns the same events as ``{"events": [...]}``. Locally it
can POST to the agents container's ``/invocations`` (``WF_AGENT_URL``). With
none of them, triage fails fast with a clear reason.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from watchfloor_core.settings import Settings

from .errors import GiveUp, RetryLater

AGENT_TIMEOUT_S = 100.0  # the agent stops itself at 90 s; this is the backstop
SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"
THROTTLED = {"ThrottlingException", "ServiceQuotaExceededException", "ServiceUnavailableException"}
LAMBDA_THROTTLED = {"TooManyRequestsException", "ServiceException", "ResourceNotReadyException"}

Invoker = Callable[[dict[str, Any], str], Awaitable[Any]]


class AgentTimeout(Exception):
    """No final output before the deadline: the job ends as timed_out."""


def final_output(content_type: str, body: bytes) -> Any:
    text = body.decode("utf-8", errors="replace")
    if "text/event-stream" in content_type:
        found: Any = None
        error: dict[str, Any] | None = None
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except ValueError:
                continue
            if isinstance(event, dict) and "output" in event:
                found = event["output"]
            elif isinstance(event, dict) and "error" in event:
                # ours: {"type": "error", "code", "error"}; the runtime's: {"error", "error_type"}
                error = event
        if found is None:
            if error is not None and error.get("code") == "timed_out":
                raise AgentTimeout(str(error["error"])[:200])
            if error is not None:
                raise GiveUp(f"agent error: {str(error['error'])[:200]}")
            raise GiveUp("agent stream ended without a final output")
        return found
    try:
        obj = json.loads(text)
    except ValueError:
        raise GiveUp("agent returned a body that is not JSON") from None
    return obj.get("output", obj) if isinstance(obj, dict) else obj


async def invoke_http(
    url: str,
    payload: dict[str, Any],
    session_id: str,
    *,
    timeout_s: float = AGENT_TIMEOUT_S,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Any:
    timeout = httpx.Timeout(timeout_s, connect=5.0)
    try:
        async with httpx.AsyncClient(transport=transport, timeout=timeout) as http:
            r = await http.post(
                url,
                json=payload,
                headers={SESSION_HEADER: session_id, "Accept": "text/event-stream, application/json"},
            )
    except httpx.TimeoutException:
        raise AgentTimeout("agent did not answer in time") from None
    except httpx.HTTPError as exc:
        raise RetryLater(f"agent unreachable: {type(exc).__name__}") from None
    if r.status_code == 429 or r.status_code >= 500:
        raise RetryLater(f"agent {r.status_code}")
    if r.status_code >= 400:
        raise GiveUp(f"agent {r.status_code}: {r.text[:200]}")
    return final_output(r.headers.get("content-type", ""), r.content)


def _invoke_agentcore(arn: str, payload: dict[str, Any], session_id: str, timeout_s: float) -> Any:
    import boto3  # lazy: only in AWS
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError, ReadTimeoutError

    client = boto3.client(
        "bedrock-agentcore",
        config=Config(read_timeout=timeout_s, connect_timeout=5, retries={"max_attempts": 1}),
    )
    try:
        resp = client.invoke_agent_runtime(
            agentRuntimeArn=arn,
            runtimeSessionId=session_id,  # ⚠ Verify (S6): minimum length (ours is > 50 chars)
            payload=json.dumps(payload).encode(),
            contentType="application/json",
            accept="text/event-stream, application/json",
        )
        body = resp["response"].read()
    except ReadTimeoutError:
        raise AgentTimeout("agent did not answer in time") from None
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in THROTTLED:
            raise RetryLater(f"agentcore {code}") from None
        raise GiveUp(f"agentcore {code}") from None
    except BotoCoreError as exc:
        raise RetryLater(f"agentcore unreachable: {type(exc).__name__}") from None
    return final_output(resp.get("contentType", ""), body)


def lambda_output(body: bytes) -> Any:
    """The agents' Lambda result, ``{"events": [...]}``, read like the runtime's SSE stream."""
    try:
        obj = json.loads(body)
    except ValueError:
        raise GiveUp("agent function returned a body that is not JSON") from None
    events = obj.get("events") if isinstance(obj, dict) else None
    if not isinstance(events, list):
        raise GiveUp("agent function returned no events")
    stream = "\n".join(f"data: {json.dumps(event)}" for event in events)
    return final_output("text/event-stream", stream.encode())


def _invoke_lambda(name: str, payload: dict[str, Any], timeout_s: float) -> Any:
    import boto3  # lazy: only in AWS
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError, ReadTimeoutError

    # One attempt: a retry would run a 90 s review twice. The job's own retries come from SQS.
    client = boto3.client(
        "lambda",
        config=Config(read_timeout=timeout_s, connect_timeout=5, retries={"total_max_attempts": 1}),
    )
    try:
        resp = client.invoke(FunctionName=name, Payload=json.dumps(payload).encode())
        body = resp["Payload"].read()
    except ReadTimeoutError:
        raise AgentTimeout("agent did not answer in time") from None
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in LAMBDA_THROTTLED:
            raise RetryLater(f"lambda {code}") from None
        raise GiveUp(f"lambda {code}") from None
    except BotoCoreError as exc:
        raise RetryLater(f"lambda unreachable: {type(exc).__name__}") from None
    if resp.get("FunctionError"):
        message = body.decode("utf-8", errors="replace")
        if "timed out" in message:
            raise AgentTimeout("agent function timed out")
        raise GiveUp(f"agent function failed: {message[:200]}")
    return lambda_output(body)


def invoker_from_settings(s: Settings) -> Invoker:
    if s.agent_runtime_arn:
        arn = s.agent_runtime_arn

        async def agentcore(payload: dict[str, Any], session_id: str) -> Any:
            return await asyncio.to_thread(_invoke_agentcore, arn, payload, session_id, AGENT_TIMEOUT_S)

        return agentcore
    if s.agent_function:
        name = s.agent_function

        async def function(payload: dict[str, Any], _session_id: str) -> Any:
            return await asyncio.to_thread(_invoke_lambda, name, payload, AGENT_TIMEOUT_S)

        return function
    if s.agent_url:
        url = s.agent_url

        async def local(payload: dict[str, Any], session_id: str) -> Any:
            return await invoke_http(url, payload, session_id)

        return local

    async def unconfigured(_payload: dict[str, Any], _session_id: str) -> Any:
        raise GiveUp(
            "no agent runtime configured: set WF_AGENT_RUNTIME_ARN or WF_AGENT_FUNCTION, or WF_AGENT_URL locally"
        )

    return unconfigured
