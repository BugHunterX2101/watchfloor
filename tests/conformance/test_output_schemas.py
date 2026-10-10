"""CF-15: every tool's structuredContent validates against its declared outputSchema, with a text twin."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import orjson
from jsonschema import Draft202012Validator

from tests.conftest import AGENT_SCOPE, FROZEN_NOW, HARBOR, KESTREL, TokenFactory
from tests.mcp_client import Mcp


def _check(tool: dict[str, Any], result: dict[str, Any]) -> None:
    assert not result.get("isError"), f"{tool['name']}: {result['structuredContent']}"
    structured = result["structuredContent"]
    errors = sorted(Draft202012Validator(tool["outputSchema"]).iter_errors(structured), key=str)
    assert not errors, f"{tool['name']}: {[e.message for e in errors[:3]]}"
    text = [c for c in result["content"] if c["type"] == "text"]
    assert text and orjson.loads(text[0]["text"]) == structured, tool["name"]
    assert next(iter(structured)) == "summary", f"{tool['name']}: summary must come first"


async def test_cf15_every_tool_result_matches_its_output_schema(
    seeded: str, mcp: Mcp, token_factory: TokenFactory
) -> None:
    tools = {t["name"]: t for t in await mcp.tools()}
    soon = (FROZEN_NOW + timedelta(days=3)).date().isoformat()
    seen: set[str] = set()

    async def run(name: str, args: dict[str, Any], caller: Mcp = mcp, schema: dict | None = None) -> dict:
        result = await caller.call(name, args)
        _check(schema or tools[name], result)
        seen.add(name)
        return result["structuredContent"]

    await run("get_risk_briefing", {})
    explained = await run("explain_deal_risk", {"deal_id": KESTREL})
    await run("lookup_deal", {"name_query": "kestrel"})
    job = await run("start_deep_triage", {"deal_id": KESTREL})
    await run("get_triage_result", {"deal_id": KESTREL})
    proposal = await run(
        "propose_action",
        {
            "deal_id": KESTREL,
            "action_type": "create_task",
            "assignee_name": "Maya",
            "title": "Book CFO call with Kestrel",
            "due_date": soon,
        },
    )
    await run("extend_preview", {"confirmation_token": proposal["confirmation_token"]})
    await run("confirm_action", {"confirmation_token": proposal["confirmation_token"]})
    await run("undo_action", {"confirmation_token": proposal["confirmation_token"]})
    rule = await run("set_standing_order", {"min_amount_usd": 75000, "quiet_days": 9})
    await run("list_standing_orders", {})
    await run("remove_standing_order", {"rule_id": rule["rule_id"]})
    await run(
        "log_deal_update", {"deal_id": HARBOR, "note": "Security review booked.", "next_step_date": soon}
    )
    await run("update_briefing_preferences", {"briefing_limit": 3})

    agent = Mcp(mcp.client, token_factory(sub="svc:triage", scope=AGENT_SCOPE, job=job["job_id"]))
    agent_tools = {t["name"]: t for t in await agent.tools()}
    for name, args in (
        ("explain_deal_risk", {"deal_id": KESTREL}),
        ("get_deal_timeline", {"deal_id": KESTREL, "days": 40}),
        ("get_deal_history", {"deal_id": KESTREL}),
        ("get_account_context", {"account_id": explained["deal"]["account_id"]}),
        ("get_prior_triage", {"deal_id": KESTREL}),
        ("get_playbook", {}),
    ):
        await run(name, args, caller=agent, schema=agent_tools[name])

    assert seen == set(tools) | set(agent_tools), "every tool must be exercised"
