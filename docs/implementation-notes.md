# Implementation notes: where the code differs from the specs

DESIGN.md and TECHNICAL.md describe the intended system. This page lists the places where the
build deliberately differs from them, with the reason and the file that holds the detail.
Decisions big enough to need an ADR link to one. Items marked **⚠ Verify** wait on a spike.

## Agents (`services/agents`)

| # | Spec says | Built | Why / where |
|---|---|---|---|
| 1 | Every service is a workspace member (TD §5) | Standalone uv project with its own `uv.lock`, excluded from the workspace | `strands-agents` pins `mcp<2.2`; the server needs `mcp>=2.3`. [ADR-011](adr/0011-agents-standalone-project.md) |
| 2 | `agents/playbook.yaml` (TD §5) | No agents-side copy; read via the `get_playbook` MCP tool | One source of truth in `packages/core/.../config/playbook.yaml`. [ADR-012](adr/0012-lead-draft-and-output-gate.md) |
| 3 | Specialist and data tools take `deal_id` (DD §10.4) | No id arguments; tools are bound to the job | A model cannot pick which record to read. ADR-012 |
| 4 | Lead returns `TriageOutput` (DD §10.3) | Lead fills a lenient `LeadDraft`; `agents/gate.py` produces the `TriageOutput` | Evidence and playbook checks are deterministic. ADR-012 |
| 5 | (not in spec) | Offline engine in `local`/`test` when model ids are unset | Full loop without Bedrock. [ADR-013](adr/0013-offline-triage-engine.md) |
| 6 | No local port given | Local agent on **:8083**; the container still listens on :8080 | :8080 is MCP, :8081 auth and :8082 the harness OAuth callback (`WF_HARNESS_REDIRECT_URI`). `.env.example`, `Procfile.dev` |
| 7 | Memory strategies and namespaces per DD §11 (⚠ Verify S6) | Retrieval by prefix `/actors/{workspace_id}:{user_ref}/`, overridable with `WF_MEMORY_NAMESPACE_PATH` | **⚠ Verify (S6)** against the memory resource's strategy namespaces once infra creates it. `agents/memory.py` |
| 8 | Specialist budgets (DD §10.4) | Analyst 4 turns / 20 s / 1 call · Context 4 / 20 s / 1 · Strategist 3 / 25 s / 2 · Critic 1 / 15 s / 2 · Lead 8 turns · job 90 s | One critic-driven revision at most. `agents/specialists/__init__.py`, `agents/lead.py`, `agents/runner.py` |

| 9 | Critic rejects actions naming people not in `get_account_context` (DD §10.6 rule 3) | Also enforced by the gate: assignee and new-owner names must match the deal owner or a contact by first name | A planted "reassign to me" never becomes an action, even if the critic misses it. `agents/gate.py` |

### Agent event stream

`POST /invocations` streams Server-Sent Events. `workers.agentcore_client.final_output` reads them.

- `{"type": "progress", "step": "started" | "analyzing" | "checking", ...}` while working.
- Exactly one terminal event:
  - `{"type": "result", "engine": "strands" | "offline", "output": TriageOutput}`, or
  - `{"type": "error", "code": ..., "error": "..."}`, where `code` is one of `invalid_payload`,
    `misconfigured`, `timed_out`, `rejected`, `data_unavailable` or `agent_failed`.
- `timed_out` maps to the dispatcher's `AgentTimeout`. Every other error becomes `GiveUp`. A
  result event wins over an earlier error event in the same stream.

## Harness (`services/harness`)

