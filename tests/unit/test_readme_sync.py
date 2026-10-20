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


def test_the_test_counts_agree_with_each_other() -> None:
    """The badge, the suite rows and the total say one number (they once read 816, 886 and 887)."""
    badge = re.search(r"badge/tests-(\d+)%20passing", README)
    assert badge, "the README has no tests badge"
    start = README.index("## Testing and quality")
    rows = re.findall(r"^\| (?!\*\*)[^|]+\| (\d+) \|", README[start:], re.M)
    total = re.search(r"^\| \*\*Total\*\* \| \*\*(\d+)\*\* \|", README[start:], re.M)
    assert total and len(rows) == 5, "the suite table changed shape: update this test"
    assert sum(map(int, rows)) == int(total.group(1)) == int(badge.group(1)), (
        rows,
        total.group(1),
        badge.group(1),
    )


def test_the_tool_counts_match_the_pinned_snapshot() -> None:
    """The README says how many tools there are; the snapshot is what ships."""
    import json

    snap = json.loads((ROOT / "services/mcp_server/schemas/tools.snapshot.json").read_text(encoding="utf-8"))
    user, agent = len(snap["user"]), len(snap["agent"])
    assert f"{user} user tools · {agent} agent tools" in README
    assert f"{user} user tools, {agent} agent tools" in README
    distinct = len({t["name"] for t in snap["user"] + snap["agent"]})
    assert f"{distinct} distinct tools" in README
