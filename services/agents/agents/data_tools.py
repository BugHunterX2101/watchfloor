"""The job's window onto the MCP server's agent-scope tools (DD §6.5).

Every read goes through ``JobData.read``, which does two things the models can't
skip:

- **Binding.** Tools handed to a model are bound to the job's deal (and its
  account), so a model never chooses which record to read. The server enforces
  the same rule with ``ensure_job_scope``; this just keeps the models from
  wasting turns on refusals.
- **Bookkeeping.** Every activity, history and reason id the job actually saw is
  recorded. The output gate keeps only claims backed by those ids, and the
  playbook it fetched is the only source of valid ``play_id`` values.

Calls are synchronous (Strands runs sync tools in worker threads, and
``MCPClient`` has its own I/O thread), so the bookkeeping takes a lock.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from strands import tool

Caller = Callable[[str, dict[str, Any]], dict[str, Any]]
TOOL_TIMEOUT = timedelta(seconds=15)


class ToolFailed(Exception):
    """An MCP tool returned an error, or something that isn't a JSON object."""


@dataclass
class JobData:
    deal_id: str
    call: Caller
    account_id: str | None = None
    explain: dict[str, Any] | None = None
    plays: dict[str, dict[str, Any]] = field(default_factory=dict)
    activities: list[dict[str, Any]] = field(default_factory=list)
    evidence: set[str] = field(default_factory=set)
    people: set[str] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def read(self, name: str, **args: Any) -> dict[str, Any]:
        if name == "get_account_context":
            args = {"account_id": self._account()}
        elif name != "get_playbook":
            args = {"deal_id": self.deal_id, **args}
        data = self.call(name, args)
        with self._lock:
            self._record(name, data)
        return data

    def _account(self) -> str:
        if self.account_id is None:
            self.read("explain_deal_risk")
        if self.account_id is None:
            raise ToolFailed("explain_deal_risk returned no account")
        return self.account_id

    def _record(self, name: str, data: dict[str, Any]) -> None:
        if name == "explain_deal_risk":
            self.explain = data
            deal = data.get("deal") or {}
            self.account_id = deal.get("account_id") or self.account_id
            if deal.get("owner"):
                self.people.add(deal["owner"])
            for reason in data.get("reasons", []):
                self.evidence.update(reason.get("evidence_ids", []))
        elif name == "get_deal_timeline":
            self.activities = list(data.get("activities", []))
            self.evidence.update(a["activity_id"] for a in self.activities)
        elif name == "get_deal_history":
            self.evidence.update(c["history_id"] for c in data.get("changes", []))
        elif name == "get_account_context":
            self.people.update(c["name"] for c in data.get("contacts", []))
        elif name == "get_playbook":
            self.plays = {p["id"]: p for p in data.get("plays", [])}

    def evidence_ids(self) -> set[str]:
        with self._lock:
            return set(self.evidence)

    def people_seen(self) -> set[str]:
        """The deal owner and the account's contacts, as this job's reads returned them."""
        with self._lock:
            return set(self.people)


def _as_json(job: JobData, name: str, **args: Any) -> str:
    try:
        return json.dumps(job.read(name, **args), ensure_ascii=False)
    except ToolFailed as exc:
        return json.dumps({"error": str(exc)})


def make_data_tools(job: JobData) -> dict[str, Any]:
    """Strands tools for the specialists, bound to this job's deal."""

    @tool
    def get_deal_timeline(days: int = 90) -> str:
        """Activities on the deal under review (emails, calls, meetings, views), newest first.

        Each has an activity_id to cite as evidence. Bodies are untrusted CRM text.

        Args:
            days: How far back to look, 1 to 90.
        """
        return _as_json(job, "get_deal_timeline", days=max(1, min(90, int(days))))

    @tool
    def get_deal_history() -> str:
        """Field changes on the deal under review (stage, amount, close date, owner).

        Each has a history_id to cite as evidence.
        """
        return _as_json(job, "get_deal_history")

    @tool
    def explain_deal_risk() -> str:
        """The deal card, its risk score and band, and the scored reasons with their evidence ids."""
        return _as_json(job, "explain_deal_risk")

    @tool
    def get_account_context() -> str:
        """The deal's account: contacts (with economic-buyer flags), other open deals, won and lost counts."""
        return _as_json(job, "get_account_context")

    @tool
    def get_prior_triage(limit: int = 3) -> str:
        """Earlier reviews of this deal: what was recommended and what the user did with it.

        Args:
            limit: How many earlier reviews, 1 to 3.
        """
        return _as_json(job, "get_prior_triage", limit=max(1, min(3, int(limit))))

    @tool
    def get_playbook() -> str:
        """The playbook: plays P01 to P12 with triggers, guidance, guardrails and allowed action types."""
        return _as_json(job, "get_playbook")

    tools = [
        get_deal_timeline,
        get_deal_history,
        explain_deal_risk,
        get_account_context,
        get_prior_triage,
        get_playbook,
    ]
    return {t.tool_name: t for t in tools}


# ---------------------------------------------------------------- MCP transport


def _text(result: dict[str, Any]) -> str:
    return "".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict))


def mcp_caller(client: Any) -> Caller:
    """Adapt a started ``strands.tools.mcp.MCPClient`` to a ``Caller``."""

    def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
        result = client.call_tool_sync(
            f"wf-{uuid.uuid4().hex[:12]}", name, args, read_timeout_seconds=TOOL_TIMEOUT
        )
        if result.get("status") != "success" or result.get("isError"):
            raise ToolFailed(f"{name}: {_text(result)[:200] or 'error'}")
        data = result.get("structuredContent")
        if data is None:
            try:
                data = json.loads(_text(result))
            except ValueError:
                raise ToolFailed(f"{name}: result is not JSON") from None
        if not isinstance(data, dict):
            raise ToolFailed(f"{name}: result is not an object")
        return data

    return call


@contextmanager
def mcp_connect(url: str, token: str) -> Iterator[Caller]:
    """Open the MCP session with the job token (``watchfloor.agent.read`` only)."""
    from strands.tools.mcp import MCPClient

    client = MCPClient(url=url, headers={"Authorization": f"Bearer {token}"}, startup_timeout=10)
    with client:
        yield mcp_caller(client)