| Spec says | Built | Why / where |
|---|---|---|
| Path A preferred: a real Alexa+ add-on; the harness is a dev tool (PRD §19, TD §4.17) | **Path B**: the harness is the entry's Alexa+ experience. The add-on side is built but not deployed (ADR-024 service token, listing pages and images, `fill_addon.py`, TD §14) | Amazon's toolkit role refused our account after the access window closed ([friction log entry 4](friction-log.md)); the rules allow a simulated Alexa+ experience |
| `harness/api` is a workspace member using FastAPI and the `mcp` SDK (TD §5) | Standalone uv project like the agents (ADR-011); Starlette like the other services; MCP over plain JSON-RPC (`mcp_http.py`) | Strands pins `mcp<2.2`. Plain JSON-RPC times every call exactly and needs no SDK session |
| Strands agent + `MCPClient` (DD §17) | Each tool from `tools/list` is wrapped as a Strands `PythonAgentTool` | Per-call latency for the drawer, and the `ui://` view of the last call |
| (not in spec) | Keyword router when `WF_HAIKU_MODEL_ID` is unset, local and test only; every turn is labelled `"engine": "offline"` | Exercise sign-in and the real tools without Bedrock, like ADR-013 |
| Session storage unspecified | Tokens in a Fernet-encrypted HttpOnly, SameSite=Lax cookie; `WF_HARNESS_SESSION_SECRET` required outside local | The harness runs on Lambda with no store |
| MCP Apps host via ext-apps utilities | ext-apps `AppBridge` (no MCP client) over `PostMessageTransport` into a `sandbox="allow-scripts"` iframe. View tool calls go to `POST /api/tool`, which allows only `get_risk_briefing`, `explain_deal_risk`, `get_triage_result`, `start_deep_triage`, `propose_action` and `confirm_action` | A tap on Approve is an explicit confirmation (DD §15.5); everything else, including standing orders, stays in the conversation |
| — | Local harness on :8082 (its OAuth callback), Vite dev server on :5173 proxying to it | `Procfile.dev`, `vite.config.ts` |

## MCP Apps views (`services/mcp_server/ui`)

| Spec says | Built | Why / where |
|---|---|---|
| Separate `Board.tsx`, `Detail.tsx`, `Confirm.tsx` bundles (TD §5) | One Preact app per bundle. The tool result's shape picks the first screen, and taps move board → detail → plan → confirm through server tools | A row tap or Propose needs the next screen in the same frame (DD §15.3–15.5) |
| CI greps `ui_dist` for `https?://` (TD §10) | `scripts/check.mjs` looks for loaders instead: `<script src>`, remote `<link>`/`<img>`, `url(http…)`, `@import` | The bundled libraries contain namespace strings such as `http://www.w3.org/2000/svg`, which load nothing. Each view is also checked at ≤ 150 KB gzip (now 70 KB) |
| `_meta.ui.resourceUri` ⚠ Verify (S3) | Kept. ext-apps 2.0.3 calls the nested form current and the flat `"ui/resourceUri"` key legacy | Answers the spec half of S3; Alexa+'s host support is still open |
| Build target "Node 20" | Vite 8 + TypeScript 7 + Preact 11, target ES2020 (no `Array.at`) | Echo webviews may be older than desktop Chrome |
| — | Views keep no viewport-relative heights | ext-apps `autoResize` measures the document; a `100vh` minimum fed the frame's height back into it |

## Infrastructure (`infra/`, `scripts/package_lambdas.py`)

