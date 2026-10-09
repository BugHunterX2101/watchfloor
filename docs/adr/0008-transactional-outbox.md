# ADR-008: Transactional outbox for every external side effect

- **Status:** Accepted
- **Source:** [DESIGN.md §26](../DESIGN.md#26-architecture-decision-records), copied verbatim. A change to this decision gets a new ADR; this file is never rewritten (TECHNICAL.md §20.5).

## Context

The confirm latency budget and atomicity.

## Decision

The outbox row is committed with the business write; SQS gives fast delivery; a sweeper guarantees it.

## Consequences

At-least-once delivery with deduplication keys.
