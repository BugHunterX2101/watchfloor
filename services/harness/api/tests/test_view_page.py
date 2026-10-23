"""A ui:// view is served as a page of its own, under a policy that lets it run and nothing else."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from conftest import BOARD_HTML, FakeAuth, FakeMcp, make_config, sign_in
from harness_api import app as app_module
from harness_api.headers import CSP

URI = "ui://watchfloor/triage-board"


async def test_the_view_is_served_with_a_policy_that_boxes_it_in(
    client: httpx.AsyncClient, mcp: FakeMcp
) -> None:
    await sign_in(client)
    r = await client.get("/api/view", params={"uri": URI})  # nothing cached yet: fetched for this person
    assert r.status_code == 200 and r.text == BOARD_HTML
    csp = {
        d.split(" ", 1)[0]: d.split(" ", 1)[1] if " " in d else ""
        for d in r.headers["content-security-policy"].split("; ")
    }
    assert csp["script-src"] == "'unsafe-inline'" and csp["style-src"] == "'unsafe-inline'"
    assert (
        csp["connect-src"] == "'none'" and csp["form-action"] == "'none'" and csp["default-src"] == "'none'"
    )
    assert csp["sandbox"] == "allow-scripts"  # an opaque origin, even if someone opens this URL directly
    assert csp["frame-ancestors"] == "'self'" and r.headers["x-frame-options"] == "SAMEORIGIN"


async def test_the_apps_own_policy_still_allows_no_inline_script_and_frames_only_itself(
    client: httpx.AsyncClient,
) -> None:
    r = await client.get("/healthz")
    assert r.headers["content-security-policy"] == CSP
    assert "script-src 'self'" in CSP and "unsafe-inline" not in CSP.replace(
        "style-src-attr 'unsafe-inline'", ""
    )
    assert "frame-src 'self'" in CSP


def _frameable(r: httpx.Response) -> None:
    """A refusal the frame can show: a line of HTML under the view's policy, not JSON the app forbids framing."""
    assert r.headers["content-type"].startswith("text/html")
    assert "frame-ancestors 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "SAMEORIGIN"


async def test_only_a_signed_in_person_gets_a_view(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/view", params={"uri": URI})
    assert r.status_code == 401 and "Sign in to continue." in r.text
    _frameable(r)


async def test_a_busy_view_says_so_in_the_frame(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, auth: FakeAuth, mcp: FakeMcp
) -> None:
    monkeypatch.setattr(app_module, "TOOL_PER_MIN", 1)
    app = app_module.create_app(
        make_config(tmp_path),
        auth_transport=httpx.MockTransport(auth),
        mcp_transport=httpx.MockTransport(mcp),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://harness.test") as c:
        await sign_in(c)
        await c.post("/api/tool", json={"name": "list_standing_orders", "arguments": {}})
        r = await c.get("/api/view", params={"uri": URI})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1 and "Try again in" in r.text
    _frameable(r)


@pytest.mark.parametrize(
    "uri",
    [
        "",
        "https://evil.example/x",
        "ui://other/board",
        "ui://watchfloor/",
        "ui://watchfloor/../x",
        "ui://watchfloor/Board",
    ],
)
async def test_only_watchfloor_views_can_be_asked_for(client: httpx.AsyncClient, uri: str) -> None:
    await sign_in(client)
    r = await client.get("/api/view", params={"uri": uri})
    assert r.status_code == 400
    _frameable(r)


async def test_an_expired_access_token_is_refreshed_not_signed_out(
    client: httpx.AsyncClient, mcp: FakeMcp
) -> None:
    # Watch reopened an hour later: the view is no longer cached and the access token has lapsed. The
    # refresh token is still good, so the page loads and the session lives on.
    await sign_in(client)
    mcp.valid.discard("at1")
    r = await client.get("/api/view", params={"uri": URI})
    assert r.status_code == 200 and r.text == BOARD_HTML
    assert "wf_h=" in r.headers["set-cookie"] and mcp.headers[-1]["authorization"] == "Bearer at2"
    assert r.headers["content-security-policy"].startswith("default-src 'none'")
