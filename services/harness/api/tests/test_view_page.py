"""A ui:// view is served as a page of its own, under a policy that lets it run and nothing else."""

from __future__ import annotations

import httpx
import pytest
from conftest import BOARD_HTML, FakeMcp, sign_in
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


async def test_only_a_signed_in_person_gets_a_view(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/view", params={"uri": URI})
    assert r.status_code == 401


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
    assert (await client.get("/api/view", params={"uri": uri})).status_code == 400
