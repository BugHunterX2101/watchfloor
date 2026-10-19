# ADR-031: Facts from the server, read-backs from code, and learning from corrections

- **Status:** Accepted (2026-10-19)
- **Refines:** ADR-029 (voice companion), ADR-030 (voice precision)

## Context

A conversation-level audit of the model path (Gemini, scripted speech, real page and server) checked
every spoken fact against the tool data of the same turn. The tools were right; the conversation
around them could still go wrong:

1. **Arithmetic by the model.** "How much is at risk?" was answered by adding up the deals the briefing
   happened to list (three of them), not every at-risk deal.
2. **No comparison data.** "Better or worse than last week?" had nothing to compare with, so the
   honest answer was a guess.
3. **Follow-ups from memory.** "Who owns it?" and "when do they decide?" were answered from earlier
   replies. History carries text only, so a detail could drift between turns; `lookup_deal` did not
   return the close date or next step at all.
4. **A paraphrased read-back.** For a held standing order the user heard the model's wording of the
   rule. A paraphrase can differ from the arguments a "yes" would save.
5. **"No" left the change waiting.** A plain "no, never mind" depended on the model dropping the
   held change; if it did not, a later "yes" could still save it.
6. **The same mishearing, every time.** Correcting "castle bio" to Kestrel Biotech fixed one turn;
   the next time the recogniser heard it the same way, the user had to correct it again.

A live run with these fixes in place (Gemini flash-lite, 15 scripted lines) found two more:

7. **A number nobody said.** "Only brief me on deals over a hundred thousand" became a held change
   to a briefing limit of 5, although the prompt says briefings can't be limited by amount. The
   exact read-back exposed it, but only if the user listened closely.
8. **Learning from a guess.** On "How is castle bio doing?" the model took castle bio as Kestrel
   Biotech on its own and called `remember`, before the user had corrected anything.

## Decision

**Totals and comparisons are computed on the server.** `get_risk_briefing` returns `at_risk_count`,
`at_risk_amount_display` and `watch_amount_display` over every open deal in scope (not only the
listed ones), and `week_ago` (`day`, `at_risk`, `attention`) from the daily snapshot seven days back.
No snapshot that day means no `week_ago`, and the assistant says it cannot compare; a segment with
nothing in it that day counts as zero, not missing. Amounts appear only when the user's preferences
include amounts.

**A lookup carries what follow-ups ask.** `lookup_deal` candidates include `close_date_display` and
`next_step`, and a single match says when it is expected to close. The prompt requires every fact
about a deal to come from a tool result in the same turn, and names the tool for each follow-up.

