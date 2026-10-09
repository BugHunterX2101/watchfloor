# ADR-005: Postgres is the source of truth for rules and preferences

- **Status:** Accepted
- **Source:** [DESIGN.md §26](../DESIGN.md#26-architecture-decision-records), copied verbatim. A change to this decision gets a new ADR; this file is never rewritten (TECHNICAL.md §20.5).

## Context

The watcher needs exact evaluation; the hot path can't afford remote memory calls.

## Decision

Rules and preferences in Postgres; AgentCore Memory only for learned agent context.

## Consequences

PRD FR-09 is refined (D3).
