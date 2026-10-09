# ADR-012: The Lead writes a draft; a deterministic gate decides what ships

- **Status:** Accepted (2026-10-08)
- **Refines:** ADR-004 (agents as tools), ADR-007 (agents read via MCP), DESIGN.md §10.3–10.6

## Context

DESIGN.md §10 has the Triage Lead return a `TriageOutput` directly. The critic agent checks
evidence, people and pricing (§10.6 rule 3), and the dispatcher validates the schema (rule 4).
Two problems showed up while building it:

1. Structured output against the strict `TriageOutput` schema fails the whole job when a model gets
   one field slightly wrong (for example an action param the playbook does not allow), even when the
   rest of the review is sound.
2. The critic is a model, and the dispatcher check covers only the shape. Neither can guarantee that
   every evidence id was actually read in this job, that a named person came from the review rather
   than from a planted note, or that an action would pass `propose_action`.

## Decision

- **Job-bound tools.** The six data tools and four specialist tools take no `deal_id`, `account_id`
  or workspace arguments. They are closures over one job's MCP session (`JobData`), so a model
  can never choose which record to read. The MCP server still enforces the job-scoped token.
- **Lenient draft.** The Lead fills `LeadDraft` (`agents/schemas.py`): the same shape as
  `TriageOutput`, with looser types and field descriptions written for the model.
- **Deterministic gate** (`agents/gate.py`) turns a draft into a `TriageOutput`:
  - risk drivers keep only evidence ids this job's tool calls returned; if none survives, the job
    ends `rejected` instead of shipping an unsupported review;
  - actions must name a play in the playbook that allows that action type, carry params that
    validate as `propose_action` input, contain no link or discount language, and are deduplicated
    by play and type; at most three ship;
  - an assignee or new owner must match, by first name, someone this job saw: the deal owner or
    a contact from `get_account_context`. A `reassign_owner` to a teammate the review never saw is
    dropped. The Lead can recommend a "find a new owner" task for a human instead;
  - the spoken summary loses ids and links and is cut to whole sentences within 45 words.
- **One playbook.** `packages/core/watchfloor_core/config/playbook.yaml` is the only copy. Agents
  read it through the `get_playbook` MCP tool; there is no `agents/playbook.yaml`.
- **Bounded specialists.** Analyst and context answers are cached after their one call. Strategist
  and critic are capped at two calls each, so the Lead can revise once. Each specialist has its own
  turn limit and timeout, and a failed or slow specialist returns an error to the Lead instead of
  ending the job.

## Consequences

- A model mistake costs one action or one driver, not the job.
- Every claim that ships cites evidence the job really read, and every action can be proposed as-is.
- The gate is plain Python with unit tests (`services/agents/tests/test_gate.py`), so the guarantees
  do not depend on prompt wording.
- The critic stays, now as a quality pass rather than the last line of defence.
