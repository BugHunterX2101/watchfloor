"""Host and Origin validation at the MCP edge (2025-11-25, Streamable HTTP "Security Warning").

The spec says a server MUST validate Origin on all incoming connections (403 when present and invalid).
Host is the second half of the DNS-rebinding defence. These tests drive the guard with raw header lists,
so cases a browser never produces (repeated headers, odd ports) are covered, and then the real app for the
GET/DELETE behaviour a stateless server owes the spec (405, since it offers no event stream).
"""

from __future__ import annotations

import httpx
import pytest
from mcp_server.middleware.edge_guard import EdgeGuard, host_name
from starlette.types import Receive, Scope, Send

from tests.conftest import TokenFactory
from tests.mcp_client import MCP_HEADERS

LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
HOSTS = ["mcp.example", "::1"]
ORIGINS = ["https://alexa.amazon.com"]


async def _ok(scope: Scope, receive: Receive, send: Send) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def _status(
    headers: list[tuple[str, str]],
    *,
    method: str = "GET",
    path: str = "/healthz",
    origins: list[str] | None = None,
) -> int:
    """One request with exactly these headers, through the guard alone.

    Driven as raw ASGI so a header can repeat or be malformed, which an HTTP client would not allow.
    """
    guard = EdgeGuard(_ok, hosts=HOSTS, origins=ORIGINS if origins is None else origins)
    scope: Scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
    }
    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    await guard(scope, receive, send)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


# ---------------------------------------------------------------- Host


@pytest.mark.parametrize(
    "host",
    ["mcp.example", "MCP.Example", "mcp.example:443", "mcp.example:8080", "[::1]", "[::1]:8080"],
)
async def test_allowed_hosts_pass_whatever_the_case_or_port(host: str) -> None:
    assert await _status([("host", host)]) == 200


@pytest.mark.parametrize(
    "host",
    [
        "evil.example",
        "mcp.example.evil.example",
        "evil.example:mcp.example",
        "mcp.example.",  # a trailing dot is a different name to the allowlist, so it fails closed
        "mcp.example:80@evil.example",  # userinfo smuggled in front of the real host
        "mcp.example:abc",
        "mcp.example:99999999",
        "mcp.example, evil.example",
        "mcp.example evil.example",
        "",
    ],
)
async def test_other_hosts_are_421(host: str) -> None:
    assert await _status([("host", host)]) == 421


async def test_a_missing_or_repeated_host_header_is_421() -> None:
    assert await _status([]) == 421
    assert await _status([("host", "mcp.example"), ("host", "evil.example")]) == 421


@pytest.mark.parametrize("raw", ["mcp.example:80@x", "mcp.example:", "a b", "mcp.example:abc", ""])
def test_a_malformed_host_has_no_name(raw: str) -> None:
    assert host_name(raw) is None


@pytest.mark.parametrize(
    ("raw", "name"), [("MCP.example:8080", "mcp.example"), ("[::1]:8080", "::1"), ("localhost", "localhost")]
)
def test_a_well_formed_host_has_its_lower_case_name(raw: str, name: str) -> None:
    assert host_name(raw) == name


# ---------------------------------------------------------------- Origin


async def test_no_origin_is_fine_because_native_clients_send_none() -> None:
    assert await _status([("host", "mcp.example")]) == 200


async def test_an_allowlisted_origin_passes_exactly() -> None:
    assert await _status([("host", "mcp.example"), ("origin", "https://alexa.amazon.com")]) == 200


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "null",  # sandboxed iframes and file:// pages
        "",
        "https://alexa.amazon.com.evil.example",
        "https://evil.example/https://alexa.amazon.com",
        "http://alexa.amazon.com",  # a different scheme is a different origin
        "https://alexa.amazon.com:444",
        "*",
    ],
)
async def test_every_other_origin_is_403(origin: str) -> None:
    assert await _status([("host", "mcp.example"), ("origin", origin)]) == 403


async def test_an_empty_allowlist_refuses_every_browser_origin() -> None:
    headers = [("host", "mcp.example"), ("origin", "https://alexa.amazon.com")]
    assert await _status(headers, origins=[]) == 403


async def test_a_wildcard_in_the_allowlist_is_a_literal_not_a_pattern() -> None:
    headers = [("host", "mcp.example"), ("origin", "https://evil.example")]
    assert await _status(headers, origins=["*", "https://*.example"]) == 403


async def test_a_second_origin_header_cannot_hide_behind_an_allowed_first_one() -> None:
    headers = [
        ("host", "mcp.example"),
        ("origin", "https://alexa.amazon.com"),
        ("origin", "https://evil.example"),
    ]
    assert await _status(headers) == 403


@pytest.mark.parametrize("origin", ["HTTPS://ALEXA.AMAZON.COM", "https://Alexa.Amazon.com"])
async def test_origin_case_is_not_significant(origin: str) -> None:
    """Origins are serialised in lower case by browsers; case games must neither lock out nor let in."""
    assert await _status([("host", "mcp.example"), ("origin", origin)]) == 200


@pytest.mark.parametrize(
    "entry", ["https://alexa.amazon.com/", " https://alexa.amazon.com ", "HTTPS://Alexa.Amazon.com"]
)
async def test_a_sloppy_allowlist_entry_still_means_the_same_origin(entry: str) -> None:
    """A trailing slash in WF_ALLOWED_ORIGINS must not silently lock the one real client out."""
    headers = [("host", "mcp.example"), ("origin", "https://alexa.amazon.com")]
    assert await _status(headers, origins=[entry]) == 200


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE", "OPTIONS", "PUT"])
@pytest.mark.parametrize("path", ["/mcp", "/.well-known/oauth-protected-resource", "/healthz"])
async def test_the_origin_rule_covers_every_method_and_path(method: str, path: str) -> None:
    """The spec says all incoming connections, not just POST /mcp."""
    headers = [("host", "mcp.example"), ("origin", "https://evil.example")]
    assert await _status(headers, method=method, path=path) == 403


async def test_a_bad_host_wins_over_a_bad_origin_and_a_bad_origin_over_a_missing_accept() -> None:
    both = [("host", "evil.example"), ("origin", "https://evil.example")]
    assert await _status(both, method="POST", path="/mcp") == 421
    bad_origin = [("host", "mcp.example"), ("origin", "https://evil.example")]
    assert await _status(bad_origin, method="POST", path="/mcp") == 403


# ---------------------------------------------------------------- what a stateless server owes the spec


async def test_get_and_delete_on_the_endpoint_are_405_not_a_hanging_stream(
    client: httpx.AsyncClient, token_factory: TokenFactory
) -> None:
    """2025-11-25: GET must return text/event-stream or 405. This server is stateless and sends plain JSON,
    so it has no stream to offer: a client that asks must be told so at once, not held open."""
    auth = {"Authorization": f"Bearer {token_factory()}"}
    get = await client.get("/mcp", headers={"Accept": "text/event-stream", **auth})
    assert get.status_code == 405
    delete = await client.delete("/mcp", headers={**MCP_HEADERS, **auth})
    assert delete.status_code == 405


async def test_a_post_is_answered_with_one_json_object_never_a_stream(
    client: httpx.AsyncClient, token_factory: TokenFactory
) -> None:
    """The spec lets a server answer a request with application/json instead of SSE. Doing so keeps the
    answer whole through any buffering proxy and means no session or stream state to lose between instances."""
    r = await client.post(
        "/mcp", json=LIST, headers={**MCP_HEADERS, "Authorization": f"Bearer {token_factory()}"}
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert "mcp-session-id" not in {k.lower() for k in r.headers}
    assert r.json()["result"]["tools"]
