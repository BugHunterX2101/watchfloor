# ADR-006: Self-hosted minimal OAuth 2.1 server (pending S2)

- **Status:** Proposed: accepted once spike S2 confirms Alexa+ cannot use a managed IdP unchanged
- **Source:** [DESIGN.md §26](../DESIGN.md#26-architecture-decision-records), copied verbatim. A change to this decision gets a new ADR; this file is never rewritten (TECHNICAL.md §20.5).

## Context

Alexa+ needs RFC 9728, RFC 8414 at a specific path, PKCE S256, a `resource` parameter and no DCR. Managed IdPs vary in support for these.

## Decision

Build a small AS on Authlib with KMS-signed JWTs, unless the S2 spike shows a managed IdP passes the Local Inspector unchanged.

## Consequences

About 1.5 days of work; full control over metadata and error behavior.
