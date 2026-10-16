"""TD copies of repository files stay true: Appendix A, the ci.yml in §12.1 and the §5 tree."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TD = (ROOT / "docs" / "TECHNICAL.md").read_text(encoding="utf-8")


def _block_after(heading: str) -> str:
    """The first fenced block after the heading that starts with ``heading``."""
    m = re.compile(r"```[\w-]*\n(.*?)\n```", re.S).search(TD, TD.index(heading))
    assert m, heading
    return m.group(1)


@pytest.mark.parametrize(
    ("heading", "path"),
    [
        ("### A.1 `Makefile`", "Makefile"),
        ("### A.2 `docker-compose.yml`", "docker-compose.yml"),
        ("### A.3 `pyproject.toml`", "pyproject.toml"),
        ("### A.4 `.env.example`", ".env.example"),
        ("### A.5 `Procfile.dev`", "Procfile.dev"),
        ("### 12.1 `ci.yml`", ".github/workflows/ci.yml"),
    ],
)
def test_appendix_copies_match_the_files(heading: str, path: str) -> None:
    real = (ROOT / path).read_text(encoding="utf-8").rstrip("\n")
    assert _block_after(heading) == real, f"TECHNICAL.md's copy of {path} drifted: paste the file in again"


def _tree_paths() -> list[str]:
    """Every path named in the §5 tree, leaving out generated (gitignored) entries."""
    paths: list[str] = []
    stack: list[tuple[int, str]] = []
    for line in _block_after("## 5. Repository file structure").splitlines()[1:]:  # [0] is "watchfloor/"
        m = re.match(r"^([│ ]*)(?:├──|└──) (.+?)(?:\s+#\s*(.*))?$", line)
        if not m:
            continue
        depth, names, comment = len(m.group(1)) // 4, m.group(2).split(" · "), m.group(3) or ""
        stack = [s for s in stack if s[0] < depth] + [(depth, names[0].rstrip("/"))]
        if "(gen)" in comment and "committed" not in comment:
            continue
        parent = "/".join(s[1] for s in stack[:-1])
        paths += [f"{parent}/{name.rstrip('/')}".lstrip("/") for name in names]
    return paths


def test_every_path_in_the_repository_tree_exists() -> None:
    paths = _tree_paths()
    assert len(paths) > 150, "the tree parser found too little: has the §5 layout changed?"
    missing = [p for p in paths if not (ROOT / p).exists()]
    assert not missing, f"TECHNICAL.md §5 names files that do not exist: {missing}"
