# ADR-025: The services on ECS Fargate while Lambda is refused

- **Status:** Accepted (2026-10-17)
- **Refines:** DESIGN.md §18.1 (compute), TECHNICAL.md §10–§11 (packaging, stacks). ADR-015 and
  ADR-016 still apply to the Lambda path.

## Context

On 2026-10-16 every Lambda API call on the account started to fail with `AccessDeniedException` and
no message: `ListFunctions`, `GetAccountSettings` and `GetFunctionConfiguration` in every Region,
for a caller with `AdministratorAccess` in an account outside any AWS Organization. Other services
answer as before, and the Free plan is active with credits left. The first deploy after it failed on
the migrator's update and then on its rollback, which left `wf-dev-data` in `UPDATE_ROLLBACK_FAILED`;
the three HTTP APIs answered 500 because API Gateway could no longer invoke their functions.
Nothing in our templates or roles explains it, and CloudTrail recorded no Lambda call. It reads as an
account-level restriction that only AWS Support can lift, on its own schedule, days before the
submission deadline (friction log entry 11).

ECS, EC2, ECR, Cloud Map and API Gateway still work on the account. App Runner does not (it needs a
subscription the account doesn't have).

## Decision

`compute: lambda | ecs` in the environment config; `dev` and `demo` set `ecs`.

- **Same code, same images.** `scripts/package_lambdas.py` writes a `Dockerfile` into every bundle:
  the packaged directory on `python:3.12-slim-bookworm`, arm64. CDK builds and pushes it on deploy.
  `Container` (infra `common.py`) is the twin of `PythonLambda`: the same base environment, a log
  group, grants to the task role.
- **Same URLs.** The three HTTP APIs stay; each route's integration becomes an HTTP proxy through a
  VPC link to the service's Cloud Map SRV records (`HttpServiceDiscoveryIntegration`). No load
  balancer: it would cost more than the services. The API sets `x-wf-host` to its own domain on
  every request, since through a VPC link `Host` names the integration; the MCP server's host check
  reads that header there (`WF_HOST_HEADER`). Custom header mapping is the documented case; a client's
  own value is overwritten.
- **Services**, one task each, 0.25 vCPU: `auth-server`, `mcp-server` (1 GB), `harness-api` (serves
  its packaged page, as the Lambda does without a CDN), `triage` (1 GB, the agents' AgentCore app,
  reached by the workers at `triage.<prefix>.internal`) and `workers`.
- **`workers.service`** does what four functions did: one SQS consumer per queue, handing each
  message to `dispatcher.handle_batch` as the event source mapping would and deleting it only on
  success (failures and crashes are redelivered, then dead-lettered, as before); the sweeper every
  minute, the watcher every 15 and the canary every 5, each with its function's timeout. It runs as
  a single task replaced stop-then-start, so two never schedule at once.
- **One-off jobs** (migrator, seeder, a watcher run) are task definitions that
  `scripts/run_task.py` starts and waits for; `make migrate-remote`, `make seed-remote` and
  `scripts/demo.py` use it on either compute.
- **Getting off Lambda without touching it.** Every Lambda resource leaves the templates. A removed
  resource is deleted in the stack's cleanup phase, after the update has succeeded; a failed delete
  is retried, then skipped, and the stack still completes. The functions stay behind, unmanaged,
  until the restriction lifts. The queues are new ones (`…-svc`), so the event source mappings
  left behind point at deleted queues and can never take a message the workers wait for. CDK custom
  resources run on Lambda too, so `ecs` requires `cdn: false` (no `BucketDeployment`).
- **Secrets** reach the containers through ECS's own secret injection, not a dynamic reference
  resolved into the task definition.
- **Metrics.** Container stdout is not read as embedded metric format, so the workers send theirs
  through `PutMetricData` (`WF_METRICS_API`, namespace-scoped grant) and `ToolLatencyMs{tool}` comes
  from a metric filter on the MCP server's JSON tool-call line. Alarms and the dashboard are
  unchanged, less the Lambda-only spillover alarm.

## Consequences

- About $39 a month for the five services at the us-east-1 Fargate arm64 rates ($0.03238 per
  vCPU-hour, $0.00356 per GB-hour), plus about $1 for the private DNS zone and Cloud Map. That is
  more than the functions cost, and within the credits for the judging window.
- No cold starts. A deploy replaces a task in about a minute; the HTTP services start the new task
  before stopping the old one.
- The deploy role needs `ecs:RunTask` and its companions for the one-off jobs
  (`infra/bootstrap/github-deploy-role.yaml`); the role's stack is updated once by hand.
- Back to Lambda, once AWS lifts the restriction: `compute: lambda` (and `cdn` as before). The
  Lambda path is unchanged and still tested (`-c wf:compute=lambda`). The orphaned functions should
  be deleted by hand first, since their names are fixed.
