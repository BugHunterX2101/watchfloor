# Friction log

A live record of rough edges in the platforms Watchfloor is built on, for the Devpost feedback
(DESIGN.md §27). Every spike outcome, success or failure, gets an entry. Required fields per entry:
task, steps, expected vs. actual, severity, workaround, suggestion.

Severity: **blocker** (no way forward) · **major** (needed a design change) · **minor** (cost
time, simple workaround) · **note** (worth knowing).

## Open spikes

Each spike gets an entry when it is answered (DESIGN.md §27). Since entry 4, every question about
the real Alexa+ client is **blocked**: answering it needs the toolkit's simulator or a deployed
add-on, and the toolkit is closed to our account.

| Spike | Question | Status |
|---|---|---|
| S1 | Alexa+ MCP Toolkit, CLI and simulator access; is `MCP-Protocol-Version` always sent? | access answered: entry 4 (no access; Path B). Header question blocked by entry 4 and still an assumption: the server rejects a missing header after `initialize` (CF-05, stricter than the spec's 2025-03-26 fallback). The harness always sends it; a real Alexa+ client that didn't would need that gate relaxed |
| S2 | Account-linking client registration; managed IdP vs. self-hosted (ADR-006) | IdP answered by ADR-006 (self-hosted). Alexa+ client registration blocked by entry 4 |
| S3 | MCP Apps support in the simulator and on devices | spec side answered (ext-apps 2.0.3: nested `_meta.ui.resourceUri`, `text/html;profile=mcp-app`, `ui/initialize` handshake; views verified in the harness host). Alexa+ host support blocked by entry 4 |
| S4 | Elicitation or sampling support in the Alexa+ client | blocked by entry 4 |
| S5 | Round trip from Alexa+ to `us-east-1` Lambda | blocked by entry 4 (the harness's round trip is measured by the latency probe) |
| S6 | Bedrock profile ids, AgentCore session ids, Strands structured output, Memory namespaces | partly answered: entries 1–3. Model ids answered by entries 8–9 (Nova in-Region ids; every quota 0). AgentCore session and Memory questions blocked by entry 9 |
| S7 | Locale and timezone passed by Alexa+ | blocked by entry 4; the workspace timezone is used |

---

## 1. Strands Agents cannot share an environment with the current MCP SDK

- **Date:** 2026-10-08 · **Area:** Strands Agents / MCP Python SDK · **Severity:** major
- **Task:** Add the Strands-based triage agents to the uv workspace that holds the MCP server.
- **Steps:** Add `strands-agents` to `services/agents/pyproject.toml` and run `uv lock`.
- **Expected:** One lock for the whole repository.
- **Actual:** No resolution. `strands-agents` 1.59.0 requires `mcp<2.2`, and the server needs
  `mcp>=2.3` for the 2025-11-25 protocol version that Alexa+ uses.
- **Workaround:** Make `services/agents` a standalone uv project with its own lock
  ([ADR-011](adr/0011-agents-standalone-project.md)). The agents reach the server only over HTTP,
  so mixed SDK versions work at runtime.
- **Suggestion:** Widen the Strands `mcp` bound to the 2.x line, or publish an MCP-client-only
  extra that does not pin the SDK.

## 2. AgentCore Memory retrieval needs exactly one namespace argument

- **Date:** 2026-10-08 · **Area:** Bedrock AgentCore SDK (`MemoryClient`) · **Severity:** minor
- **Task:** Recall a user's earlier triage outcomes before the Lead plans a review.
- **Steps:** Call `MemoryClient().retrieve_memories(memory_id=..., query=..., top_k=5)`.
- **Expected:** A search across the actor's memories.
- **Actual:** The call needs exactly one of `namespace` (exact) or `namespace_path` (prefix). The
  namespace layout depends on the strategies configured on the memory resource, which the Python
  SDK does not show.
- **Workaround:** Search by prefix `/actors/{actorId}/`, overridable with
  `WF_MEMORY_NAMESPACE_PATH`. Recall returns nothing rather than failing the job when retrieval
  errors. Still to confirm against the real resource (**⚠ Verify S6**).
- **Suggestion:** A way to list a memory's strategy namespaces from the SDK, and an example of
  prefix retrieval in the docs.

## 3. The AgentCore app picks its listen address from Docker markers

- **Date:** 2026-10-08 · **Area:** Bedrock AgentCore SDK (`BedrockAgentCoreApp`) · **Severity:** note
- **Task:** Run the agent locally (:8083) and in the AgentCore container (:8080).
- **Steps:** `app.run(port=...)` with no host.
- **Expected:** One documented default.
- **Actual:** The host is `0.0.0.0` only when `DOCKER_CONTAINER` is set or `/.dockerenv` exists;
  otherwise `127.0.0.1`. A container without those markers would not be reachable.
- **Workaround:** The agents' Dockerfile sets `DOCKER_CONTAINER=1`. Locally, loopback is what we want.
- **Suggestion:** Document the rule next to `run()`, or accept an environment variable for the host.

## 4. The Alexa+ toolkit is closed to an account that missed the access window

- **Date:** 2026-10-15 · **Area:** Alexa+ MCP Toolkit (`alexa-ai` CLI) · **Severity:** blocker
- **Task:** Install the `alexa-ai` CLI to scaffold, deploy and account-link the add-on (spike S1).
- **Steps:** Following "Set Up Your Development Environment": an IAM user `alexa-ai-user` with one
  inline policy allowing `sts:AssumeRole` on
  `arn:aws:iam::372468808636:role/AddOn3PDeveloperToolsRead`, a profile `alexa-ai` assuming that
  role (`source_profile = alexa-ai-user`, `us-west-2`), then
  `aws sts get-caller-identity --profile alexa-ai`.
- **Expected:** The assumed role in account `372468808636`, then `aws codeartifact login` and
  `npm install -g @alexa-ai/cli`.
- **Actual:** `AccessDenied ... is not authorized to perform: sts:AssumeRole`, for the dedicated
  user and for an administrator alike. The IAM policy simulator allows the call on our side, so the
  role's trust policy refuses the account. The CLI, the Local Inspector and the web simulator all
  sit behind that role, and the access window had closed. The setup page does not say how an
  account gets added, or what the error means.
- **Workaround:** Path B, which the rules allow: the simulated Alexa+ harness (`services/harness`)
  is the MCP client, against the same self-hosted 2025-11-25 Streamable HTTP server, real OAuth and
  real protocol. The add-on work stays ready for when access opens: the Tier 1 service token
  ([ADR-024](adr/0024-alexa-service-token.md)), the listing pages and images, and
  `scripts/addon_assets/fill_addon.py` (TECHNICAL.md §14). The test IAM user, its access key and
  both CLI profiles were deleted the same day.
- **Suggestion:** Publish the CLI and the Local Inspector on public npm, as the MCP Inspector is,
  and keep only deploys gated. Failing that, an access-request form linked from the setup page, and
  a note that `AccessDenied` on this role means the account is not allowlisted.

## 5. EC2 on the Free plan retries an ineligible instance type instead of failing

- **Date:** 2026-10-09 · **Area:** AWS Free plan / EC2 Auto Scaling · **Severity:** minor
- **Task:** First `dev` deploy of the network stack, with a fck-nat NAT instance
  ([ADR-009](adr/0009-nat-instance.md)).
- **Steps:** `cdk deploy` with the NAT Auto Scaling group on `t4g.nano`.
- **Expected:** The instance launches, or the deploy fails at once with the reason.
- **Actual:** The group retried the launch for over half an hour with "The specified instance type
  is not eligible for Free Tier". The Free plan launches only free-tier-eligible types; the arm64
  ones in us-east-1 are `t4g.micro` and `t4g.small`. The plan also limits RDS backup retention.
- **Workaround:** `t4g.micro` in every environment, and RDS backup retention of 1 day in `dev`
  ([ADR-014](adr/0014-free-plan-instance-types.md)).
- **Suggestion:** Treat an ineligible type as a permanent launch failure, so the group and the stack
  fail fast, and name the eligible types in the error.

## 6. CloudFront is closed to a new account until AWS Support verifies it

- **Date:** 2026-10-09 · **Area:** Amazon CloudFront · **Severity:** major
- **Task:** Serve the harness page from S3 behind CloudFront (DESIGN.md §17).
- **Steps:** The first `dev` deploy of the harness stack.
- **Expected:** A distribution.
- **Actual:** "Your account must be verified before you can add new CloudFront resources."
  Verification is a support case with no stated turnaround.
- **Workaround:** A `cdn` switch, off in `dev`: the harness API Lambda serves the built page itself
  on its execute-api host, so the page and the API keep one origin and the session cookie stays
  first-party ([ADR-015](adr/0015-harness-without-cloudfront.md)). The cost is no edge caching and a
  cold start on the first request.
- **Suggestion:** State the verification step in the CloudFront getting-started guide, and show the
  account's verification status in the console with a way to request it.

## 7. A new account cannot reserve any Lambda concurrency

- **Date:** 2026-10-09 · **Area:** AWS Lambda · **Severity:** minor
- **Task:** Reserve concurrency for the MCP server (20) and the dispatcher (3), DESIGN.md §18.1.
- **Steps:** The first `dev` deploy.
- **Expected:** The reservations apply.
- **Actual:** "Specified ReservedConcurrentExecutions for function decreases account's
  UnreservedConcurrentExecution below its minimum value of [10]". A new account's concurrency quota
  is 10 and Lambda keeps 10 unreserved, so no reservation of any size can succeed.
- **Workaround:** A `reserve_concurrency` switch, off in `dev`; the functions share the account's
  10 executions ([ADR-016](adr/0016-no-reserved-concurrency-on-a-new-account.md)). A quota increase
  was requested on 2026-10-10.
- **Suggestion:** Say in the error that the account quota itself is the limit, and link the
  Service Quotas request.

## 8. Claude on Bedrock is out of reach on the Free plan

- **Date:** 2026-10-10 · **Area:** Amazon Bedrock on the AWS Free plan · **Severity:** major
- **Task:** Run the harness, drafts and triage agents on Claude Sonnet- and Haiku-class models
  (DESIGN.md §26).
- **Steps:** Enable the Claude models for a Free plan account.
- **Expected:** On-demand use paid from the account's credits.
- **Actual:** Claude on Bedrock is sold through AWS Marketplace, which the Free plan restricts, so
  `dev` ran with models off.
- **Workaround:** Amazon Nova: Lite for the fast slot, Pro for reasoning, both in-Region in
  us-east-1. Nova writes `<thinking>` before tool calls and calls tools best with greedy decoding,
  so replies are stripped of it and agents run at temperature 0, top-k 1
  ([ADR-022](adr/0022-nova-on-the-free-plan.md)).
- **Suggestion:** Mark in the Bedrock model catalog which models a Free plan account can invoke.

## 9. Every Bedrock and AgentCore quota on the account is 0

- **Date:** 2026-10-15 · **Area:** Amazon Bedrock / Bedrock AgentCore service quotas ·
  **Severity:** blocker
- **Task:** Run Nova calls and create the AgentCore Memory resource for deep triage.
- **Steps:** Deploy with `models_enabled: true` and `agents_enabled: true`; check Service Quotas.
- **Expected:** Default on-demand quotas, as on an established account.
- **Actual:** `CreateMemory` returned 403 "contact customer support". Service Quotas shows every
  Bedrock inference quota at 0 (on-demand, cross-Region and global, for every model), and none of
  the request quotas is adjustable. AgentCore's quotas (Memories, Active Session Workloads, Docker
  image size) are 0 too. Only AWS Support can raise them.
- **Workaround:** Every model caller tries Bedrock, then Gemini through Strands' `GeminiModel`, then
  a deterministic fallback; the triage agents run as a plain Lambda function (`agents_host: lambda`)
  ([ADR-023](adr/0023-gemini-when-bedrock-cannot-run.md)). The deterministic risk scores never
  depended on a model (ADR-002).
- **Suggestion:** Show at sign-up and on the Bedrock console that a new account's quotas start at 0,
  and make the inference quotas adjustable through Service Quotas like other services.

## 10. Strands retries outlast the API Gateway timeout

- **Date:** 2026-10-15 · **Area:** Strands Agents (Bedrock model provider) · **Severity:** minor
- **Task:** Hand a harness turn to the offline engine when the model is throttled.
- **Steps:** A harness turn with Bedrock quotas at 0 (entry 9).
- **Expected:** The model call fails quickly and the fallback answers.
- **Actual:** The turn waited out Strands' default backoff (4 s, doubling to 64 s) and hit API
  Gateway's 30 s integration limit, so the user got a timeout instead of an answer.
- **Workaround:** `retry_strategy=None` on every agent, two quick standard-mode botocore retries
  in the harness, and a 25 s cap on a turn ([ADR-022](adr/0022-nova-on-the-free-plan.md), update of
  2026-10-15).
- **Suggestion:** A total deadline per agent call, alongside the retry count, so an agent behind an
  HTTP front door can bound its latency without giving up retries entirely.

## 11. Lambda refuses every call on the account, without a reason

- **Date:** 2026-10-16 · **Area:** AWS Lambda (account) · **Severity:** blocker
- **Task:** Deploy `dev` after a code change (`cdk deploy`, wf-dev-data first).
- **Steps:** The stack updated the migrator function; then, as the administrator,
  `aws lambda list-functions`, `get-account-settings` and `get-function-configuration`.
- **Expected:** The update goes through, as every deploy had since 2026-10-09.
- **Actual:** `AccessDeniedException` with no message on the update and on its rollback, which left
  the stack in `UPDATE_ROLLBACK_FAILED`. Every Lambda call since returns the same in every Region,
  for a caller with `AdministratorAccess`, outside any AWS Organization; other services answer
  normally, the Free plan is active with credits left, and CloudTrail has no record of the denied
  calls. API Gateway could no longer invoke the functions, so the live environment answered 500.
  The console and the error say nothing about why or what to do.
- **Workaround:** `continue-update-rollback` with the migrator skipped, then the services moved to
  ECS Fargate behind the same HTTP APIs (VPC link and Cloud Map), with the Lambda resources removed
  from the templates so CloudFormation never calls Lambda again
  ([ADR-025](adr/0025-ecs-when-lambda-is-refused.md)). A support case asks for the restriction's
  reason and its lifting.
- **Suggestion:** When an account-level restriction denies a service, say so in the error (a
  distinct error code and a link to the AWS Health event or the support path), not an empty
  `AccessDeniedException` that reads like an IAM problem.