**The question before a held write is built in code.** `spoken_write` turns the exact held arguments
into the sentence the user hears ("To be exact, I'd save a standing order that flags deals of at
least $100,000 … Should I save it?"), replacing the model's wording. A removal is named only when the
rule was listed in the same turn; otherwise the model's question stands.

**A plain no drops the held change in code.** `declines` recognises "no", "never mind", "cancel that",
"forget it", "no, never mind, leave it" and similar, as the whole message. A correction ("no, make it
forty") or a new request is not a decline and goes to the model as before. The page's grammar accepts
the same chained phrases.

**Corrections are learned, narrowly.** When the user corrects a name or phrase, the model calls
`control_app remember {heard, meant}`. The page keeps up to eight lessons in this browser, rewrites
the user's words with them before the grammar or the model reads them (and says so in the
transcript), and sends them in the profile as quoted facts. Only names and short phrases qualify:
never a number in digits or words, a yes or no, a cancel or the wake word, because those are read
back instead. Whole words only, longest first, and a match already equal to the right name is left
alone. Lessons are listed in Settings, each removable, and "forget everything" clears them. The same
rules are checked in `lessons.ts` and `control.py`, and a lesson the page could not have learned is
dropped from the prompt.

**A held write may use only numbers the user said.** Before a standing order or a briefing change
is held, `unsaid_numbers` checks its `briefing_limit`, `quiet_days`, `min_amount_usd` and
`min_score` (or 100 minus it, a health) against the numbers in the user's message and their last
four messages, in digits or words ("a hundred thousand", "$50K", "two weeks"). The assistant's own
replies don't count. A number nobody said is refused before anything is held, and the model is told
to say what the tool can't do or ask for the number. The confirming call after a yes is not checked
again: its arguments are the ones the user heard.

**A lesson is learned only from a correction.** `may_learn` accepts `remember` only when the user's
message corrects ("no", "not", "I said", "I meant", "actually") or says yes to a "did you mean"
question, and only when the misheard words appear in what the user said. A name the model took on
its own is said in the reply ("Taking that as Kestrel Biotech"), so the user can put it right.

**Every number and date spoken is checked against this turn.** A later live run showed the model
answering from memory: "$136K across all deals on watch and at risk" with no tool call (that sum is
the at-risk deals only), "health is 28, quiet for fourteen days" after a failed lookup (the tools say
29 and 16), "closing next Tuesday" for December 1, and "twenty on watch" for a snapshot whose 20 counts
at-risk and on-watch deals together. `Grounds` collects every number, month and weekday in this turn's
tool results and arguments, what the code told the model, the screen, the profile and the user's own
last messages (never the assistant's earlier replies). A reply that states a number above one, a
month, a weekday or a relative date ("next Tuesday") none of them contain is sent back once with
the list ("call the tools you need and answer again"); what is still unsupported after that is cut
sentence by sentence, and a reply with nothing left says it could not check rather than guess. No
second look after a write, so nothing is done twice. The week-over-week answer is computed on the
server: `week_ago.comparison` says both counts, now and then, and which way each moved ("Mixed: more
deals are at risk, but fewer need attention overall"), and `week_ago.watch` is the on-watch count.

**Greetings and offers are fixed in code.** The same run showed "Good morning" answered with "Good
afternoon" and one email offer made three turns running, against the prompt. `tidy_reply` puts the
user's own greeting back, and drops a closing "want me to …?" that matches one of the last three
replies (same verb, a shared word). It touches only the model's words: never a read-back, never a
question while a change waits for an answer, and never a reply that is only the offer.

## Evidence

- Unit tests: `test/lessons.test.mjs` (what may be learned, rewriting, retirement and the cap, the
  page action, the profile, decline phrasings); `test_control.py` (lesson rules, the quoted
  profile note, exact read-backs, declines and non-declines); `test_converse.py` (the read-back
  replaces the model's words, a spoken no drops the held change so a later yes saves nothing, a
  correction is not a decline, lessons reach the prompt).
- Guards from the live run: `test_control.py` (numbers heard in digits and words, a held write
  only with numbers the user said, a lesson only from a correction, greeting back in kind and no
  repeated offer); `test_converse.py` (a write with an unsaid number is refused before anything is
  held; a name is learned from a correction, never from a guess).
- Live rerun (Gemini flash-lite, the same 15 lines, 2026-10-19): "over a hundred thousand" was read
  back as a standing order for $100,000, not a briefing limit; "castle bio" was answered as "Taking
  that as Kestrel Biotech" with nothing learned, then learned on "No, I said Kestrel" and applied on
  the next mention; totals, owner, close date, next step, the 10-to-14-day correction and both
  declines were right. Greeting and repeated offers were wrong in that run and are fixed above.
- Grounding: `test_control.py` checks the live-run sentences against the briefing they came with
  (the made-up 28, 14, "next Tuesday" and an unsaid "three" are caught; the true totals, 29, 16 days,
  "Tuesday, December 1" and advice such as "call them this week" pass); `test_converse.py` shows a
  guess sent back and answered from the briefing, a guess repeated after the check cut to the
  sentences that hold, and a reply using only the user's own numbers left alone.
- Integration tests against the seeded database: totals match a direct sum over every open deal
  with a one-deal briefing; `week_ago` matches the snapshot rows (at risk, both bands, on watch), its
  comparison quotes both counts now and then, and it disappears without a snapshot; a lookup says
  when Kestrel is expected to close.

## Not verified

- **How often a model calls `remember` on its own.** The tool, rules and prompt are tested; whether
  a given model chooses to call it after a correction is measured only by live runs.
- **Real recognisers.** Speech was scripted. Lessons help only with mishearings that repeat.
- **Every way of saying a number.** `numbers_said` reads digits, words, "K"/"million", halves and
  quarters ("half a million"), and weeks or months as days. A phrasing it misses ("three quarters of
  a million") is refused and the model asks again: a lost turn, never a wrong value saved.
- **Facts that are not numbers or dates.** A made-up reason ("the buyer asked about SSO") or a
  wrong stage carries no number for `Grounds` to catch; the prompt and the same-turn tool rule
  cover it, and only live runs measure how often a model breaks it.
- **Model compliance in general.** The code guards hold whatever the model does; how often a model
  follows the rest of the prompt is measured only by live runs, one model and one script so far.

## Consequences

- One more page action (`remember`), kept equal between the page and the server by the existing test.
- The briefing output grew by five fields; the tools snapshot was regenerated.
- A lesson can never change a number or an answer, so a wrong lesson costs at most a wrong name,
  which the user sees in the transcript and can remove in Settings.