| Spec says | Built | Why / where |
|---|---|---|
| `infra` is a workspace member (TD Appendix A.3) | Its own uv project (`infra/uv.lock`); `make infra-sync` | aws-cdk-lib is large and only infra work needs it |
| Stack order net → data → auth → mcp → async → agents (TD §11.1) | net → data → auth → mcp → **agents → async** → harness → ops | The dispatcher needs the runtime ARN; Agents needs nothing from Async |
| MCP reads queue URLs from Async | Queues have fixed names; `EnvConfig.queue_url` derives URLs and ARNs | Async would otherwise depend on Mcp and Mcp on Async |
| Domains on API Gateway (DD §18.1) | Fixed host names in `infra/config/<env>.yaml` with one ACM certificate; Route 53 aliases when a zone is configured, `DnsTarget` outputs otherwise | Issuer, audience and redirect URI must be known to several stacks at once |
| App and auth roles have DB credentials (DD §13.3) | `wf_app` and `wf_auth` get generated secrets attached to the instance; the migrator Lambda enables each login with its secret's password (`set_role_passwords`) | Migration 0004 creates the roles NOLOGIN on purpose |
| ⚠ Verify CDK support for AgentCore (DD §18.1) | aws-cdk-lib 2.273 has L1 `CfnRuntime` and `CfnMemory`. First deploy of an environment runs with `-c wf:agentRuntime=false`, then the image is pushed, then a full deploy | A runtime can't be created before its image exists |
| Memory namespaces (DD §11, ⚠ S6) | Strategies `facts` → `/actors/{actorId}/facts`, `preferences` → `/actors/{actorId}/preferences`; the runtime gets `WF_MEMORY_NAMESPACE_PATH=/actors/{actorId}/` | The agents' prefix search covers both, by construction |
| Harness: S3 + CloudFront, Lambda + HTTP API | CloudFront serves the page **and** proxies `/converse`, `/api/*`, `/oauth/*`, `/healthz` to the API | One origin keeps the session cookie first-party, as locally |
| Session secrets | Secrets Manager values passed as CloudFormation dynamic references in Lambda environment variables | The services read `WF_SESSION_SECRET` / `WF_HARNESS_SESSION_SECRET` from the environment |
| Wheels for `aarch64-manylinux2014` (TD §10.1) | `aarch64-manylinux_2_28` | asyncpg 0.32 publishes no manylinux2014 aarch64 wheel; the python3.12 runtime (AL2023, glibc 2.34) runs 2_28 |
| MCP zip < 25 MB unzipped (TD §10.1) | 47 MB (auth 42, harness 74, workers 26) before stripping; CI strips native extensions with `aarch64-linux-gnu-strip` | asyncpg + cryptography native code is ~26 MB unstripped; provisioned concurrency keeps the MCP Lambda warm |
| `wf-hot-p95` on all tools | One p95 alarm per hot tool (`get_risk_briefing`, `explain_deal_risk`) | The server emits `ToolLatencyMs` with a `tool` dimension only, and alarms can't aggregate across dimensions |
| Budgets $25/$60/$90 | One account-wide budget, created from `dev.yaml` only | A tag-filtered budget reads $0 until the tag is activated for cost allocation |
| CI per TD §12 | `ci.yml` (also called by both deploy workflows), Postgres service for the full suite, Node 24, no pyright yet. Evals, latency probe and Local Inspector are not wired yet | They depend on the evals stage |
| `scripts/invoke_seeder.py` → `make seed-remote` (TD Appendix A.1) | A seeder Lambda, `wf-<env>-seeder` (`auth_server.seeder`), invoked with the AWS CLI like the migrator. Event `{"seed", "bootstrap"}`, both on by default | It runs from the auth_server zip (Argon2 is there), which also carries `db/`. The result reports counts, never a secret |
| Dispatcher and canary secrets filled in by hand | The Auth stack generates `alexa-client`, `dispatcher-client`, `demo-login` and `canary/login` (`{"email": canary_email}` plus a generated `password`); Async and Ops use them | Nothing to paste after a deploy except the Slack webhook. The canary signs in as a seeded rep (`daniel@…` by default) with its own password, so rotating the demo password doesn't break it |
| `seed-remote` after every deploy (TD §12) | `dev` re-seeds on every push; a `demo` release runs `{"seed": false}` (clients and logins only) | A release must not reset demo data mid-recording; `make demo-reset ENV=demo` does that on purpose |

## Local addresses

`WF_MCP_URL` and `WF_JWKS_URL` default to `127.0.0.1`, not `localhost`. On Windows, `localhost`
resolves to `::1` first, where nothing listens, and each new connection waits about 2 s before
falling back. That was measured at a 2,194 ms median against 53 ms for the same `get_risk_briefing`.
`WF_ISSUER` and `WF_RESOURCE_URI` stay `localhost`: they are identifiers inside tokens, so the only
local cost is on sign-in and job-token minting.

## Evals, latency, conformance and load (`tests/evals`, `scripts/`, `tests/load`)

