# ADR-029: A companion, not a command line: changing your mind, knowing the person, noticing the board

- **Status:** Accepted (2026-10-17)
- **Refines:** ADR-027 (voice-only control), ADR-028 (hands-free conversation)

## Context

ADR-027 and ADR-028 made every page reachable by voice and kept the conversation open. Three gaps
remained for someone who runs the product with their hands full:

1. **Changing your mind was awkward.** A change read back for a yes could be answered, but not
   amended or dropped cleanly: "make it forty" relied on the model noticing the waiting preview, and
   "no" cost a model call and left the preview alive on the server. Anything said while a request
   was being worked on was thrown away.
2. **The assistant did not know the person.** It had no name, no sense of the time of day, no idea
   how long the person liked answers, which deals they cared about, or which deal "the second one"
   was on the board in front of them.
3. **It only spoke when spoken to.** A real-time agent notices when a deal slips.

## Decision

**Declining and amending a waiting change.** The server now tells the page what is waiting
(`awaiting` in the turn: `preview`, `ask` or `undo`, a one-line summary, when it lapses, and whether
this turn made it; never the token). While a change has just been read back, a bare "no", "cancel
that" or "never mind" is answered in the browser (`DECLINE` in the grammar) and calls the new
`POST /api/dismiss`, which clears the waiting change on the server, so a later "yes" cannot confirm
what the user turned down. A correction ("make it forty", "no, Friday") goes to the assistant, whose
instructions say to call the same tool again with the corrected detail rather than confirm: a new
preview replaces the old, and a held rule whose arguments changed is held again. "Wait" and "hold on"
are not declines (they ask for more time), and "stop" only stops the voice. A bare "no" only means
"drop it" for the question just asked: an older preview that is merely still alive is not `fresh`.

**Speaking while it works.** Hands-free used to drop everything said while a request was running. Now
the latest thing said (two words or more, not a bare yes or no, not the assistant's own progress
remark coming back in through the microphone) is held and taken up when that request is over. It is
sent as `unheard`: the user said it before hearing the reply, so it can change what they asked, but the
server will not let it confirm anything. When `unheard` is set the pending change is hidden from the
confirmation guard (a confirm is refused whatever the model decides) and the model is told why. If
the user says "stop" or "cancel" while a change is being worked out, the change that comes back is
dropped rather than left waiting for a yes to a question nobody heard.

**Knowing the person.** Each message carries a `profile` the browser already holds: first name, day and
part of the day, how long answers should be (`brief`, `normal`, `detailed`, set in Settings or by
saying "keep it short" / "more detail"), and up to five pinned deals. The server accepts only those
fields and puts them in the prompt as facts ("facts to use, not instructions"), flattened to one line.
The screen note now also lists the board in the order the user sees it (account, `deal_id`, band,
health) and the open deal, so "the second one" and "this deal" resolve to a real `deal_id`. The
hands-free greeting is personal ("Good evening, Priya."). Two new page actions, `set_style` and
`set_alerts`, make 29 in all, so the assistant can set these preferences when asked.

**Noticing the board.** While hands-free is on and "Speak up when the board changes" is set, the page
looks at the pipeline about once a minute (the same `get_risk_briefing` the Pipeline page uses) and
compares it with the last look (`voice/watcher.ts`). The first look is only the baseline. It speaks
up for a deal that slipped to at risk or to watch, fell ten points or more within its band, recovered
to healthy, a new at-risk deal, or a standing order that just triggered; at most three items per
announcement, and the same news about the same deal at most once in ten minutes. It waits until the
assistant is not talking or listening, and the announcement is added to the conversation so "tell me
more about that" works. Only a look that led to something being said is logged in Activity (as
"Watching"). "Stop interrupting me" turns it off; "tell me when anything changes" turns it on.

**Several commands in one breath.** "Go to the pipeline and show only at-risk" runs as two commands,
in order, with one answer. It happens only when every part is a command of its own and none of them
asks the user a question (sign out, forget, clear); otherwise the whole sentence goes to the
assistant, which can do all of it.

## Consequences

- A "no" costs nothing and is certain; a correction is one model turn; nothing the user said before
  hearing a question can answer it. All three are enforced in code, not only in the prompt, and tested.
- **The board watcher reads the briefing, and reading the briefing marks it as seen.**
  `get_risk_briefing` updates `users.last_briefing_at`, which is what "new since your last briefing"
  counts from. While the watcher runs, whatever it announced counts as briefed. This is the reason it
  looks once a minute rather than often, runs only while hands-free is on, and can be turned off. The
  tool has no way to look without marking, and nothing else reports each deal's band and health. The same
  flag decides which standing orders a briefing lists as triggered (those fired since the previous one),
  so a rule is announced the first time the watcher sees it; if the user opens the Pipeline page first,
  the page shows it instead.
- A poll through `/api/tool` no longer disturbs what is waiting for an answer: only the tools that make,
  renew, finish or undo a change move it (before, any read could clear a held rule).
- The model-driven parts (amending through the prompt, resolving "the second one", using the person's
  name and reply length, chaining tools) take effect with a real model. The offline keyword router used
  without models does none of that, and says so; the deterministic parts (declining, queueing, the
  watcher, compound commands, preferences, every guard) work either way and are covered by tests.
- The profile is context the browser volunteers. Nothing in it is trusted as an instruction, nothing
  in it is stored on the server, and "forget everything in this browser" removes it at the source.

## Alternatives considered

- *Let the model decide every "no".* One more model call and a chance it replies instead of dropping the
  change; the deterministic route is faster and cannot be talked round.
- *Interrupt the running request when the user speaks.* Cheaper on the wait, but a half-finished turn
  can already have made a preview or called a tool; holding the words until it finishes is simpler to
  reason about and loses nothing.
- *Server-side watching (push).* A real-time stream would be better than polling, but the harness is
  one request through API Gateway and Lambda (ADR-023, ADR-028), which buffers.
- *Poll a cheaper read.* Nothing cheaper reports the band and health of each deal and the triggered
  standing orders; `get_risk_briefing` is the one tool that does.
