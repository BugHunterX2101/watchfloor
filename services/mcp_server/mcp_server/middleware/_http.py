"""Small ASGI helpers shared by the edge middleware."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import orjson
from starlette.types import Receive, Scope, Send

Header = tuple[bytes, bytes]


def state(scope: Scope) -> dict[str, Any]:
    return scope.setdefault("state", {})


def is_mcp_post(scope: Scope) -> bool:
    return scope["type"] == "http" and scope["path"] == "/mcp" and scope["method"] == "POST"


async def respond(
    send: Send,
    status: int,
    *,
    body: bytes = b"",
    content_type: str | None = None,
    headers: Sequence[Header] = (),
) -> None:
    hdrs: list[Header] = [(b"content-length", str(len(body)).encode())]
    if content_type:
        hdrs.append((b"content-type", content_type.encode()))
    hdrs.extend(headers)
    await send({"type": "http.response.start", "status": status, "headers": hdrs})
    await send({"type": "http.response.body", "body": body})


async def respond_jsonrpc_error(
    send: Send, status: int, code: int, message: str, *, headers: Sequence[Header] = ()
) -> None:
    """HTTP rejection with an optional JSON-RPC error body (no id), as 2025-11-25 allows."""
    body = orjson.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": code, "message": message}})
    await respond(send, status, body=body, content_type="application/json", headers=headers)


async def read_body(receive: Receive, limit: int) -> bytes | None:
    """Buffer the request body; None as soon as it exceeds ``limit`` bytes."""
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def replay(body: bytes, receive: Receive) -> Receive:
    """A receive callable that yields the buffered body once, then defers to the original."""
    sent = False

    async def _receive() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return _receive
