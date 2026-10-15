"""Privacy policy, terms of use and listing images: the HTTPS URLs an Alexa+ add-on listing
links to (addon.json). The images are rendered by scripts/addon_assets/render.py."""

from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import Response

from .context import TEMPLATES

_CACHE = {"cache-control": "public, max-age=3600"}
ADDON_ASSETS = Path(__file__).resolve().parent / "static" / "addon"


async def privacy(request: Request) -> Response:
    return TEMPLATES.TemplateResponse(request, "privacy.html", headers=_CACHE)


async def terms(request: Request) -> Response:
    return TEMPLATES.TemplateResponse(request, "terms.html", headers=_CACHE)
