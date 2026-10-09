"""Write the hosts AWS assigned into infra/config/<env>.yaml, for an environment without custom domains.

Without a ``certificate_arn``, the MCP and auth servers answer on their HTTP APIs' execute-api
hosts and the harness on its CloudFront host. Each stays the same for the life of its stack, so,
like custom domains, they live in the config as fixed strings (infra/wf_infra/config.py). This
reads them from the stacks' ``DefaultHost`` outputs and writes them as ``mcp_domain``,
``auth_domain`` and ``harness_domain``. ``make cdk-deploy`` runs it before deploying, and again
after a first deploy, which is the one that creates them.

    uv run python scripts/default_hosts.py --env dev            # write them (AWS_PROFILE=watchfloor)
    uv run python scripts/default_hosts.py --env dev --check    # exit 1 if the config would change
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEYS = {"mcp_domain": "mcp", "auth_domain": "auth", "harness_domain": "harness"}  # config key → stack


def config_path(env: str) -> Path:
    return ROOT / "infra" / "config" / f"{env}.yaml"


def scalar(text: str, key: str) -> str | None:
    match = re.search(rf"^{key}:[ \t]*([^\s#]+)", text, re.MULTILINE)
    value = match.group(1).strip("'\"") if match else None
    return None if value in ("null", "~") else value


def assigned_hosts(env: str, region: str) -> dict[str, str]:
    """Each deployed stack's DefaultHost output, by config key; a stack not deployed yet is left out."""
    import boto3
    from botocore.exceptions import ClientError

    cfn = boto3.client("cloudformation", region_name=region)
    hosts: dict[str, str] = {}
    for key, stack in KEYS.items():
        try:
            outputs = cfn.describe_stacks(StackName=f"wf-{env}-{stack}")["Stacks"][0].get("Outputs", [])
        except ClientError as exc:
            if "does not exist" in str(exc):
                continue
            raise
        host = next((o["OutputValue"] for o in outputs if o["OutputKey"] == "DefaultHost"), None)
        if host:
            hosts[key] = host
    return hosts


def rewrite(text: str, hosts: dict[str, str]) -> str:
    """Replace each key's value, keeping the line's comment."""
    for key, host in hosts.items():
        text, n = re.subn(
            rf"^({key}:[ \t]*)[^\s#]+", lambda m, h=host: m.group(1) + h, text, count=1, flags=re.MULTILINE
        )
        if n != 1:
            raise SystemExit(f"no {key} line in the config")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--env", choices=["dev", "demo"], required=True)
    parser.add_argument("--check", action="store_true", help="exit 1 if the config would change")
    args = parser.parse_args(argv)

    path = config_path(args.env)
    text = path.read_bytes().decode("utf-8")  # bytes: keep the file's line endings
    if scalar(text, "certificate_arn"):
        print(f"{args.env}: custom domains (certificate_arn), nothing to do")
        return 0
    hosts = assigned_hosts(args.env, scalar(text, "region") or "us-east-1")
    if not hosts:
        print(f"wf-{args.env} is not deployed yet: its first deploy assigns the hosts")
        return 0
    new = rewrite(text, hosts)
    if new == text:
        print(f"{args.env}: the config already has the hosts AWS assigned")
        return 0
    if args.check:
        print(f"{args.env}: the config does not have the hosts AWS assigned yet")
        return 1
    path.write_bytes(new.encode("utf-8"))
    for key, host in hosts.items():
        print(f"{key}: {host}")
    print(
        f"Written to {path.relative_to(ROOT).as_posix()}. Deploy again so every Lambda uses them, and commit it."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
