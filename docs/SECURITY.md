# Security register

What was found, what was changed, what is accepted, and what has **not** been verified. Each resolved
item names the test that fails without the fix.

## Resolved

| # | Finding | Fix | Test |
|---|---------|-----|------|
| 1 | A validly signed token for a user that does not exist, with a real workspace id, got that workspace's briefing. | `make_context` refuses an unknown user (`UnknownUser`, "Unknown user"). | `tests/security/test_tenant_isolation.py` |
| 2 | The app role could rewrite `workspaces`, `schema_migrations` and `audit_log` rows. | Migration `0006_least_privilege.sql`: grants cut back, audit log append-only, deletes only past the 7-day retention policy. | `tests/security/test_db_privileges.py` (also guards every future `workspace_id` table for forced RLS and a policy) |
| 3 | A saturated connection pool hung requests with no bound. | `acquire` times out after 2 s; MCP answers `TEMPORARILY_UNAVAILABLE`, the auth server 503 with `Retry-After`. | `tests/integration/test_db_faults.py` |
| 4 | `httpx` logs each request URL at INFO, which put the Slack webhook URL (a secret) in the logs on every post. | The `httpx` logger is held at WARNING in the worker. | `tests/security/test_log_hygiene.py` |
| 5 | Outside local/test the harness accepted an `http://` OAuth redirect URI. | `load()` raises `Misconfigured` unless it is https. | `services/harness/api/tests/test_hardening.py` |
| 6 | The Host check split on the first colon, so `mcp.example:80@evil.example` and `mcp.example:abc` passed as `mcp.example`; a second `Origin` header was never read; a repeated `Host` was not refused; an allowlist entry with a trailing slash silently matched nothing. | `EdgeGuard` takes exactly one Host that is a well-formed `host[:port]` (port at most 65535), refuses more than one Origin, and compares origins lower-cased without a trailing slash. | `tests/security/test_edge_guard.py` |

Confirm / undo / extend races are proven exactly-once in `tests/security/test_races.py`; the outbox's
duplicate-delivery window is pinned in `tests/integration/test_delivery_faults.py`. Removing the
`status='pending'` guard in `proposals.claim` or the `FOR UPDATE` in `deals.lock` makes those tests fail.

## Edge behaviour the MCP 2025-11-25 spec requires, and where it is proven

Read from <https://modelcontextprotocol.io/specification/2025-11-25/basic/transports> on 2026-10-17.

| Requirement (Streamable HTTP) | Here |
|---|---|
| Validate `Origin` on all incoming connections; present and invalid is 403 | Every method and path; `test_the_origin_rule_covers_every_method_and_path` |
| A POST may be answered with `application/json` instead of an event stream | One JSON object, no `Mcp-Session-Id`; the server is stateless |
| GET must return `text/event-stream` or 405 | 405 with `Allow: POST` (DELETE too) |
| Host check (DNS rebinding) | 421; exactly one well-formed Host, or the gateway's `x-wf-host` on ECS |

Streaming tokens back to the caller is therefore not needed for conformance. A tool call is one request and
one complete answer, so there is no partial output for a buffering proxy to hold back.

## Accepted

- **`braces` ReDoS (GHSA-vfj7-8cjw-p6xm)** reaches `services/mcp_server/ui` through `vite-plugin-singlefile`
  → `micromatch`. It is a build-time dev dependency, no fixed release exists, and the pattern input is the
  repository's own files. Production audits should run `npm audit --omit=dev`.
- **`mcp` 2.1.1, CVE-2026-59951** (unbounded session memory in the stateful Streamable HTTP *server*; fixed in
  1.30.0 and 2.2.0) in `services/agents` and `services/harness/api`. `strands-agents` pins `mcp<2.2`
  (ADR-011), so both stay on 2.1.1. Neither serves MCP: no code in them imports `mcp`, they use the strands
  client only. The MCP server itself is on 2.3.0 and stateless, so it is not exposed. The one exception is
  written out, by id and by project, in `.github/workflows/security.yml` and `make audit`;
  `tests/security/test_ci_security_config.py` fails if the two disagree or if an ignored id is missing
  from this page. Remove it when `strands-agents` allows `mcp>=2.2`.
- **Health checks** treat any status below 500 as alive, so a 421 from the Host check does not fail them.
  The cost is that `/healthz` cannot report database trouble to the orchestrator; the alarms do.

## Data sent to external AI providers

- **Gemini API, unpaid tier** (ADR-023, the model wherever Bedrock cannot run): Google's terms
  (<https://ai.google.dev/gemini-api/terms>, read 2026-10-17) say submitted content is used to improve its
  services, human reviewers may read it, and "Do not submit sensitive, confidential, or personal
  information". **Only the synthetic seed workspace may go to it.** Real CRM data needs the paid tier, whose
  terms say prompts are not used to improve products, or Bedrock.
- **Bedrock**: AWS's own data-handling terms apply; they were not re-read for this register.
- CRM notes are untrusted input to every model call. The triage output gate and the hostile-note seed
  (`db/seed/hostile_notes.py`) exist for that reason.

## Not verified

These could not be run, and nothing here claims they pass:

- Deploy to a staging environment, migrations there, seeded smoke and remote conformance
  (the AWS account's compute block).
- An RDS snapshot or point-in-time restore. `scripts/restore_drill.py` proves the dump/restore/compare logic
  against local Postgres only.
- Load against a deployed environment, provider-failure behaviour under load, and a RIGS backtest against real
  outcomes: there is no historical CRM data, so no accuracy figure is claimed. A local concurrency curve exists
  (`docs/perf/load-baseline.md`); it shares one laptop with its load generator and database, so it is not an
  AWS number.
- Independent security review.
- Container image scan. Trivy 0.58.2 was run over the working tree on 2026-10-17 (lock files, Dockerfiles, the
  CloudFormation templates and a secret scan; HIGH and CRITICAL, unfixed ignored) and found no vulnerability or
  misconfiguration. Its one hit was the local dev signing key under `.secrets/`, which is gitignored and
  untracked, so the CI job skips that directory. No built image was scanned. `.github/workflows/security.yml`
  has not run on GitHub, and Dependabot's acceptance of `.github/dependabot.yml` is unconfirmed.

## Running the server on a plain container host (for example Render)

What the code needs, and what is unproven:

- It listens on `$PORT` (`run.sh`) and binds `WF_BIND_HOST`; set `WF_BIND_HOST=0.0.0.0`.
- `WF_ALLOWED_HOSTS` must be the service's public host name, and `WF_ALLOWED_ORIGINS` the harness origin.
  If left at the `localhost` default every request gets 421.
- It is not one app: the MCP server, the auth server (OAuth, ES256 keys), the harness and the workers are
  separate services, state is PostgreSQL 16 with row-level security (roles `wf_app`, `wf_auth`, `wf_owner`),
  and the outbox and jobs use SQS. It does not use DynamoDB.
- Render's published free-tier limits (<https://render.com/docs/free>, read 2026-10-17): a free web service
  spins down after 15 minutes without traffic and takes about a minute to wake; free Postgres expires 30
  days after creation (14-day grace, then deleted) and only one is allowed; 750 free instance hours per
  month. Whether Render's Postgres lets the migrator create the three roles, and what Host header its
  health check sends, were **not** checked.
- Pointing it at SQS and Bedrock from outside AWS needs AWS credentials as host environment variables.
