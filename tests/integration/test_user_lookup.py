"""Teammate lookup treats a spoken name as words, never as a LIKE pattern."""

from __future__ import annotations

import pytest
from watchfloor_core import db
from watchfloor_core.repos import users

from tests.conftest import WS


@pytest.mark.parametrize("spoken", ["%", "_", "!", "%%", "P_iya"])
async def test_wildcard_characters_do_not_match_every_teammate(seeded: str, spoken: str) -> None:
    async with db.ws_tx(WS) as conn:
        everyone = await users.all_names(conn)
        found = await users.find_by_name(conn, spoken)
    assert len(everyone) > 1
    assert len(found) < len(everyone)


async def test_a_first_name_still_finds_the_teammate(seeded: str) -> None:
    async with db.ws_tx(WS) as conn:
        first = (await users.all_names(conn))[0]["display_name"].split()[0]
        found = await users.find_by_name(conn, first)
    assert found and all(first.lower() in r["display_name"].lower() for r in found)
