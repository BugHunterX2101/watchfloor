"""scripts/default_hosts.py: the hosts AWS assigned, written into an environment's config."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _load() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "default_hosts.py"
    spec = importlib.util.spec_from_file_location("wf_default_hosts", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hosts_cli = _load()

CONFIG = (
    "env: dev\r\n"
    "region: us-east-1\r\n"
    "mcp_domain: null\r\n"
    "auth_domain: null          # filled in after the first deploy\r\n"
    "harness_domain: null\r\n"
    "certificate_arn: null\r\n"
)
ASSIGNED = {
    "mcp_domain": "abc123defg.execute-api.us-east-1.amazonaws.com",
    "auth_domain": "hij456klmn.execute-api.us-east-1.amazonaws.com",
    "harness_domain": "d1a2b3c4d5e6f7.cloudfront.net",
}


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "dev.yaml"
    path.write_bytes(CONFIG.encode())
    monkeypatch.setattr(hosts_cli, "ROOT", tmp_path)
    monkeypatch.setattr(hosts_cli, "config_path", lambda env: path)
    return path


def test_writes_the_assigned_hosts_keeping_comments_and_line_endings(
    config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hosts_cli, "assigned_hosts", lambda env, region: ASSIGNED)
    assert hosts_cli.main(["--env", "dev", "--check"]) == 1
    assert config.read_bytes().decode() == CONFIG  # --check writes nothing
    assert hosts_cli.main(["--env", "dev"]) == 0
    text = config.read_bytes().decode()
    assert "mcp_domain: abc123defg.execute-api.us-east-1.amazonaws.com\r\n" in text
    assert "auth_domain: hij456klmn.execute-api.us-east-1.amazonaws.com          # filled in" in text
    assert "harness_domain: d1a2b3c4d5e6f7.cloudfront.net\r\n" in text
    assert hosts_cli.main(["--env", "dev", "--check"]) == 0  # up to date now


def test_nothing_to_do_before_the_first_deploy(config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hosts_cli, "assigned_hosts", lambda env, region: {})
    assert hosts_cli.main(["--env", "dev", "--check"]) == 0
    assert config.read_bytes().decode() == CONFIG


def test_custom_domains_are_left_alone(config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config.write_bytes(CONFIG.replace("certificate_arn: null", "certificate_arn: arn:aws:acm:x").encode())

    def unexpected(env: str, region: str) -> dict[str, str]:
        raise AssertionError("no AWS call with custom domains")

    monkeypatch.setattr(hosts_cli, "assigned_hosts", unexpected)
    assert hosts_cli.main(["--env", "dev"]) == 0


def test_scalar_reads_null_as_none() -> None:
    assert hosts_cli.scalar(CONFIG, "mcp_domain") is None
    assert hosts_cli.scalar(CONFIG, "region") == "us-east-1"