| Spec says | Built | Why |
|---|---|---|
| Golden triage set with labeled drivers (DD §22.5) | Built from the seed at the frozen demo instant (`--write-golden`): the 40 highest-scoring deals, labeled with the RIGS factors behind their top three evidenced reasons, plus the 10 hostile deals; keyed by name and amount | RIGS generated the data, so its reasons are the ground truth; seeded ids carry the seed time, names don't. Live labels that differ are reported as drift |
| 10 hostile deals (DD §22.5) vs "two hostile notes" in the seed description (DD §10.6) | `HOSTILE_DEALS = 10`; the first two keep their original random draws and the other eight use their own streams | Adding them changed no other seeded row; a deal the notes would lift out of Healthy is skipped |
| Groundedness ≥ 98% | Evidence ids are enforced at store time; whether each claim is *supported* is a manual spot-check of a fixed 10-claim sample in the results file | Needs a reader (or a judge model, not built) |
| Cost per job | Each job logs one `llm_usage` line (model calls, input and output tokens); the eval report points there | Token counts stay in the logs, not in the stored result; see "Update (2026-10-16)" below for the Logs Insights query |
| (not in spec) | Every eval job posts its plan to Slack and writes a memory record | An incoming webhook can't be redirected; documented in the runner and README |
| Latency probe at n = 200 | Calls are paced at 55/min, interleaved across tools; a 429 is waited out and not counted | The per-user limit is 60/min; a 200-call run per tool takes about 18 minutes |
| `conformance_remote.py` | CF-01..15 except CF-04 (needs an agent token, covered by the unit suite); the Host-header 421 check runs locally only | Behind API Gateway the Host header is the custom domain by construction |
| Local Inspector in `release-demo` (TD §12.3) | No CI step | It is interactive (`addon-local-inspector <url>` opens a browser UI) and installs from Amazon's private toolkit registry, which is closed to our account (friction log entry 4) |
| Utterance eval in the Alexa+ web simulator (DD §22.6) | Run by hand in the harness; `observed_tool` comes from the drawer | No simulator access (Path B) |
| Load profile 20 rps (DD §21.3) | `rate_limit_user` / `rate_limit_ws` in the env config (defaults 60 / 600 per minute), passed to the MCP function | 20 rps is twice the per-workspace limit (DD §18.1); raise them for the load window only |
| Offline engine drivers come from scored reasons | When no reason carries evidence (a healthy deal with only gaps), it cites the latest inbound conversation as an R driver, from its type and date only | `TriageOutput` needs at least one evidenced driver, so healthy deals failed review |
| `make dev` | Sets `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8` for honcho | A cp1252 console crashed honcho on the logs' `→` and `·` |

## Demo operations (`scripts/demo.py`)

| Spec says | Built | Why |
|---|---|---|
| `make demo-* ENV=dev\|demo` (TD Appendix A.1) | `demo.py --env dev\|demo` invokes Lambdas with boto3, in the region from `infra/config/<env>.yaml`: the seeder resets (`{"bootstrap": false}`) and runs `{"demo": "status"\|"clock"\|"outbox-retry"}` (`db.seed.ops`, shared with the local path); the watcher Lambda is `watch-now`. One attempt per invoke, no retries | The database is private to the VPC, and the seeder already has the owner secret and `db/`, so no new function or grant. A retried reset or watcher run would repeat its side effects |
| (not in spec) | Two switches (ADR-022). `models_enabled` (`WF_MODELS_ENABLED`), on in both configs with Amazon Nova: off means no Bedrock grants, template drafts and the offline harness engine. `agents_enabled` (`WF_AGENTS_ENABLED`), off in both configs: no Agents stack; `start_deep_triage` returns `TEMPORARILY_UNAVAILABLE` before queuing a job; the deploy workflows skip the agents image and `release-demo` the evals | The target account is on the AWS Free plan. Claude is a Marketplace product there, but Nova is first-party and paid from the credits. AgentCore is not yet deployed on the account. Refusing up front beats a job that can only fail. The infra tests synthesize every combination |
| TD §11 (custom domains) | Domains optional: with `certificate_arn: null` (both shipped configs) the services answer on their execute-api and cloudfront.net hosts; `scripts/default_hosts.py` writes those into the config from each stack's `DefaultHost` output, and `make cdk-deploy` runs it before a deploy and, after the first one, deploys again. `pending.invalid` stands in until then. The budget counts usage before credits | A domain can't be paid for with AWS credits, and the account runs on credits only. The assigned hosts are as stable as the stacks, so the config keeps fixed strings and the no-cross-stack-cycles design holds. A budget net of credits would read $0 |
| (not in spec) | `dev` ships with `provisioned_concurrency: 0` | About $9 a month of the credits; cold starts are acceptable in `dev`, and `demo` keeps one for its latency gate |
| (not in spec) | `boto3` is in the root dev group | Remote conformance and the evals read the canary secret in `dev`/`demo`, and `demo.py` invokes Lambdas; `uv sync` didn't install it before, so CI's remote conformance would have failed |
| (not in spec) | `demo.py` fills unset `WF_*` variables from `.env` | `make demo-clock` must run its watcher with the same `WF_DEMO_MODE` as `make dev` |
| `outbox-retry` re-arms one row (TD §17.3) | `--id N` (repeatable) or `--all-dead`; only rows with status `dead` | Never resends a row that already went out |
| `demo-status` prints state (DD §24) | Also checks the four scenario scores; exits 1 on drift | Catches a wrong clock offset before recording (DD §23) |

