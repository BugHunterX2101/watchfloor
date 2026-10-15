# Friction log

A live record of rough edges in the platforms Watchfloor is built on, for the Devpost feedback
(DESIGN.md §27). Every spike outcome, success or failure, gets an entry. Required fields per entry:
task, steps, expected vs. actual, severity, workaround, suggestion.

Severity: **blocker** (no way forward) · **major** (needed a design change) · **minor** (cost
time, simple workaround) · **note** (worth knowing).

## Open spikes

These still need an entry each (DESIGN.md §27).

| Spike | Question | Status |
|---|---|---|
| S1 | Alexa+ MCP Toolkit, CLI and simulator access; is `MCP-Protocol-Version` always sent? | answered: entry 4 (no toolkit access; Path B) |
| S2 | Account-linking client registration; managed IdP vs. self-hosted (ADR-006) | open |
| S3 | MCP Apps support in the simulator and on devices | spec side answered (ext-apps 2.0.3: nested `_meta.ui.resourceUri`, `text/html;profile=mcp-app`, `ui/initialize` handshake; views verified in the harness host). Alexa+ host support still open |
| S4 | Elicitation or sampling support in the Alexa+ client | open |
| S5 | Round trip from Alexa+ to `us-east-1` Lambda | open |
| S6 | Bedrock profile ids, AgentCore session ids, Strands structured output, Memory namespaces | partly answered: entries 1–3 |
| S7 | Locale and timezone passed by Alexa+ | open |

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
  `scripts/addon_assets/fill_addon.py` (TECHNICAL.md §14).
- **Suggestion:** Publish the CLI and the Local Inspector on public npm, as the MCP Inspector is,
  and keep only deploys gated. Failing that, an access-request form linked from the setup page, and
  a note that `AccessDenied` on this role means the account is not allowlisted.
