"""Fill the listing in the addon.json that `alexa-ai new mcp` scaffolds.

    uv run python scripts/addon_assets/fill_addon.py [--addon addon-package/addon.json] [--check]

Sets the en-US store listing (name, descriptions, example phrases, privacy and terms URLs, icons,
carousel image) and the MCP endpoint, and keeps every other key the CLI wrote (account linking,
for one). The pages and images are served by the auth server (auth_server/legal.py). `--check`
fetches each URL first. Limits are Amazon's (MCP toolkit quickstart).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

AUTH_URL = "https://b4sp3enfx6.execute-api.us-east-1.amazonaws.com"
MCP_URL = "https://k04yht6ng0.execute-api.us-east-1.amazonaws.com/mcp"
LOCALE = "en-US"
ICON_SIZES = (72, 64, 88, 126, 180, 241)

SHORT = "Hear which deals in your pipeline are at risk and why, then act by voice. Nothing changes without your yes."
FULL = "\n\n".join(
    (
        (
            "Watchfloor watches a B2B sales pipeline and tells you, by voice, which deals are slipping and why."
        ),
        (
            'Ask "What\'s at risk in my pipeline?" for a short briefing that leads with the deals that need you '
            "most. Each deal has a health score from 0 to 100, and every reason behind it is spelled out: how "
            "long since the customer last engaged, whether buying signals have dried up, which qualification "
            "steps are missing, and whether the close date has been pushed or the amount cut."
        ),
        (
            'Ask why a deal is at risk to hear its reasons and its two-week trend. Say "dig into it" for a deep '
            "review, in which a team of AI agents reads the deal's history and proposes a plan."
        ),
        (
            "Watchfloor can also act for you: create a follow-up task, draft an email, reassign a deal's owner, "
            "post an alert to your team's Slack channel, log a note or a new close date, or set a standing order "
            'such as "flag deals over 50K that go quiet for five days". Nothing changes until you say yes, and '
            "you can undo for 30 seconds."
        ),
        (
            "This is a demonstration built for the Amazon Alexa+ and AWS hackathon. Link it with the demo account"
            " given in the project's testing instructions; every company and deal in it is fictional."
        ),
    )
)
PHRASES = [
    "What's at risk in my pipeline?",
    "Why is Kestrel Biotech at risk?",
    "Dig into Harbor and Pine",
    "Flag deals over 50K that go quiet for five days",
]
CAROUSEL_ALT = (
    'Watchfloor answering "What\'s at risk in my pipeline?" with two at-risk deals, '
    "Kestrel Biotech at health 29 and Harbor & Pine Logistics at health 34, each with its reasons."
)


def listing(auth_url: str) -> dict[str, Any]:
    assets = f"{auth_url}/static/addon"
    return {
        "name": {"value": "Watchfloor"},
        "shortDescription": SHORT,
        "fullDescription": FULL,
        "examplePhrases": PHRASES,
        "privacyAndCompliance": {
            "privacyPolicyUrl": f"{auth_url}/privacy",
            "termsOfUseUrl": f"{auth_url}/terms",
        },
        "mediaAssets": {
            "icons": {
                "light": [{"size": f"{n}x{n}", "uri": f"{assets}/icon-{n}x{n}.png"} for n in ICON_SIZES],
                "dark": [],
            },
            "carouselImages": [
                {"uri": f"{assets}/carousel-600x900.png", "altText": CAROUSEL_ALT, "size": "600x900"}
            ],
            "bannerImages": [],
        },
    }


def problems(entry: dict[str, Any]) -> list[str]:
    found = []
    if len(entry["name"]["value"]) > 30:
        found.append("name over 30 characters")
    if len(entry["shortDescription"]) > 123:
        found.append(f"shortDescription is {len(entry['shortDescription'])} characters, over 123")
    if len(entry["fullDescription"]) > 4000:
        found.append("fullDescription over 4000 characters")
    if not 3 <= len(entry["examplePhrases"]) <= 4 or any(len(p) > 200 for p in entry["examplePhrases"]):
        found.append("examplePhrases must be 3 to 4 phrases of at most 200 characters")
    if any(len(c["altText"]) > 250 for c in entry["mediaAssets"]["carouselImages"]):
        found.append("carousel altText over 250 characters")
    return found


def urls(entry: dict[str, Any]) -> list[str]:
    media = entry["mediaAssets"]
    return [
        *entry["privacyAndCompliance"].values(),
        *(i["uri"] for i in media["icons"]["light"]),
        *(c["uri"] for c in media["carouselImages"]),
    ]


def unreachable(targets: list[str]) -> list[str]:
    bad = []
    for url in targets:
        try:
            with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310 - our own https URLs
                if r.status != 200:
                    bad.append(f"{url}: HTTP {r.status}")
        except OSError as exc:
            bad.append(f"{url}: {exc}")
    return bad


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--addon", type=Path, default=Path("addon-package/addon.json"))
    parser.add_argument("--auth-url", default=AUTH_URL)
    parser.add_argument("--mcp-url", default=MCP_URL)
    parser.add_argument("--check", action="store_true", help="fetch every page and image URL first")
    opts = parser.parse_args()

    entry = listing(opts.auth_url.rstrip("/"))
    errors = problems(entry) + (unreachable(urls(entry)) if opts.check else [])
    if errors:
        sys.exit("\n".join(errors))

    doc = json.loads(opts.addon.read_text(encoding="utf-8"))
    doc.setdefault("manifestVersion", "1.0")
    store = doc.setdefault("storeListing", {})
    store["distributionCountries"] = ["US"]
    store.setdefault("locales", {}).setdefault(LOCALE, {}).update(entry)
    mcp = [i for i in doc.setdefault("integrations", []) if i.get("type") == "MCP"]
    if not mcp:
        mcp = [{"type": "MCP"}]
        doc["integrations"].append(mcp[0])
    mcp[0].setdefault("config", {}).setdefault("endpoints", {})["default"] = {
        "type": "HTTPS",
        "uri": opts.mcp_url,
    }
    opts.addon.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Filled {opts.addon}. Run `alexa-ai deploy` next.")


if __name__ == "__main__":
    main()
