"""Nothing secret-shaped is committed, and the files that must stay on one machine stay untracked.

Looks only at what git tracks, so a local ``.env`` is not read. The patterns are the token formats the
providers publish; a placeholder such as ``<token>`` or ``xxxx`` does not match any of them. Each
detector is also run on a string built at test time, so a pattern that stops matching fails loudly
instead of silently passing the whole tree.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAX_BYTES = 2_000_000

DETECTORS: dict[str, re.Pattern[str]] = {
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "AWS access key id": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "Slack webhook URL": re.compile(
        r"hooks\.slack\.com/services/T[A-Z0-9]{6,}/B[A-Z0-9]{6,}/[A-Za-z0-9]{20,}"
    ),
    "Slack token": re.compile(r"\bxox[abprs]-[0-9A-Za-z-]{10,}"),
    "private key block": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
}

# Built at run time so this file does not itself contain a token-shaped string.
SAMPLES: dict[str, str] = {
    "GitHub token": "gh" + "p_" + "a1B2" * 9,
    "Google API key": "AI" + "za" + "Sy" + "A" * 33,
    "AWS access key id": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
    "Slack webhook URL": "hooks.slack.com/services/" + "T0ABCDEF1/B0ABCDEF1/" + "a1B2" * 6,
    "Slack token": "xo" + "xb-" + "1234567890-abcdefghij",
    "private key block": "-----BEGIN " + "EC PRIVATE KEY-----",
}

NEVER_TRACKED = (".env", "infra/cdk.context.json")
NEVER_UNDER = (".secrets/",)
NEVER_SUFFIX = (".pem", ".p12", ".pfx", ".jks")
MUST_BE_IGNORED = (".env", ".secrets/dev_es256.pem", "infra/cdk.context.json")


def _tracked() -> list[str]:
    try:
        done = subprocess.run(  # noqa: S603
            ["git", "ls-files", "-z"],  # noqa: S607
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [p for p in done.stdout.decode("utf-8").split("\0") if p]


def test_each_detector_matches_a_sample_of_its_own_kind() -> None:
    assert set(SAMPLES) == set(DETECTORS)
    for kind, sample in SAMPLES.items():
        assert DETECTORS[kind].search(sample), f"the {kind} detector no longer matches a real-shaped value"


def test_placeholders_are_not_mistaken_for_secrets() -> None:
    for text in ("ghp_<token>", "AIza...", "hooks.slack.com/services/T000/B000/XXXX", "xoxb-<token>"):
        assert not any(d.search(text) for d in DETECTORS.values()), text


def test_no_tracked_file_holds_a_secret_shaped_string() -> None:
    found = []
    for rel in _tracked():
        if rel == "tests/security/test_no_secrets_committed.py":
            continue
        path = ROOT / rel
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        for kind, pattern in DETECTORS.items():
            for m in pattern.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                found.append(f"{rel}:{line} looks like a {kind}")  # never the value itself
    assert not found, "secret-shaped strings are committed:\n" + "\n".join(found)


def test_files_that_must_stay_local_are_not_tracked() -> None:
    tracked = _tracked()
    bad = [
        p
        for p in tracked
        if p in NEVER_TRACKED or p.startswith(NEVER_UNDER) or p.lower().endswith(NEVER_SUFFIX)
    ]
    assert not bad, f"tracked, but must stay local: {bad}"


@pytest.mark.parametrize("path", MUST_BE_IGNORED)
def test_the_ignore_file_covers_what_must_stay_local(path: str) -> None:
    try:
        done = subprocess.run(  # noqa: S603
            ["git", "check-ignore", "-q", path],  # noqa: S607
            cwd=ROOT,
            check=False,
        )
    except OSError:
        pytest.skip("git is not available")
    assert done.returncode == 0, f"{path} is not gitignored"