## Update (2026-10-16): fixes from the agent architecture audit

Rules enforced in code rather than in prompts, and what each review and turn now records.

| Area | Built | Why |
|---|---|---|
| Spoken confirmation (harness) | A preview's `confirmation_token` and summary ride to the next turn in a third cookie, `wf_h_pending`: Fernet-sealed with the session's key, HttpOnly, at most 15 minutes. That turn's prompt names the waiting preview and its token; after a confirm, the token is kept for the 30 s undo window. A preview made in a view (`/api/tool`) is carried too. The offline router confirms on a plain "yes" and undoes on "undo" | Only `{role, text}` travels between turns, so "yes" could never reach the token |
| Confirmation gate (harness) | `mcp_tool` refuses, before the server, a `confirm_action` whose token came from a preview in the same turn, or that isn't the waiting preview's token | The prompt said "only after the user says yes"; nothing enforced it |
| Model chain (harness) | A model that fails after a successful call to a tool not marked `readOnlyHint` ends the turn with that call's summary and "I stopped rather than make the change twice"; no next model, no offline router | A second model replayed the turn and repeated the change |
| `set_standing_order` | An identical active rule (same conditions and channel; jsonb equality) returns the existing order: "You already have that standing order" | A repeated request doubled every alert |
| Who answered (agents → result) | The output carries `engine` (`strands` or `offline`); the dispatcher also copies the event's `engine` into an output without one. `get_triage_result` returns `engine` and `checked`, and says "This was the quick, rule-based review, not the full one." when the offline engine answered; the Slack plan is headed "Quick review" | `final_output` kept only `output`, so the label was lost and a fallback read as a full review |
| Critic and specialists (agents) | Each specialist run is recorded (`ok`, `failed`, `timed_out`; the critic `issues` unless it answered `{"ok": true}`) and stored as `specialists`. A model review whose last critic run didn't approve it is `checked: false`, its confidence and its actions' confidences are capped at 0.5, and it says "This review wasn't double-checked, so treat it as a first pass." | A skipped or failed critic was invisible |
| Memory records | "… Watchfloor recommended P01, P04." The Context prompt treats such a record as an earlier recommendation, not a preference | It read as the user's choice. Memory stays off until the confirm path, the sweeper and undo write what the user did (confirmed, expired, undone); none of them writes memory yet |
| Spoken summary | `speakable` also strips e-mail addresses and phone numbers (separators required, so amounts, dates and day counts stay) | Contact details read aloud |
| Usage | One `llm_usage` line per harness turn and per triage job: `scope`, `engine`, `model`, `calls` (Strands `cycle_count`), `input_tokens`, `output_tokens` (`accumulated_usage`), and for jobs `job` and `outcome`. Tokens a failed or timed-out model spent are included | Cost per job was unmeasured. No metric filter is deployed (a custom metric can cost beyond the free tier); query it instead |
| Eval guard | Deployed runs count only `engine: strands` results; others are listed as `other_engine` and count as not completed (a hostile deal as unfinished). `--max-jobs` (kinds alternate), `--pace` (15 s between starts when deployed), and deployed runs between 2026-10-22 and 2026-11-20 are skipped unless `--during-judging` | A fallback run could pass on the rule-based engine, and an eval run spends the quota the judges need |
| Gemini tiers | `WF_GEMINI_HAIKU_MODEL_ID` (`gemini_haiku_model`) runs the fast tier: harness, drafts, Analyst, Context. Unset, everything stays on `WF_GEMINI_MODEL_ID`; the shipped configs leave it unset | Lets the busiest tier move to a model with its own free-tier quota; check the limits in AI Studio first |

Usage, summed in CloudWatch Logs Insights over the harness and triage log groups:

```
filter @message like /llm_usage/
| parse @message "scope=* engine=* model=* calls=* input_tokens=* output_tokens=*" as scope, engine, model, calls, inp, out
| stats count(*) as units, sum(calls) as model_calls, sum(inp) as input_tokens, sum(out) as output_tokens by scope, engine
```
