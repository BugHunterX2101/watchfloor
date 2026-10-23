"""The screens draw the band lines where rigs.yaml puts them: health = 100 - risk (ADR-020), so a
band that risk enters at 65 begins at health 35. The TypeScript copies are read, not imported."""

from __future__ import annotations

import re
from pathlib import Path

from watchfloor_core.rigs_params import load_params

ROOT = Path(__file__).resolve().parents[2]


def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_web_band_edges_match_rigs_yaml() -> None:
    bands = load_params().bands
    m = re.search(
        r"BAND_EDGES = \{ at_risk: (\d+), watch: (\d+) \}", _source("services/harness/web/src/logic.ts")
    )
    assert m, "BAND_EDGES not found in logic.ts"
    assert (int(m[1]), int(m[2])) == (100 - bands.at_risk_enter, 100 - bands.watch_enter)


def test_view_band_edges_match_rigs_yaml() -> None:
    bands = load_params().bands
    src = _source("services/mcp_server/ui/src/format.ts")
    at_risk = re.search(r"AT_RISK_HEALTH = (\d+);", src)
    watch = re.search(r"WATCH_HEALTH = (\d+);", src)
    assert at_risk and watch, "band constants not found in format.ts"
    assert (int(at_risk[1]), int(watch[1])) == (100 - bands.at_risk_enter, 100 - bands.watch_enter)
