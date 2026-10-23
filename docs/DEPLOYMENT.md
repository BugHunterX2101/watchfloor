# Deploying and operating Watchfloor

This is the runbook that used to sit in the README: demo operations, the AWS deploy, and the checks
against a running stack. The README keeps the summary; this file keeps the detail. Rollbacks,
redrives and incident playbooks are in [TECHNICAL.md](TECHNICAL.md) §17.

## Demo operations

| Command | Effect |
|---|---|
| `make demo-reset` | Re-seed relative to now and assert the scenario scores (`ENV=dev\|demo`: through the seeder job) |
| `make demo-clock OFFSET=+14h` | Jump the workspace clock to "next morning", then run the watcher (needs `WF_DEMO_MODE=true`) |
| `make demo-clock OFFSET=0` | Back to real time |
| `make demo-watch-now` | One watcher run: fires standing orders now |
| `make demo-status` | Bands, scenario scores, board, pending proposals, jobs, outbox backlog |
| `uv run python scripts/demo.py outbox-retry --id N` | Re-arm a dead outbox row (`--all-dead` for all of them) |

Each takes `ENV=dev|demo` (for the script, `--env`) to act on a deployed environment: the seeder
job resets, sets the clock, reports status and re-arms outbox rows from inside the VPC, and the
watcher job is the watcher run. With `compute: ecs` (the shipped configs, ADR-025) each is a one-off
Fargate task that `scripts/run_task.py` starts and waits for, about a minute longer than a Lambda
invoke; your AWS credentials need what the deploy role grants for it (`ecs:RunTask` and its
companions in `infra/bootstrap/github-deploy-role.yaml`). With `compute: lambda` they invoke
`wf-<env>-seeder` and `wf-<env>-watcher`, which needs `lambda:InvokeFunction` on both.
Offsets take effect only where `demo_mode: true` (`demo`, not `dev`); `demo-clock` warns otherwise.

## Deploy to AWS

Eight CDK stacks per environment (`dev`, `demo`) in `us-east-1`: net, data, auth, mcp, agents, async,
harness, ops. Pushes to `main` run the checks; `dev` deploys when `deploy-dev` is run by hand, and `v*.*.*` tags deploy `demo` (`.github/workflows`).

**On an AWS Free plan account** (new accounts since July 2025 start on it), the models are Amazon
Nova, which runs on demand in us-east-1 and is paid from the credits (ADR-022). Nova Lite runs the
harness, the follow-up drafts and the fast specialists. Nova Pro runs the triage Lead. The `demo`
config ships with `models_enabled: true` and `agents_enabled: false`, so everything deploys except the
Agents stack, and `start_deep_triage` answers that deep reviews aren't available. Set
`agents_enabled: true` to deploy AgentCore Runtime as well; on its first run with agents on,
`deploy-dev` creates the image repository, pushes the agents image, then creates the runtime.
`models_enabled: false` switches every model off: drafts use a template and the harness its
offline engine. That is how `dev` runs today: the account's Bedrock inference quotas (on-demand,
cross-Region and global) and its AgentCore quotas are all 0 and not adjustable in Service Quotas,
so a model call can only be throttled. Once AWS Support raises them, set `models_enabled: true`.
Meanwhile `dev` runs Gemini through Strands (ADR-023, `gemini_enabled`): the harness and the drafts try
Bedrock while models are on, then Gemini, then the keyword router or the template. Gemini's key is the
plain-string secret `wf-<env>/models/gemini`, created by hand. `agents_host: lambda` runs the triage
agents as a function in the async stack instead of on AgentCore, on the same chain: a Lead whose
model fails hands the review to the offline engine. That is how `dev` offers deep reviews today.

**Where it runs** (`compute`, ADR-025). Since 2026-10-16 the account refuses every Lambda call, so
the shipped configs set `compute: ecs`: the same packaged bundles run as arm64 Fargate containers
behind the same HTTP APIs (a VPC link and Cloud Map, no load balancer), one service each for the
auth server, the MCP server, the harness, the triage agents and the workers (dispatcher, watcher,
sweeper and canary in one process). The migrator and the seeder are one-off tasks. `compute: lambda`
is the original layout, unchanged, for when AWS lifts the restriction.

**Without a domain** (the shipped configs: `certificate_arn: null`), nothing needs buying. The MCP
and auth servers answer on their API Gateway hosts (`<id>.execute-api.us-east-1.amazonaws.com`) and
the harness on its CloudFront host (`<id>.cloudfront.net`), or, with `cdn: false` as in `dev` today
(ADR-015), on its own API Gateway host. Those hosts exist only once the stacks
do, and never change after that, so `make cdk-deploy` (run by `make deploy` and both deploy
workflows) writes them into the config as `mcp_domain`, `auth_domain` and `harness_domain`: before
deploying, from stacks that already exist, and after a first deploy, which it then repeats so every
service uses them. Commit them when the workflow's notice shows them. With a domain, set the three
host names and `certificate_arn` instead (and `hosted_zone_*` to have CDK create the DNS records).

