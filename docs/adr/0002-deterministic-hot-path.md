# ADR-002: Deterministic hot path on Lambda with provisioned concurrency

- **Status:** Accepted
- **Source:** [DESIGN.md §26](../DESIGN.md#26-architecture-decision-records), copied verbatim. A change to this decision gets a new ADR; this file is never rewritten (TECHNICAL.md §20.5).

## Context

C1 (500 ms).

## Decision

Precomputed RIGS, no LLM calls in tools, provisioned concurrency 1.

## Alternatives

Fargate (always warm, ~$9/month more, more ops); LLM in tools (violates C1).

## Consequences

Intelligence is split between Alexa+ (language) and the warm path (agents). Fargate remains the documented fallback.
