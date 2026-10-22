"""The version gate's initialize counter-offer (ADR-034), on the bare EdgeGuard → ProtocolVersion pair."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import orjson
from mcp_server.middleware.edge_guard import EdgeGuard
from mcp_server.middleware.version import ProtocolVersion


def _initialize(version: str | None) -> dict[str, Any]:
    params: dict[str, Any] = {"capabilities": {}, "clientInfo": {"name": "kiro", "version": "x"}}
    if version is not None:
        params["protocolVersion"] = version
    return {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params}


async def _through(allowed: Iterable[str], payload: Any) -> tuple[int, bytes, bytes, bytes]:
    """Status, the body the app downstream read, its content-length header, and the body sent."""
    seen: dict[str, bytes] = {}

    async def app(scope: Any, receive: Any, send: Any) -> None:
        seen["body"] = (await receive())["body"]
        seen["length"] = dict(scope["headers"])[b"content-length"]
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    guard = EdgeGuard(ProtocolVersion(app, allowed=allowed), hosts=["mcp.example"], origins=[])
    body = orjson.dumps(payload)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": [
            (b"host", b"mcp.example"),
            (b"accept", b"application/json, text/event-stream"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
    }
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await guard(scope, receive, send)
    return sent[0]["status"], seen.get("body", b""), seen.get("length", b""), body


async def test_an_unallowed_offer_is_rewritten_to_the_newest_allowed_handshake_version() -> None:
    status, downstream, length, _ = await _through({"2025-06-18", "2025-11-25"}, _initialize("2024-11-05"))
    assert status == 200
    assert orjson.loads(downstream)["params"]["protocolVersion"] == "2025-11-25"
    assert length == str(len(downstream)).encode()


async def test_an_operator_who_allows_only_2025_11_25_still_gets_kiro_a_usable_version() -> None:
    _, downstream, _, _ = await _through({"2025-11-25"}, _initialize("2025-06-18"))
    msg = orjson.loads(downstream)
    assert msg["params"]["protocolVersion"] == "2025-11-25"
    assert msg["params"]["clientInfo"] == {"name": "kiro", "version": "x"}  # nothing else changes


async def test_an_allowed_offer_passes_through_byte_for_byte() -> None:
    _, downstream, length, sent = await _through({"2025-06-18", "2025-11-25"}, _initialize("2025-06-18"))
    assert downstream == sent
    assert length == str(len(sent)).encode()


async def test_no_rewrite_without_a_reachable_handshake_version_or_an_offer() -> None:
    # 2026-07-28 has no initialize handshake, so there is nothing to counter-offer.
    _, downstream, _, sent = await _through({"2026-07-28"}, _initialize("2025-06-18"))
    assert downstream == sent
    _, downstream, _, sent = await _through({"2025-11-25"}, _initialize(None))
    assert downstream == sent


def test_the_counter_offer_follows_protocol_order_not_set_order() -> None:
    gate = ProtocolVersion(None, allowed=["2025-11-25", "2024-11-05", "2025-06-18", "2026-07-28"])  # type: ignore[arg-type]
    assert gate.counter_offer == "2025-11-25"
