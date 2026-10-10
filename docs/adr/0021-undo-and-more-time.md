# ADR-021: Undo on the receipt, more time on a preview

- **Status:** Accepted (2026-10-10)
- **Completes:** ADR-019 (its two deferred items)
- **Refines:** DESIGN.md §6 (tools), §8 (action engine and outbox), §15 (views), §17 (harness)

## Context

ADR-019 made approvals visible: a verb-first sheet, a before → after diff and a receipt. It left
two gaps, both of which need server support:

- **No way back.** A wrong tap on "Reassign owner" could only be fixed by proposing the reverse
  change, and by then the channel had already seen the first one.
- **A hard five-minute limit.** A manager reading the sheet aloud to a colleague could watch it
  expire mid-sentence and have to ask again.

## Decision

### Two new customer tools

Users now see 14 tools. Agents still see none of the write tools.

| Tool | Scope | Annotations | What it does |
|---|---|---|---|
| `extend_preview` | read | `readOnlyHint: true` | Adds five minutes to a pending preview's expiry, never past 15 minutes after the preview was made. It keeps the same token and changes nothing else. |
| `undo_action` | write | `destructiveHint: true`, `idempotentHint: true` | Reverses an action that was confirmed in the last 30 seconds. It takes the same `confirmation_token`. |

`ConfirmOut` gains an optional `undo_until`. It is present only when the action can be undone.

### What undo reverses

| Action | Undo |
|---|---|
| `create_task` | Cancels the task and withdraws its Slack post. |
| `reassign_owner` | Restores the previous owner, re-scores the deal and withdraws the post. |
| `notify_channel` | Withdraws the post. |
| Close date or amount change | Restores the old values and re-scores the deal. |
| `draft_followup` | Can't be undone. The draft only ever goes to the requester. |

- **No history left behind.** An undone field change also deletes the `deal_history` rows it wrote. Their ids are kept with the proposal's stored result and are never shown. A close-date push that was taken back within 30 seconds is therefore not scored as a push.
- **All or nothing.** Undo runs in a savepoint. It refuses, and writes nothing, in these cases:
  - after the 30-second window
  - when the same thing has changed again since the action ran, for example someone else reassigned the deal
  - for a draft
  - when the action was already undone
- **What a refused or replayed token says.** Each refusal returns `INVALID_ACTION` with a recovery. A later `confirm_action` with an undone token reports that the action was undone.

### Slack posts wait out the window

- **Held back.** An action's Slack post is written to the outbox with `next_attempt_at` set to confirm time plus 35 seconds (the window plus a margin), so no worker can claim it earlier.
- **Queue delay.** The post is published to SQS with a matching `DelaySeconds`. The sweeper remains the fallback.
- **Withdrawal.** Undo marks the row `cancelled` only if no attempt has started (`attempts = 0`). Otherwise it is too late, and the whole undo is refused.
- **Migration 0005.** It adds `undone` to `action_proposals.status`, an `undone_at` column, and `cancelled` to `outbox.status`. Retention purges cancelled rows along with sent ones.

### Views

- **Confirm sheet.**
  - In the last minute, "More time" appears beside the timer.
  - Once time is added, the bar refills and the timer reads "· more time added".
  - Screen readers hear "More time added: about 6 minutes to decide" once, not on every tick.
- **Receipt.**
  - For 30 seconds it shows "Undo 0:27", a quiet secondary button.
  - The held post reads "Posts to #channel when undo closes" and is marked with a hollow ring, not a check, because it hasn't happened yet.
  - Only the outcome is a live region, so the countdown is never re-announced.
- **Undone receipt.** It has a turned-back arrow, the title "Undone", and a list of what was reversed.
- **No way back to a spent sheet.** The receipt replaces the sheet, and the undone receipt replaces the receipt.
- **Undo needs the token.** Undo appears only when the approval was tapped in the view, because only then does the view hold the token. By voice, "undo that" works through the model.

### Harness

- Views may call `extend_preview` and `undo_action`.
- The trace labels them "Gave the preview more time · nothing changed yet" and "Undid the approved action". The undo carries the write mark.
- An undo plays the done chord reversed (G5 E5 C5).
- Earcons now play for actions asked for by voice too, not only for taps.
- The prompt tells the model when to use each tool.

### Consistency pass across both frontends

- **Factor bars.** They read High, Some, Low or None instead of 0.83, so health is the only number on the screen.
- **Screen readers.** Bands are spoken "At Risk", as the voice says them, not spelled out letter by letter.
- **Board.**
  - The eyebrow is uppercase like every other eyebrow.
  - Health, band and amount columns line up from row to row.
- **Detail.** The freshness line no longer repeats the band chip above it.
- **Close-date diff.** The old date uses the same format as the new one (`from_display`).
- **Receipts.** The summary no longer repeats the title ("Done. Done.").
- **Error text.** Errors in a view show the message plus what to do next, the same words the voice speaks.
- **Wording.** User-facing text uses US spelling throughout ("canceled", "Color theme").
- **Closed deals.** A closed deal "has no live health score".

## Consequences

- **Add-on redeploy.** The tool schemas changed, so the Alexa+ add-on must be redeployed (`alexa-ai deploy`) wherever it is registered.
- **Slower team posts.** Posts from approved actions arrive about 35 seconds after the approval instead of at once. Draft follow-ups are unaffected.
- **Clock skew.** Countdowns compare server times with the browser clock, as the expiry timer already did.
