# ADR-003: Two-tool confirmation instead of elicitation

- **Status:** Accepted
- **Source:** [DESIGN.md §26](../DESIGN.md#26-architecture-decision-records), copied verbatim. A change to this decision gets a new ADR; this file is never rewritten (TECHNICAL.md §20.5).

## Context

Elicitation support in Alexa+ is unknown (S4), and the server is stateless.

## Decision

`propose_action` + `confirm_action` with a bound single-use token.

## Consequences

Works on any MCP client; the MCP App's Approve button reuses the same path; the model sees the token (acceptable: bound, short-lived, single-use).
