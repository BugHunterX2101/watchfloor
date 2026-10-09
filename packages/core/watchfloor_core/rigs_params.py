"""Loads ``config/rigs.yaml`` into typed, immutable parameters."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(__file__).parent / "config" / "rigs.yaml"


@dataclass(frozen=True)
class StageParams:
    name: str
    norm_days: int
    intent_baseline: float


@dataclass(frozen=True)
class Weights:
    r: float = 0.30
    i: float = 0.25
    g: float = 0.25
    s: float = 0.20


@dataclass(frozen=True)
class BandThresholds:
    at_risk_enter: int = 65
    at_risk_exit_below: int = 60
    watch_enter: int = 40
    watch_exit_below: int = 36


@dataclass(frozen=True)
class EngagementPoints:
    email_inbound: float = 1.0
    meeting: float = 2.0
    call_inbound: float = 1.5
    doc_view: float = 0.2
    doc_view_cap: int = 10


@dataclass(frozen=True)
class SlippageParams:
    push: float = 0.35
    stage_regression: float = 0.40
    amount_cut: float = 0.25
    amount_cut_threshold: float = 0.15
    magnitude_kicker: float = 0.10
    magnitude_days: int = 30
    window_days: int = 90


@dataclass(frozen=True)
class GapParams:
    per_gap: float = 0.25
    overdue_grace_days: int = 2


@dataclass(frozen=True)
class RigsParams:
    model_version: str
    weights: Weights
    bands: BandThresholds
    points: EngagementPoints
    engagement_window_days: int
    stages: dict[str, StageParams]
    slippage: SlippageParams
    gaps: GapParams

    def stage(self, name: str) -> StageParams:
        try:
            return self.stages[name]
        except KeyError:
            raise ValueError(f"no RIGS parameters for stage {name!r} (closed deals are not scored)") from None


def parse_params(doc: dict[str, Any]) -> RigsParams:
    w = doc["weights"]
    b = doc["bands"]
    total = w["r"] + w["i"] + w["g"] + w["s"]
    if abs(total - 1.0) > 1e-9:
        raise ValueError(f"RIGS weights must sum to 1.0, got {total}")
    return RigsParams(
        model_version=doc["model_version"],
        weights=Weights(**w),
        bands=BandThresholds(
            at_risk_enter=b["at_risk"]["enter"],
            at_risk_exit_below=b["at_risk"]["exit_below"],
            watch_enter=b["watch"]["enter"],
            watch_exit_below=b["watch"]["exit_below"],
        ),
        points=EngagementPoints(**doc["engagement_points"]),
        engagement_window_days=doc.get("engagement_window_days", 14),
        stages={name: StageParams(name=name, **vals) for name, vals in doc["stages"].items()},
        slippage=SlippageParams(**doc["slippage"]),
        gaps=GapParams(**doc["gaps"]),
    )


@lru_cache(maxsize=1)
def load_params(path: str | None = None) -> RigsParams:
    with open(path or CONFIG_PATH, encoding="utf-8") as fh:
        return parse_params(yaml.safe_load(fh))