What `dev` costs, before credits: about $25–30 a month (Postgres `t4g.micro` and its storage, the
NAT instance and its public IPv4 address, about ten secrets, a KMS key, alarms and logs), plus about
$40 for the five Fargate services on `compute: ecs` (0.25 vCPU each at the arm64 rates, and the
private DNS zone). On `compute: lambda` `dev` ships without provisioned concurrency, which would add
about $9, and `demo` keeps it for its latency gate. Deploy one environment at a time on a
credits-only account. The budget in `dev.yaml` e-mails at $15, $25 and $35 of monthly usage, before
credits.

### Before the first deploy

1. Fill in `infra/config/<env>.yaml`: account, the alert e-mail, the Bedrock model ids (⚠ Verify
   S6), and either nothing for the host names (see above) or three of your own with an ACM certificate
   in `us-east-1` that covers them.
2. `npx aws-cdk@2 bootstrap aws://<account>/us-east-1` once per account.
3. The GitHub OIDC deploy role, once per environment (`CreateOidcProvider=false` for the second):
   ```bash
   aws cloudformation deploy --template-file infra/bootstrap/github-deploy-role.yaml \
     --stack-name wf-dev-github-deploy --capabilities CAPABILITY_NAMED_IAM \
     --parameter-overrides Env=dev CreateOidcProvider=true
   ```
   Its `RoleArn` output is the repository secret `AWS_DEPLOY_ROLE_DEV` (`_DEMO` for demo); add
   `AWS_ACCOUNT_ID` as a repository variable, and GitHub environments named `dev` and `demo` (the
   role trusts only jobs running in them; give `demo` required reviewers).

### First deploy of an environment

The agent runtime needs its image to exist first.

```bash
make deploy ENV=dev AGENT_RUNTIME=false   # everything except the agent runtime
make agents-image ENV=dev && make agents-push ENV=dev REPO=<RepositoryUri output>
make deploy ENV=dev              # now with the runtime
make migrate-remote ENV=dev      # migrations, then logins for wf_app / wf_auth from their secrets
make seed-remote ENV=dev         # demo workspace, OAuth clients and logins (the seeder job)
```

`seed-remote` is the AWS twin of `make seed` + `make auth-bootstrap`. Every secret it registers is
generated by the Auth stack in Secrets Manager, and none is printed:

| Secret | Holds | Used by |
|---|---|---|
| `wf-<env>/auth/demo-login` | `password` for every seeded user | You, signing in to the harness |
| `wf-<env>/auth/alexa-client` | `client_secret` of `wf-alexa` | The Alexa+ account-linking setup |
| `wf-<env>/auth/dispatcher-client` | `client_secret` of `wf-dispatcher` | The dispatcher, for job tokens |
| `wf-<env>/canary/login` | `email` (`canary_email` in the config) and its own `password` | The canary |

Set `alexa_redirect_uris` in the config once the skill exists, then `make seed-remote ENV=<env>
SEED_EVENT='{"seed": false}'` registers them without touching the data. Pushes to `main` re-seed
`dev`; a `demo` release only re-registers clients and logins, and `make demo-reset ENV=demo`
resets the demo data before a recording.

Two secrets are filled in by hand: `wf-<env>/slack/webhook` (the webhook URL) and, where
`gemini_enabled` is on, `wf-<env>/models/gemini` (a Google AI Studio API key as a plain string;
nothing else creates it, so create it in Secrets Manager). With custom
domains but no hosted zone in the config, point each host name at its stack's `DnsTarget` output.

`make synth ENV=dev` checks the stacks without building anything; `make test-infra` synthesizes both
environments and asserts what the specs pin down (TLS-only Postgres, ES256 KMS key, alias with
provisioned concurrency, queue timeouts, alarms, agent role without database access).

## Against a running stack

These sign in as the canary user, as a real client would (`WF_CANARY_EMAIL` / `WF_CANARY_PASSWORD`
from `.env` locally; the secret `wf-<env>/canary/login` for `ENV=dev|demo`, so the caller needs
`secretsmanager:GetSecretValue` on it). `ENV=local` needs `make dev` running.

```bash
make conformance-remote ENV=dev   # CF-01..15 over HTTPS (CF-04 needs an agent token: unit suite only)
make latency ENV=dev              # p95 ≤ 150 ms, p99 ≤ 300 ms per read tool, paced under 60 calls/min
make evals ENV=dev                # 40 driver + 10 injection deep reviews, DD §22.5 thresholds
uv run locust -f tests/load/locustfile.py --headless --host https://<mcp domain>   # DD §21.3, 7 min
```

`make evals` drives real jobs: each one posts its plan to the environment's Slack channel. Locally
it exercises the offline engine, so it checks the pipeline, not the model. Rebuild the golden set
with `--write-golden` after a seed or RIGS change. The load profile's 20 rps is above the default
rate limits, so raise `rate_limit_user` / `rate_limit_ws` in `infra/config/<env>.yaml` for the
load window. `tests/evals/utterances.csv` is the 30-utterance sheet (DD §22.6), filled in by hand.
On Path B it runs in the harness: type each utterance and record the first tool from the drawer. Tagged releases run conformance, evals and the latency probe
against `demo`, and upload the eval results.
