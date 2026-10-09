# ADR-013: A deterministic offline triage engine for local and test only

- **Status:** Accepted (2026-10-08)
- **Refines:** DESIGN.md §10.2, TECHNICAL.md §8 (local development)

## Context

A deep triage job crosses the dispatcher, the job-token grant, the agent runtime and the MCP agent
tools. Exercising that loop on a laptop or in CI should not need Bedrock access, model ids or
spend. A silent fallback in a deployed environment, though, would make a demo look like it used the
agents when it did not.

## Decision

- When both `WF_SONNET_MODEL_ID` and `WF_HAIKU_MODEL_ID` are set, the agent runs the Strands Lead.
- When they are not, and only when `WF_ENV` is `local` or `test`, it runs `agents/offline.py`:
  - the drivers are the top RIGS reasons, with their own evidence ids;
  - the actions come from playbook triggers, in playbook order, with params built from the deal;
  - the result passes the same output gate (ADR-012) as the Lead's draft.
- In `dev` and `demo` without model ids, every job ends with `misconfigured`. Nothing falls back.
- Every result event says which engine produced it: `{"type": "result", "engine": "strands" | "offline", ...}`.

## Consequences

- `make dev` runs the whole triage loop end to end with no AWS account.
- The offline engine is labelled in every result, so it can never pass for the agents.
- Its output is deterministic, which makes it a stable fixture for dispatcher and harness tests.
