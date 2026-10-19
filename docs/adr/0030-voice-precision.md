# ADR-030: Voice precision: a command must account for the whole sentence

- **Status:** Accepted (2026-10-19)
- **Refines:** ADR-027 (voice-only control), ADR-028 (hands-free conversation)

## Context

ADR-027 put every page action behind a spoken phrase. A page-by-page audit of what a person would
actually say (147 phrasings probed against the grammar, then the live stack driven by scripted
speech) found places where the page did the wrong thing confidently:

1. **Half a sentence ran.** "Sort by health and tell me about Kestrel" matched the sort rule on its
   prefix and dropped the second request.
2. **Words were taken for names.** "Call me back when the review is done" set the user's name to
   "back when the review is done".
3. **Places on the screen were unknown.** "Open the second one" and "pin the first deal" had no
   meaning, and "dig into this" with a deal sheet open looked up a company called "this" on the
   server, or reviewed the wrong deal when a stale one was remembered.
4. **Everyday phrasings missed.** A bare "pipeline" or "orders", "clear filters", "reset the board",
   "only at risk", "too bright", "too fast", "try another voice".
5. **Help was generic.** "What can I say?" listed everything, not what works on the page in front of
   the person.

## Decision

**One rule for the grammar: a command consumes the whole utterance with a closed grammar.** Anything
left over means "not a command", and the sentence falls to the compound splitter (several clean
commands joined by "and" or "then") or, failing that, to the assistant whole. A second request is
never silently dropped. In practice:

- **Sort** accepts "sort by <key>" only when the whole remainder is a key (urgency, health, amount,
  name and their synonyms), plus "<most urgent | largest | sickest | alphabetical> first".
- **Names** go through `plausibleName`, which rejects clause words ("back", "when", "if") and
  anything that reads as a sentence.
- **Remove a rule** returns nothing when the target contains "and / then / also / but / plus", so
  "remove the first rule and add one for deals over a hundred thousand" goes to the assistant and
  no removal question opens.
- **Bare page names** navigate: pipeline, orders / standing orders, activity, guide (and "home"
  already did). "Watch", "start", "deals", "rules" and "overview" are deliberately left to the
  assistant because they are too often the start of a question.
- **Reset** words ("clear / reset / remove the filters", "reset the board") restore all bands and the
  default segment. "Only" is accepted after a band or segment and before a limit.
- **Complaints** map to settings: "too bright" / "too dark" set the theme; "too fast" / "too slow"
  set the speed.

**Places on the board, from the page's own ids.** The page sends `board` (deal ids in on-screen
order) in its context. `dealAt` resolves "the first / second / third / fourth / fifth / top / last
one, deal or account" and "number three" in the browser for open and pin. The offline router does
the same from the server's copy of the screen (`screen_deal`). A place with nothing in it says so
("There isn't a deal in that place on the board") instead of looking up a company called "third
one". With a deal sheet open, "this" means that deal, with no name lookup.

**Help is about the page you are on.** `helpText(page)` returns the commands for Watch, Pipeline,
Orders, Activity or Guide; "what can I say here / on this page" and "what can you do here" ask for
it, and plain "what can I say" keeps the overall list.

## Evidence

- A grammar probe of 147 phrasings: 140 behave as intended; the seven differences are deliberate
  (the bare words above, "more detail please" as a follow-up question, "turn off the alerts" being a
  write that needs the assistant and a yes).
- Unit tests: nine in `test/precision.test.mjs` (sort whole-phrase, compounds, plausible names, the
  remove-rule guard, bare pages, board filters, complaints, board places, per-page help) and one API
  test for the offline router reading "this deal" and board places from the screen.
- End to end on the live stack (real page, real server, real offline engine, scripted speech through
  hands-free): every check passes, including both halves of a compound, the whole sentence going
  to the assistant when only part is a command, "the second one" opening the second card on screen,
  "dig into this" reviewing the open deal without a name lookup, the unprompted announcement of the
  finished review, pin and unpin by place, and no console errors. One earlier failure was found to
  be the old server code still running after an edit (`uvicorn --reload` does not reliably reload
  Python on Windows), which also reproduced the original bug; restarting the stack fixed it.

## Considered and not adopted

- **`maxAlternatives` / n-best re-ranking against the grammar.** It is in the spec and Chrome
  accepts it, but how many alternatives or what confidence a given browser actually returns is not
  something we could verify, so a design that depends on it could not be tested here.
- **Speech-recognition phrase biasing** (`SpeechRecognitionPhrase`). Experimental and on-device
  only; not available in the browsers the page targets.
- **Fuzzy matching of commands.** It trades the "never run half a sentence" guarantee for recall.
  The closed grammar plus the assistant as the fallback covers the gap without risk to the data.

## Not verified

- **The model path, in part.** One live run against Gemini (`gemini-flash-lite-latest`, scripted
  speech, real page and server) read back a standing order, saved it only after a spoken yes, and
  removed it again by place; a preference change and a declined change were also read back and held
  until a yes or no. That run found a real bug: after the assistant saved a rule, the Orders page kept
  its old list, so "remove the first rule" answered "you don't have any standing orders". The page
  now reloads after a saved or removed rule, and the re-run passed. Logging a deal update saves
  immediately by design (only date and amount changes wait for a yes), and undo covers confirmed
  actions only, so a logged note cannot be taken back by voice. One run, one model: not a measure of
  how often a model will pick the right tool. The offline router cannot write at all (it briefs,
  lists, looks up, explains, starts and reads a review, confirms a preview and undoes).
- **A real recogniser.** Speech was scripted. Accuracy on real voices, accents and noise is
  unmeasured.

## Consequences

- Phrases that used to half-work now either work completely or are handed whole to the assistant,
  which costs a model call in the second case.
- The grammar is larger; the guide page and per-page help are generated from the same lists, and a
  test keeps the page actions and the server's `ACTIONS` equal.
