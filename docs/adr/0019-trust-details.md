# ADR-019: Trust details: approvals, evidence and freshness

- **Status:** Accepted (2026-10-10)
- **Refines:** ADR-018 (interaction details), DESIGN.md §8 (propose → confirm), §15 (views), §17 (harness)

## Context

A design review of comparable products found that Watchfloor's next gain is making trust visible,
not more polish. Agent products (Claude in Chrome, Copilot Studio, Claude Code, GitHub's coding
agent) share a vocabulary: preview before acting, approvals scaled to impact, receipts, logs.
Revenue tools (HubSpot, Salesforce Pipeline Inspection, Gong, Clari) pair every score with signed
drivers, change and freshness. Research also warns that explanations raise acceptance whether or
not the AI is right (Bansal et al., CHI 2021), so evidence must be checkable, not just persuasive.

The server already holds the two guarantees the screens now state. A confirmation token is bound
to a hash of the exact payload, and an expired or superseded token runs nothing (DD §8). This ADR
changes only the views and the harness. No tool contract changes.

## Decision

**Confirm sheet**

- **Wording.** A verb-first title, and a button that names the action ("Reassign owner", "Post to
  #deals", "Create task", "Draft follow-up", "Update deal") rather than "Approve". The Alexa+ guide
  reserves the primary colour for that final action.
- **What changes.** A before → after diff of the changed fields (owner, close date, amount), the old
  value struck through, followed by a **What happens** list built from `preview.side_effects`.
- **Two tiers.** Reassigning an owner, posting to a channel and editing deal fields affect other
  people, so the sheet gets a firmer frame and says "Affects your team". Creating a task and
  drafting a follow-up stay light.
- **Expiry wording.** On expiry the sheet dims and says "Expired. Nothing was changed.", once
  aloud to screen readers too.

**Done receipt**

- **Contents.** Each side effect in the past tense, each with a drawn check, plus when it was
  recorded and the audit reference. All of this was already in ConfirmOut but not shown.
- **Already done.** A second confirm of the same token says it ran earlier and nothing ran twice.

**Board**

- **Headline.** The number to read from across the room: "$420K at risk · 1 deal".
- **Numbered rows.** Rows are numbered in the order the answer lists them.
- **Freshness.** A briefing older than an hour reads "Last updated 3 h ago" in amber instead of
  passing for current.

**Bands everywhere:** a word, a colour and a shape. Triangle is at risk, square is watch, circle is
healthy, so a band reads under Color Correction, Color Inversion and in greyscale (NHS status
palette pattern).

**Deal detail**

- **The score says what it measures.** It reads "/100 risk": higher is worse here, unlike CRM
  health scores, so it is labelled every time.
- **Freshness.** "scored 9:12", or "scored 3 h ago" in amber when stale.
- **Trend line.**
  - It keeps a fixed 0–100 axis.
  - It shades the at-risk zone (65 and up) and dashes the watch line (40).
  - Its caption gives the change ("up 41").
  - The end dot turns hollow when the data is stale.
- **Factor bars.** Only the biggest driver gets the strong bar, and it is named in words ("biggest
  driver"). The other bars stay muted (Few's bullet-graph restraint).

**Motion in views** stays within DD §15.2's 150 ms. A press settles to 0.97 over 120 ms on a strong
ease-out (`cubic-bezier(0.23, 1, 0.32, 1)`). These are transitions, not keyframes, so a second tap
interrupts cleanly.

**Harness**

- **Plain-language trace.** Each span leads with what the step did ("Prepared a preview · nothing
  changed yet", "Ran the approved action"), with the tool id beside it. Calls that change something
  carry a pencil and the word "writes", following the tools' `readOnlyHint` annotations.
- **Wait ladder.** "Working on it…" at once, "Still working…" at 3 s, "Taking longer than usual…" at
  10 s, in the orb label and the live trace card (NN/g response-time limits).
- **Interrupted replies.** When speech is cut off (Escape, the mic, a new question), the words never
  spoken stay faint and the line ends "· stopped", so the transcript shows what was actually heard.
- **Fix.** A reply cut off by a new question no longer resets the orb to idle while that question
  is being answered.

**Server: an approval covers what its preview showed.**

- **What is recorded.** A reassignment stores the owner the preview showed (`from_owner_id`). A
  close-date or amount change stores the old value (`from_close_date`, `from_amount_cents`). These
  sit in the hashed params, so they are bound to the token like the rest.
- **What happens at confirm.** If the deal no longer matches, the action is refused with
  `INVALID_ACTION`: "Kestrel Biotech's owner changed after that preview, so nothing was changed",
  with "Ask me to propose it again" as the recovery.
- **Older proposals.** Proposals stored before this change have no such keys and skip the check.
- **No contract change.** The error code and the tool schemas are unchanged.

## Deferred

- **Undo.** Undo on the receipt, such as a delayed channel post or deleting a just-created task,
  needs new server tools and an outbox delay.
- **"More time" on a preview.** Needs a server change to extend the token.

Reasons already carry their benchmark ("No two-way contact for 9 days; typical for Proposal is 6").
Showing a source and age per reason as a chip would need the scorer to return them per reason. Each
deferred item is a contract change, to be decided separately.

## Consequences

- **Bundle sizes.** Views are 72.8 KB gzip each, under the 150 KB gate. The harness is 66 KB JS and
  6.8 KB CSS gzip.
- **Source of truth.** The band thresholds in `format.ts` mirror `rigs.yaml` enter values. If the
  model's bands change, both change.
