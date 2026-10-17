"""The security workflow, `make audit` and Dependabot say the same thing (docs/SECURITY.md).

Nothing here runs a scanner. It keeps three hand-written files from drifting apart: the one accepted
advisory exception, the projects that are audited, and the rule that a scan never runs on a push to main
(a push deploys).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/security.yml").read_text(encoding="utf-8"))
DEPENDABOT = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text(encoding="utf-8"))
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")

UV_PROJECTS = {".", "services/agents", "services/harness/api", "infra"}
NODE_PROJECTS = {"services/harness/web", "services/mcp_server/ui"}


def _on() -> dict:
    # YAML 1.1 reads the key `on` as the boolean True.
    return WORKFLOW.get("on", WORKFLOW.get(True))


def _audit_recipe() -> str:
    return MAKEFILE.split("\naudit:", 1)[1].split("\n\n", 1)[0]


def _workflow_ignores() -> dict[str, set[str]]:
    rows = WORKFLOW["jobs"]["python-advisories"]["strategy"]["matrix"]["include"]
    return {r["project"]: set(re.findall(r"--ignore-vuln (\S+)", r["ignore"])) for r in rows}


def _make_ignores() -> dict[str, set[str]]:
    audit = _audit_recipe()
    out: dict[str, set[str]] = {p: set() for p in UV_PROJECTS}
    for projects, flags in re.findall(r'case "\$\$d" in ([^)]+)\) ign="([^"]+)"', audit):
        for project in projects.split("|"):
            out[project.strip()] = set(re.findall(r"--ignore-vuln (\S+)", flags))
    return out


def test_never_runs_on_a_push() -> None:
    # A push to main triggers deploy-dev.yml; a scan has no business next to it.
    assert "push" not in _on()


def test_workflow_is_read_only() -> None:
    assert WORKFLOW["permissions"] == {"contents": "read"}


def test_workflow_and_make_audit_the_same_python_projects() -> None:
    assert set(_workflow_ignores()) == UV_PROJECTS
    for project in UV_PROJECTS:
        assert project in _audit_recipe()


def test_workflow_and_make_accept_the_same_advisories() -> None:
    assert _workflow_ignores() == _make_ignores()


def test_every_accepted_advisory_is_written_up() -> None:
    accepted = set().union(*_workflow_ignores().values())
    assert accepted, "the exception list is empty: delete this test and the note in SECURITY.md"
    text = (ROOT / "docs/SECURITY.md").read_text(encoding="utf-8")
    for advisory in accepted:
        assert advisory in text, f"{advisory} is ignored in CI but not explained in docs/SECURITY.md"


def test_node_projects_match() -> None:
    matrix = WORKFLOW["jobs"]["node-advisories"]["strategy"]["matrix"]["project"]
    assert set(matrix) == NODE_PROJECTS
    for project in NODE_PROJECTS:
        assert project in _audit_recipe()


def test_dependabot_covers_every_lock_file() -> None:
    covered = {(u["package-ecosystem"], u["directory"]) for u in DEPENDABOT["updates"]}
    for project in UV_PROJECTS:
        directory = "/" if project == "." else f"/{project}"
        assert ("uv", directory) in covered, directory
        assert (ROOT / project / "uv.lock").is_file()
    for project in NODE_PROJECTS:
        assert ("npm", f"/{project}") in covered
        assert (ROOT / project / "package-lock.json").is_file()
    assert ("github-actions", "/") in covered


def test_dependabot_directories_exist() -> None:
    for update in DEPENDABOT["updates"]:
        assert (ROOT / update["directory"].lstrip("/")).is_dir(), update["directory"]
