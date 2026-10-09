"""Workers end to end on the seeded database (DD §8.4, §9.2, §10.2; TD §6.4).

The test database is shared with the security suite, which starts its own
Kestrel and Harbor triage jobs. So these tests process only the outbox rows
they create (never a whole-table drain), and triage runs on Northgate.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
from watchfloor_core import db
from watchfloor_core.repos import jobs, misc, outbox, proposals, scores
from watchfloor_core.services import scoring
from watchfloor_core.settings import get_settings
from workers import canary, dispatcher, sweeper, watcher
from workers.agentcore_client import AgentTimeout
from workers.drafts import DraftRequest, template_draft
from workers.errors import GiveUp, RetryLater

from tests.conftest import FROZEN_NOW, KESTREL, PRIYA, WS
from tests.integration.test_auth_server import PASSWORD, auth_app, emails  # noqa: F401 - shared fixtures
from tests.mcp_client import Mcp

NORTHGATE = "dl_01J9NORTHGATE00000000000"


@dataclass
class Fakes:
    """Stand-ins for Slack, AgentCore, the auth server, Bedrock and AgentCore Memory."""

    slack_error: Exception | None = None
    agent_result: Any = None
    agent_error: Exception | None = None
    posts: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    invocations: list[tuple[dict[str, Any], str]] = field(default_factory=list)
    drafts: list[DraftRequest] = field(default_factory=list)
    memories: list[str] = field(default_factory=list)

    def deps(self) -> dispatcher.Deps:
        async def slack(channel: str, message: dict[str, Any]) -> None:
            if self.slack_error:
                raise self.slack_error
            self.posts.append((channel, message))

        async def invoke(payload: dict[str, Any], session_id: str) -> Any:
            self.invocations.append((payload, session_id))
            if self.agent_error:
                raise self.agent_error
            return self.agent_result

        async def mint(ws: str, job_id: str) -> str:
            return f"job-token:{ws}:{job_id}"

        async def draft(req: DraftRequest) -> str:
            self.drafts.append(req)
            return await template_draft(req)

        async def remember(ws: str, user_id: str, text: str) -> None:
            self.memories.append(text)

        return dispatcher.Deps(
            slack=slack, invoke_agent=invoke, mint_job_token=mint, draft=draft, remember=remember
        )


async def _enqueue(kind: str, payload: dict[str, Any], *, at: datetime = FROZEN_NOW) -> int:
    async with db.ws_tx(WS) as conn:
        oid = await outbox.enqueue(
            conn,
            workspace_id=WS,
            kind=kind,
            payload=payload,
            dedupe_key=f"test:{secrets.token_hex(6)}",
            at=at,
        )
    assert oid is not None
    return oid


async def _row(oid: int) -> Any:
    async with db.ws_tx(WS) as conn:
        return await outbox.get(conn, oid)


async def _rewind(oid: int) -> None:
    """Make a backing-off row due again, as if its wait had passed."""
    async with db.ws_tx(WS) as conn:
        await conn.execute("UPDATE outbox SET next_attempt_at = $2 WHERE id = $1", oid, FROZEN_NOW)


async def _new_job(*, created_at: datetime = FROZEN_NOW) -> tuple[str, int]:
    async with db.ws_tx(WS) as conn:
        jid = await jobs.create(
            conn, workspace_id=WS, user_id=PRIYA, deal_id=NORTHGATE, trace_id="trace-test", at=created_at
        )
        oid = await outbox.enqueue(
            conn,
            workspace_id=WS,
            kind="triage_job",
            payload={"job_id": jid},
            dedupe_key=f"job:{jid}",
            at=created_at,
        )
    assert oid is not None
    return jid, oid


async def _job(jid: str) -> Any:
    async with db.ws_tx(WS) as conn:
        return await jobs.get(conn, jid)


async def _triage_output(*, evidence: str | None = None) -> dict[str, Any]:
    if evidence is None:
        async with db.ws_tx(WS) as conn:
            evidence = sorted(await misc.evidence_ids(conn, NORTHGATE))[0]
    return {
        "voice_summary": "Northgate has gone quiet since the pricing call. Re-engage the economic buyer.",
        "risk_drivers": [
            {"factor": "R", "claim": "No reply since the pricing call", "evidence_ids": [evidence]}
        ],
        "recommended_actions": [
            {
                "action_type": "create_task",
                "params": {"assignee": "Maya"},
                "rationale": "Book a call with the economic buyer this week",
                "play_id": "P01",
                "confidence": 0.8,
            }
        ],
        "confidence": 0.72,
    }


# ---------------------------------------------------------------- watcher


@pytest.fixture
async def standing_order(seeded: str, mcp: Mcp) -> AsyncIterator[str]:
    rule = await mcp.ok("set_standing_order", {"min_score": 65})
    yield rule["rule_id"]
    await mcp.ok("remove_standing_order", {"rule_id": rule["rule_id"]})


async def test_watcher_scores_alerts_once_and_hands_off(standing_order: str, sqs_stub: list[int]) -> None:
    async with db.ws_tx(WS) as conn:
        kestrel_before = (await scores.get(conn, KESTREL))["score"]

    first = await watcher.run()
    assert first["failed"] == 0 and first["scored"] > 0

    async with db.ws_tx(WS) as conn:
        rows = await conn.fetch(
            "SELECT id, kind, payload FROM outbox WHERE dedupe_key LIKE $1 ORDER BY id", f"{standing_order}:%"
        )
        matching = await conn.fetchval(
            """SELECT count(*) FROM risk_scores rs JOIN deals d ON d.id = rs.deal_id
               WHERE d.stage NOT IN ('won', 'lost') AND rs.score >= 65"""
        )
        kestrel_after = (await scores.get(conn, KESTREL))["score"]
    assert matching >= 1  # Kestrel (71) at least; earlier tests may have moved Harbor & Pine
    assert first["alerts"] == len(rows) == matching
    assert all(r["kind"] == "slack_message" for r in rows)
    assert "Standing order" in str(rows[0]["payload"]["message"])
    assert {r["id"] for r in rows} <= set(sqs_stub), "alerts must be handed to the queue"
    assert kestrel_after == kestrel_before, "recompute at the same instant must not move scores"

    again = await watcher.run()
    assert again["alerts"] == 0, "one alert per rule and deal per 24 hours"


async def test_daily_snapshot_runs_once_after_0005_local(seeded: str) -> None:
    async with db.pool().acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("SELECT set_config('app.workspace_id', $1, true)", WS)
            later = FROZEN_NOW + timedelta(days=30)  # a day with no trend rows yet
            early = later.replace(hour=0, minute=4)
            assert await scoring.daily_snapshot_if_due(conn, local_now=early) == 0
            due = early.replace(minute=5)
            assert await scoring.daily_snapshot_if_due(conn, local_now=due) > 0
            assert await scoring.daily_snapshot_if_due(conn, local_now=due.replace(hour=9)) == 0
        finally:
            await tx.rollback()


# ---------------------------------------------------------------- dispatcher: Slack


async def test_slack_row_is_delivered_once(seeded: str) -> None:
    fakes = Fakes()
    oid = await _enqueue(
        "slack_message", {"channel": "#deals-at-risk", "message": {"text": "hello", "blocks": []}}
    )
    assert await dispatcher.process(WS, oid, fakes.deps()) == "sent"
    assert await dispatcher.process(WS, oid, fakes.deps()) == "skipped", (
        "a duplicate message must not re-post"
    )
    assert fakes.posts == [("#deals-at-risk", {"text": "hello", "blocks": []})]
    row = await _row(oid)
    assert row["status"] == "sent" and row["attempts"] == 1


async def test_transient_failures_back_off_then_go_dead(seeded: str) -> None:
    fakes = Fakes(slack_error=RetryLater("slack 503"))
    oid = await _enqueue("slack_message", {"channel": "#c", "message": {"text": "x"}})

    assert await dispatcher.process(WS, oid, fakes.deps()) == "retry"
    row = await _row(oid)
    assert row["next_attempt_at"] == FROZEN_NOW + timedelta(seconds=30) and row["last_error"] == "slack 503"
    assert await dispatcher.process(WS, oid, fakes.deps()) == "skipped", "backing off"

    for _ in range(3):  # attempts 2-4 retry after 2, 10 and 30 minutes
        await _rewind(oid)
        assert await dispatcher.process(WS, oid, fakes.deps()) == "retry"
    await _rewind(oid)
    assert await dispatcher.process(WS, oid, fakes.deps()) == "dead"
    row = await _row(oid)
    assert row["status"] == "dead" and row["attempts"] == 5


async def test_retry_after_longer_than_backoff_wins(seeded: str) -> None:
    fakes = Fakes(slack_error=RetryLater("slack rate limited", retry_after=timedelta(seconds=90)))
    oid = await _enqueue("slack_message", {"channel": "#c", "message": {"text": "x"}})
    assert await dispatcher.process(WS, oid, fakes.deps()) == "retry"
    assert (await _row(oid))["next_attempt_at"] == FROZEN_NOW + timedelta(seconds=90)


async def test_permanent_failure_goes_dead_at_once(seeded: str) -> None:
    fakes = Fakes(slack_error=GiveUp("slack 400: invalid_blocks"))
    oid = await _enqueue("slack_message", {"channel": "#c", "message": {"text": "x"}})
    assert await dispatcher.process(WS, oid, fakes.deps()) == "dead"
    row = await _row(oid)
    assert row["attempts"] == 1 and "invalid_blocks" in row["last_error"]


# ---------------------------------------------------------------- dispatcher: drafts


async def test_draft_is_written_and_kept_across_slack_retries(seeded: str) -> None:
    payload = {
        "proposal_id": "pr_test",
        "deal_id": KESTREL,
        "user_id": PRIYA,
        "account": "Kestrel Biotech",
        "intent": "Answer their security questions",
    }
    oid = await _enqueue("draft_followup", payload)
    fakes = Fakes(slack_error=RetryLater("slack 502"))
    assert await dispatcher.process(WS, oid, fakes.deps()) == "retry"
    assert len(fakes.drafts) == 1
    req = fakes.drafts[0]
    assert req.account == "Kestrel Biotech" and req.intent == payload["intent"] and req.activities
    saved = (await _row(oid))["payload"]["draft"]
    assert saved.startswith("Subject:")

    fakes.slack_error = None
    await _rewind(oid)
    assert await dispatcher.process(WS, oid, fakes.deps()) == "sent"
    assert len(fakes.drafts) == 1, "the retry must reuse the stored draft, not call the model again"
    channel, message = fakes.posts[0]
    assert channel.startswith("#")
    assert message["blocks"][0]["text"]["text"].startswith("DRAFT")


# ---------------------------------------------------------------- dispatcher: deep triage


async def test_triage_job_runs_gates_and_stores(seeded: str, sqs_stub: list[int]) -> None:
    jid, oid = await _new_job()
    fakes = Fakes(agent_result=await _triage_output())
    assert await dispatcher.process(WS, oid, fakes.deps()) == "sent"

    payload, session = fakes.invocations[0]
    assert payload == {
        "job_id": jid,
        "deal_id": NORTHGATE,
        "workspace_id": WS,
        "user_ref": PRIYA,
        "mcp_token": f"job-token:{WS}:{jid}",
        "trace_id": "trace-test",
    }
    assert session == f"triage-{jid}-{NORTHGATE}" and len(session) >= 33
    job = await _job(jid)
    assert job["status"] == "done" and job["result"]["confidence"] == 0.72

    async with db.ws_tx(WS) as conn:
        plan = await conn.fetchrow("SELECT id, kind FROM outbox WHERE dedupe_key = $1", f"jobplan:{jid}")
    assert plan["kind"] == "slack_message" and plan["id"] in sqs_stub, (
        "the Slack plan is published after commit"
    )
    assert fakes.memories and fakes.memories[0].endswith(f"({NORTHGATE}): drivers R; recommended P01.")

    assert await dispatcher.process(WS, oid, fakes.deps()) == "skipped"
    assert len(fakes.invocations) == 1


async def test_triage_rejects_unsupported_evidence(seeded: str) -> None:
    jid, oid = await _new_job()
    fakes = Fakes(agent_result=await _triage_output(evidence="act_made_up"))
    assert await dispatcher.process(WS, oid, fakes.deps()) == "sent"
    job = await _job(jid)
    assert job["status"] == "failed" and "unknown evidence" in job["error"]
    assert job["result"] is None and not fakes.memories


async def test_triage_timeout_and_misconfiguration(seeded: str) -> None:
    jid, oid = await _new_job()
    assert await dispatcher.process(WS, oid, Fakes(agent_error=AgentTimeout("slow")).deps()) == "sent"
    assert (await _job(jid))["status"] == "timed_out"

    jid, oid = await _new_job()
    unconfigured = Fakes(agent_error=GiveUp("no agent runtime configured"))
    assert await dispatcher.process(WS, oid, unconfigured.deps()) == "dead", "config errors must alarm"
    job = await _job(jid)
    assert job["status"] == "failed" and "no agent runtime" in job["error"]


async def test_triage_transient_error_keeps_the_job_running(seeded: str) -> None:
    jid, oid = await _new_job()
    fakes = Fakes(agent_error=RetryLater("agentcore ThrottlingException"))
    assert await dispatcher.process(WS, oid, fakes.deps()) == "retry"
    assert (await _job(jid))["status"] == "running"

    fakes.agent_error, fakes.agent_result = None, await _triage_output()
    await _rewind(oid)
    assert await dispatcher.process(WS, oid, fakes.deps()) == "sent"
    assert (await _job(jid))["status"] == "done"


# ---------------------------------------------------------------- SQS entry point


async def test_sqs_batch_reports_only_crashes(seeded: str) -> None:
    oid = await _enqueue("slack_message", {"channel": "#c", "message": {"text": "x"}})
    event = {
        "Records": [
            {"messageId": "m1", "body": f'{{"ws": "{WS}", "outbox_id": {oid}}}'},
            {"messageId": "m2", "body": "not json"},
            {"messageId": "m3", "body": '{"ws": "ws_nowhere", "outbox_id": 1}'},
        ]
    }
    result = await dispatcher.handle_batch(event, Fakes().deps())
    assert result == {"batchItemFailures": [{"itemIdentifier": "m3"}]}
    assert (await _row(oid))["status"] == "sent"


# ---------------------------------------------------------------- sweeper


async def test_sweeper_expires_times_out_and_republishes(seeded: str, sqs_stub: list[int]) -> None:
    stale_job, stale_oid = await _new_job(created_at=FROZEN_NOW - timedelta(minutes=10))
    async with db.ws_tx(WS) as conn:
        # The stale job's own row was delivered long ago; only the job is left hanging.
        await conn.execute("UPDATE outbox SET status = 'sent' WHERE id = $1", stale_oid)
        pid = await proposals.create(
            conn,
            workspace_id=WS,
            user_id=PRIYA,
            deal_id=KESTREL,
            action_type="notify_channel",
            params={"message": "x"},
            params_hash=secrets.token_bytes(32),
            token_hash=secrets.token_bytes(32),
            preview={"account": "Kestrel Biotech"},
            expires_at=FROZEN_NOW - timedelta(seconds=1),
            at=FROZEN_NOW - timedelta(minutes=6),
        )
    message = {"channel": "#c", "message": {"text": "x"}}
    lost = await _enqueue("slack_message", message, at=FROZEN_NOW - timedelta(minutes=2))
    fresh = await _enqueue("slack_message", message)

    stats = await sweeper.run()
    assert stats["expired_proposals"] >= 1 and stats["timed_out_jobs"] >= 1
    assert lost in sqs_stub, "a row pending 60 s past its attempt time is handed back to the queue"
    assert fresh not in sqs_stub, "rows inside the 60 s grace belong to the fast path"
    assert (await _job(stale_job))["status"] == "timed_out"
    async with db.ws_tx(WS) as conn:
        assert await conn.fetchval("SELECT status FROM action_proposals WHERE id = $1", pid) == "expired"
        await conn.execute("UPDATE outbox SET status = 'sent' WHERE id = ANY($1)", [lost, fresh])


# ---------------------------------------------------------------- canary


async def test_canary_signs_in_lists_tools_and_briefs(app: Any, auth_app: Any, emails: list[str]) -> None:  # noqa: F811
    s = get_settings().model_copy(
        update={"canary_email": emails[3], "canary_password": PASSWORD, "mcp_url": "http://localhost/mcp"}
    )
    result = await canary.run(
        settings=s,
        auth_transport=httpx.ASGITransport(app=auth_app, client=("10.0.1.1", 50000)),
        mcp_transport=httpx.ASGITransport(app=app),
    )
    assert result["ok"], result
    assert result["stage"] == "done"


async def test_canary_reports_where_it_failed(app: Any, auth_app: Any) -> None:  # noqa: F811
    s = get_settings().model_copy(
        update={
            "canary_email": f"nobody-{secrets.token_hex(4)}@example.com",
            "canary_password": "wrong-password",
        }
    )
    result = await canary.run(
        settings=s,
        auth_transport=httpx.ASGITransport(app=auth_app, client=("10.0.1.2", 50000)),
        mcp_transport=httpx.ASGITransport(app=app),
    )
    assert not result["ok"]
    assert result["stage"] == "sign-in" and "login: HTTP 401" in result["detail"]
