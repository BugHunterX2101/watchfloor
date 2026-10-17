"""The README's repository map and links stay true, and its diagrams stay present."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text(encoding="utf-8")

DIAGRAMS = ("flowchart", "sequenceDiagram", "stateDiagram-v2", "erDiagram", "classDiagram")


def _map_block() -> str:
    start = README.index("## Repository map")
    m = re.compile(r"```text\n(.*?)\n```", re.S).search(README, start)
    assert m, "the README has no ```text block under 'Repository map'"
    return m.group(1)


def _tree_paths() -> list[str]:
    """Every path named in the repository map. Same layout as the tree in docs/TECHNICAL.md §5."""
    paths: list[str] = []
    stack: list[tuple[int, str]] = []
    for line in _map_block().splitlines()[1:]:  # [0] is "watchfloor/"
        m = re.match(r"^([│ ]*)(?:├──|└──) (.+?)(?:\s+#\s*(.*))?$", line)
        if not m:
            continue
        depth, names = len(m.group(1)) // 4, m.group(2).split(" · ")
        stack = [s for s in stack if s[0] < depth] + [(depth, names[0].rstrip("/"))]
        parent = "/".join(s[1] for s in stack[:-1])
        paths += [f"{parent}/{name.rstrip('/')}".lstrip("/") for name in names]
    return paths


def test_every_path_in_the_repository_map_exists() -> None:
    paths = _tree_paths()
    assert len(paths) > 120, "the map parser found too little: has the layout of the tree changed?"
    missing = [p for p in paths if not (ROOT / p).exists()]
    assert not missing, f"README's repository map names paths that do not exist: {missing}"


def test_every_relative_link_resolves() -> None:
    links = re.findall(r"\]\((?!https?:|mailto:|#)([^)\s]+)\)", README)
    assert len(links) > 40, "the link pattern found too little"
    broken = [link for link in links if not (ROOT / link.split("#")[0]).exists()]
    assert not broken, f"README links to files that do not exist: {broken}"


def test_every_in_page_anchor_has_a_heading() -> None:
    def slug(heading: str) -> str:
        text = re.sub(r"[`*_]|<[^>]+>", "", heading).strip().lower()
        text = re.sub(r"[^\w\s-]", "", text)
        return re.sub(r"\s", "-", text)

    headings = {slug(h) for h in re.findall(r"^#{1,6} (.+)$", README, re.M)}
    anchors = set(re.findall(r"\]\(#([^)\s]+)\)", README))
    missing = sorted(a for a in anchors if a not in headings)
    assert not missing, f"README links to in-page anchors with no heading: {missing}"


def test_the_architecture_diagrams_are_present_and_closed() -> None:
    blocks = re.findall(r"```mermaid\n(.*?)\n```", README, re.S)
    assert len(blocks) >= 21, f"expected the architecture diagrams, found {len(blocks)}"
    for i, block in enumerate(blocks, 1):
        assert block.lstrip().startswith(DIAGRAMS), f"diagram {i} starts with {block.splitlines()[0]!r}"
    assert README.count("```") % 2 == 0, "a code fence in the README is not closed"
