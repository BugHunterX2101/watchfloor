# ADR-010: Explainable deterministic scoring (RIGS) over ML scoring in v1

- **Status:** Accepted
- **Source:** [DESIGN.md §26](../DESIGN.md#26-architecture-decision-records), copied verbatim. A change to this decision gets a new ADR; this file is never rewritten (TECHNICAL.md §20.5).

## Context

No labeled outcomes; trust (PRD H4); latency.

## Decision

A weighted heuristic with reasons and hysteresis.

## Consequences

V2 calibrates weights with logistic regression once won/lost data exists; `model_version` is tracked on every score.
