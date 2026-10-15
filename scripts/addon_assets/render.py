"""Render the Alexa+ listing images from listing.html with headless Chrome.

    uv run --with pillow python scripts/addon_assets/render.py [--chrome PATH]

Writes the six icon sizes and the 600x900 carousel image that addon.json's mediaAssets need to
services/auth_server/auth_server/static/addon/, which the auth server serves at /static/addon/.
Needs `npm ci` in services/harness/web (for the fonts) and Chrome or Edge. Headless Chrome will not
open a window as small as an icon, so the icon is drawn once at 964 px and scaled down with Pillow.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve().parent / "listing.html"
OUT = ROOT / "services/auth_server/auth_server/static/addon"
ICON_SIZES = (64, 72, 88, 126, 180, 241)
ICON_MASTER = 964  # 4 x 241
CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "chromium",
)


def find_chrome(given: str | None) -> str:
    for candidate in (given, *CANDIDATES):
        if candidate and (Path(candidate).exists() or shutil.which(candidate)):
            return candidate
    raise SystemExit("Chrome or Edge not found; pass --chrome PATH")


def shoot(chrome: str, query: str, width: int, height: int, out: Path) -> None:
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run(  # noqa: S603 - a local browser with arguments this script builds
            [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--force-device-scale-factor=1",
                "--allow-file-access-from-files",
                f"--user-data-dir={profile}",
                f"--window-size={width},{height}",
                "--virtual-time-budget=3000",  # web fonts load before the shot
                f"--screenshot={out}",
                SOURCE.as_uri() + query,
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
    if not out.exists():
        raise SystemExit(f"Chrome wrote no {out.name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chrome")
    chrome = find_chrome(parser.parse_args().chrome)
    OUT.mkdir(parents=True, exist_ok=True)
    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        master = Path(tmp) / "icon.png"
        shoot(chrome, "?icon", ICON_MASTER, ICON_MASTER, master)
        with Image.open(master) as big:
            big = big.convert("RGB")
            for size in ICON_SIZES:
                big.resize((size, size), Image.Resampling.LANCZOS).save(
                    OUT / f"icon-{size}x{size}.png", optimize=True
                )
    shoot(chrome, "", 600, 900, OUT / "carousel-600x900.png")
    print("\n".join(sorted(p.name for p in OUT.glob("*.png"))))


if __name__ == "__main__":
    main()
