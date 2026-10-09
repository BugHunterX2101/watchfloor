# ADR-004: Agents as tools, not a dynamic swarm or A2A

- **Status:** Accepted
- **Source:** [DESIGN.md §26](../DESIGN.md#26-architecture-decision-records), copied verbatim. A change to this decision gets a new ADR; this file is never rewritten (TECHNICAL.md §20.5).

## Context

15 days; reliability over novelty.

## Decision

A Lead orchestrator with four specialist agents exposed as tools, plus one critic revision.

## Alternatives

A Strands swarm (less predictable); A2A microservices (more deployment surface).

## Consequences

Easy tracing and evals. A2A is P2.
