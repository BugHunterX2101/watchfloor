"""CDK app (TD §11.1): one environment per synth, ``cdk synth -c env=dev|demo``.

Order differs from TD §11.1 in one place: Agents comes before Async, because the
dispatcher needs the runtime ARN, and the Agents stack needs nothing from Async.
Synth without built Lambda zips: ``-c wf:stubAssets=true`` (CI's synth check).
"""

from __future__ import annotations

import os
from pathlib import Path

import aws_cdk as cdk
from wf_infra.config import CONFIG_DIR, load_config
from wf_infra.stacks.agents import AgentsStack
from wf_infra.stacks.async_ import AsyncStack
from wf_infra.stacks.auth import AuthStack
from wf_infra.stacks.data import DataStack
from wf_infra.stacks.harness import HarnessStack
from wf_infra.stacks.mcp import McpStack
from wf_infra.stacks.net import NetStack
from wf_infra.stacks.ops import OpsStack


def build(app: cdk.App) -> dict[str, cdk.Stack]:
    env_name = app.node.try_get_context("env") or "dev"
    config_dir = app.node.try_get_context("wf:configDir")  # the synth tests' own configs
    cfg = load_config(
        env_name,
        account=app.node.try_get_context("account") or os.environ.get("CDK_DEFAULT_ACCOUNT"),
        directory=Path(config_dir) if config_dir else CONFIG_DIR,
    )
    override = app.node.try_get_context("wf:modelsEnabled")  # one synth, either way
    if override is not None:
        cfg = cfg.model_copy(update={"models_enabled": str(override).lower() in ("true", "1")})
    aws_env = cdk.Environment(account=cfg.account, region=cfg.region)
    p = cfg.prefix

    net = NetStack(app, f"{p}-net", cfg=cfg, env=aws_env)
    data = DataStack(app, f"{p}-data", cfg=cfg, net=net, env=aws_env)
    auth = AuthStack(app, f"{p}-auth", cfg=cfg, net=net, data=data, env=aws_env)
    mcp = McpStack(app, f"{p}-mcp", cfg=cfg, net=net, data=data, env=aws_env)
    # Without models (a Free plan account) there is no AgentCore to deploy.
    agents = AgentsStack(app, f"{p}-agents", cfg=cfg, env=aws_env) if cfg.models_enabled else None
    asyncs = AsyncStack(app, f"{p}-async", cfg=cfg, net=net, data=data, auth=auth, agents=agents, env=aws_env)
    harness = HarnessStack(app, f"{p}-harness", cfg=cfg, env=aws_env)
    ops = OpsStack(app, f"{p}-ops", cfg=cfg, auth=auth, mcp=mcp, asyncs=asyncs, env=aws_env)
    # Mcp reaches Auth (JWKS) and the Async queues through fixed URLs, so it has no stack dependency on
    # them. A send before the queues exist only logs; the sweeper republishes the outbox row (ADR-008).
    cdk.Tags.of(app).add("project", "watchfloor")
    cdk.Tags.of(app).add("env", cfg.env)
    return {s.stack_name: s for s in (net, data, auth, mcp, agents, asyncs, harness, ops) if s is not None}


if __name__ == "__main__":
    application = cdk.App()
    build(application)
    application.synth()
