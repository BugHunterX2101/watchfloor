"""EnvConfig rules for host names: custom domains, or the hosts AWS assigns."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from wf_infra.config import CONFIG_DIR, PENDING_HOST, EnvConfig

FREE = Path(__file__).resolve().parent / "config" / "free" / "dev.yaml"


def _free(**changes: object) -> EnvConfig:
    data = yaml.safe_load(FREE.read_text(encoding="utf-8"))
    return EnvConfig.model_validate({**data, **changes})


def test_hosts_read_as_pending_until_aws_assigns_them() -> None:
    cfg = _free()
    assert not cfg.custom_domains
    assert cfg.issuer == f"https://{PENDING_HOST}"
    assert cfg.resource_uri == f"https://{PENDING_HOST}/mcp"
    assert cfg.harness_url == f"https://{PENDING_HOST}"


def test_assigned_hosts_are_used_as_they_are() -> None:
    cfg = _free(
        mcp_domain="abc123defg.execute-api.us-east-1.amazonaws.com",
        auth_domain="hij456klmn.execute-api.us-east-1.amazonaws.com",
        harness_domain="d1a2b3c4d5e6f7.cloudfront.net",
    )
    assert cfg.resource_uri == "https://abc123defg.execute-api.us-east-1.amazonaws.com/mcp"
    assert cfg.issuer == "https://hij456klmn.execute-api.us-east-1.amazonaws.com"
    assert cfg.harness_url == "https://d1a2b3c4d5e6f7.cloudfront.net"


def test_a_certificate_needs_all_three_host_names() -> None:
    with pytest.raises(ValidationError, match="all three host names"):
        _free(
            certificate_arn="arn:aws:acm:us-east-1:123456789012:certificate/x", mcp_domain="mcp.example.com"
        )


def test_a_hosted_zone_needs_custom_domains() -> None:
    with pytest.raises(ValidationError, match="hosted zone"):
        _free(hosted_zone_id="Z123", hosted_zone_name="example.com")


def test_the_fast_tier_gemini_model_is_passed_only_when_set() -> None:
    from wf_infra.common import base_env

    one = base_env(_free(gemini_enabled=True), "harness")
    assert one["WF_GEMINI_MODEL_ID"] == "gemini-flash-lite-latest" and "WF_GEMINI_HAIKU_MODEL_ID" not in one
    two = base_env(_free(gemini_enabled=True, gemini_haiku_model="gemini-fast"), "harness")
    assert two["WF_GEMINI_HAIKU_MODEL_ID"] == "gemini-fast"
    off = base_env(_free(gemini_enabled=False, gemini_haiku_model="gemini-fast"), "harness")
    assert "WF_GEMINI_HAIKU_MODEL_ID" not in off


@pytest.mark.parametrize("env", ["dev", "demo"])
def test_the_shipped_configs_are_valid(env: str) -> None:
    data = yaml.safe_load((CONFIG_DIR / f"{env}.yaml").read_text(encoding="utf-8"))
    EnvConfig.model_validate({**data, "account": "123456789012"})
