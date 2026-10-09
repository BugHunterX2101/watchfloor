# ADR-011: The agents service is its own uv project

- **Status:** Accepted (2026-10-08)
- **Amends:** TECHNICAL.md §5 (repository layout: every service a workspace member)

## Context

The MCP server needs `mcp>=2.3` for the 2025-11-25 protocol features it serves. `strands-agents`
1.59.0, which the Triage Lead and its specialists are built on, pins `mcp<2.2`. One uv workspace
resolves one version of each package, so the two cannot share a lock.

`watchfloor-core` does not import `mcp`, and the agents reach the MCP server only over HTTP
(ADR-007), so nothing needs both versions in one process.

## Decision

`services/agents` is a standalone uv project with its own `uv.lock`. It is excluded from the root
workspace and depends on `packages/core` by path (editable). The agents' Dockerfile installs from
that lock (`uv sync --frozen`), and `make bootstrap` / `make agents-sync` sync it separately.

## Alternatives

- **Pin the server to `mcp<2.2`:** loses the protocol version Alexa+ speaks. Rejected.
- **Drop Strands and write the agent loop on the Bedrock Converse API:** possible, but gives up
  structured output, tool plumbing and AgentCore integration that S6 already verified.
- **Install agents with `--no-deps` overrides:** unreproducible and fragile.

## Consequences

- Two locks to keep current. `make test-all` runs both suites (`make test-agents` for the agents).
- `watchfloor-core` must stay free of `mcp` imports. Today it is.
- When Strands lifts the pin, fold the project back into the workspace with a new ADR.
