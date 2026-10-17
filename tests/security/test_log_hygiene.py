"""Secrets, tokens and CRM text stay out of logs and error messages (plan phase 2).

Each test hands the code a distinctive fake secret, makes its failure paths run, and then looks for that
secret in every captured log record and in the exceptions that carry the failure upwards.
"""

from __future__ import annotations

import logging

import httpx
import pytest
from workers import drafts
from workers.errors import GiveUp, RetryLater
from workers.slack import SlackWebhook

from tests.conftest import HARBOR, TokenFactory
from tests.mcp_client import Mcp
from tests.unit.test_workers_unit import MESSAGE, REQ

WEBHOOK = "https://hooks.slack.test/services/T0SECRET/B0SECRET/XSECRETWEBHOOKX"
GEMINI_KEY = "AIzaSECRETGEMINIKEY0123456789"
NOTE = "SENSITIVE-NOTE-4711 jane.doe@customer.example is the real buyer"
CHANNEL_POST = "SENSITIVE-POST-4712 about the Harbor renewal"


def _leaks(secret: str, *texts: str) -> list[str]:
    return [t for t in texts if secret in t]


@pytest.mark.parametrize("failure", ["unreachable", "429", "500", "400"])
async def test_a_slack_failure_never_repeats_the_webhook_url(
    failure: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    def reply(request: httpx.Request) -> httpx.Response:
        if failure == "unreachable":
            raise httpx.ConnectError(f"All connection attempts failed for {request.url}", request=request)
        return httpx.Response(int(failure), text="invalid_blocks")

    hook = SlackWebhook(WEBHOOK, transport=httpx.MockTransport(reply))
    with pytest.raises((RetryLater, GiveUp)) as caught:
        await hook("#deals-at-risk", MESSAGE)

    assert not _leaks("XSECRETWEBHOOKX", str(caught.value), repr(caught.value), caplog.text)
    if (
        failure == "unreachable"
    ):  # the original error names the URL, so it must not be chained for a traceback
        assert caught.value.__cause__ is None and caught.value.__suppress_context__


@pytest.mark.parametrize("failure", ["unreachable", "429", "503", "400"])
async def test_a_gemini_failure_never_repeats_the_api_key(
    failure: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    def reply(request: httpx.Request) -> httpx.Response:
        if failure == "unreachable":
            raise httpx.ConnectError(f"cannot reach {request.url} with {request.headers['x-goog-api-key']}")
        quoted = {"error": {"message": f"Invalid key {request.headers['x-goog-api-key']} in request"}}
        return httpx.Response(int(failure), json=quoted)

    with pytest.raises((RetryLater, GiveUp)) as caught:
        await drafts.gemini_draft(
            REQ, model_id="gemini-test", api_key=GEMINI_KEY, transport=httpx.MockTransport(reply)
        )

    assert not _leaks(GEMINI_KEY, str(caught.value), repr(caught.value), caplog.text)


async def test_a_rejected_bearer_token_is_never_logged(
    client: httpx.AsyncClient, token_factory: TokenFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    rejected = [
        token_factory(expires_in=-120),
        token_factory(aud="https://other.example/mcp"),
        "not.a.jwt-SECRETBEARERPART",
    ]
    for token in rejected:
        r = await Mcp(client, token).post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 401, r.text
        for part in (p for p in token.split(".") if len(p) >= 20):  # a JWT's header, claims and signature
            assert part not in caplog.text and part not in r.text
    assert "SECRETBEARERPART" not in caplog.text


async def test_crm_text_and_confirmation_tokens_stay_out_of_the_logs(
    seeded: str, mcp: Mcp, sqs_stub: list[int], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await mcp.ok("log_deal_update", {"deal_id": HARBOR, "note": NOTE})
    proposal = await mcp.ok(
        "propose_action", {"deal_id": HARBOR, "action_type": "notify_channel", "message": CHANNEL_POST}
    )
    token = proposal["confirmation_token"]
    await mcp.ok("confirm_action", {"confirmation_token": token})
    await mcp.call("confirm_action", {"confirmation_token": token})  # a replay logs nothing sensitive either

    assert not _leaks("SENSITIVE-NOTE-4711", caplog.text), "a rep's note is CRM data"
    assert not _leaks("jane.doe@customer.example", caplog.text), "a buyer's e-mail is personal data"
    assert not _leaks("SENSITIVE-POST-4712", caplog.text)
    assert not _leaks(token, caplog.text), "a confirmation token is a one-time credential"
