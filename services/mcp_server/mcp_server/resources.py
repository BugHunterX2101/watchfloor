"""MCP Apps UI resources (DD §6.6): three single-file HTML views served as ``ui://`` resources.

The HTML is built from ``services/mcp_server/ui`` into ``ui_dist/``. Until the
bundle is built, a small placeholder page is served so hosts never get a 404.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import mcp_types as types

MIME_TYPE = "text/html;profile=mcp-app"
UI_DIST = Path(__file__).resolve().parent / "ui_dist"

TRIAGE_BOARD = "ui://watchfloor/triage-board"
DEAL_DETAIL = "ui://watchfloor/deal-detail"
CONFIRM_SHEET = "ui://watchfloor/confirm-sheet"


@dataclass(frozen=True)
class UiResource:
    uri: str
    file: str
    name: str
    description: str


UI_RESOURCES: tuple[UiResource, ...] = (
    UiResource(TRIAGE_BOARD, "board.html", "Triage board", "Ranked list of the deals that need attention."),
    UiResource(
        DEAL_DETAIL, "detail.html", "Deal detail", "RIGS breakdown, evidence and the deep-review plan."
    ),
    UiResource(CONFIRM_SHEET, "confirm.html", "Confirm sheet", "Action preview with Approve and Not now."),
)
_BY_URI = {r.uri: r for r in UI_RESOURCES}

_PLACEHOLDER = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{name}</title></head>
<body style="font-family:system-ui,sans-serif;margin:16px">
<p><strong>{name}</strong></p><p>The Watchfloor UI bundle has not been built yet.</p>
</body></html>
"""


def descriptors() -> list[types.Resource]:
    return [
        types.Resource.model_validate(
            {"uri": r.uri, "name": r.name, "description": r.description, "mimeType": MIME_TYPE}
        )
        for r in UI_RESOURCES
    ]


@lru_cache(maxsize=8)
def load_ui(uri: str) -> str | None:
    res = _BY_URI.get(uri)
    if res is None:
        return None
    path = UI_DIST / res.file
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return _PLACEHOLDER.format(name=res.name)
