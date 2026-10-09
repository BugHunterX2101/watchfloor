# ADR-007: Agents access data only through MCP tools

- **Status:** Accepted
- **Source:** [DESIGN.md §26](../DESIGN.md#26-architecture-decision-records), copied verbatim. A change to this decision gets a new ADR; this file is never rewritten (TECHNICAL.md §20.5).

## Context

One authorization and audit path; avoid VPC attachment for AgentCore.

## Decision

Job-scoped tokens and agent-only read tools.

## Consequences

Slightly higher agent latency per tool call (acceptable on the warm path); a strong security story and visible MCP composability.
