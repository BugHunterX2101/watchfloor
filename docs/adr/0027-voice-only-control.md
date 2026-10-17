# ADR-027: Voice-only control: hands-free listening, spoken commands, and page actions for the assistant

- **Status:** Accepted (2026-10-17)
- **Refines:** ADR-003 (confirmation), ADR-018 (interaction details), ADR-026 (harness pages)

## Context

The harness could be talked to, but not run by voice. The microphone was push-to-talk and took one
utterance; the assistant could call Watchfloor's tools but not touch the page; and four of the five
pages, the settings and the sign-out had no spoken way in. Someone with their hands full, or no
screen in front of them, could ask a question and nothing else.

A voice assistant that is meant to be used without looking has three jobs the old design left open:
hear only what is meant for it, do what it is told on every page, and never do something drastic
because it misheard.

## Decision

**Hands-free mode** keeps the microphone open and acts like a smart speaker (`web/src/voice/handsfree.ts`).
Nothing is acted on until the wake word, "Watchfloor", is heard (recognition hears it as one word or
two; "watch for" is deliberately not a wake word, because it opens real requests). After the wake word
there are eight seconds to say the request; after a reply there are eight seconds for a follow-up with
no wake word (Alexa's follow-up mode). While the assistant is talking only "stop" is heard, and not if
the words are the assistant's own voice coming back through the microphone (`isEcho`, a fuzzy match
against what is being said, held for three seconds after it stops). It switches itself off after five
minutes without a request, pauses while the tab is hidden, and says why if the microphone is blocked.
Everything it depends on (the recogniser, the clock, what to do with a request) is injected, so the
state machine is tested with a fake clock in Node. The page shows the state on every page in a dock,
in words, with "Talk" and "Turn off".

**Two ways to act, one set of actions.** A fixed list of 27 page actions (`navigate`, `set_theme`,
`pipeline_view`, `open_deal`, `pin_deal`, `activity_view`, `read_page`, `sign_out`, and so on;
`web/src/voice/actions.ts`) is the whole vocabulary.

1. *A spoken-command grammar* (`voice/grammar.ts`) turns an utterance that is exactly a command into
   an action, in the browser, with no model and no network: "go to orders", "show only at-risk deals",
   "sort by amount", "open Kestrel", "dark mode", "speak slower", "remove the first rule", "stop". It is
   strict on purpose: the whole utterance must be the command, so "show me why Kestrel is at risk" is
   a question for the assistant. Deal and order names are matched the way speech recognition spells
   them (a phonetic fold, so "kestral" meets "Kestrel"), and two equally good matches are asked about,
   never guessed. The same grammar reads typed text.
2. *`control_app`*, a tool the assistant calls for everything else ("show me Harbor and Pine and tell
   me what's wrong"). It does not touch the page: the harness checks the request against the same
   list (`harness_api/control.py`), returns it in the turn's `actions`, and the browser checks it
   again before running it. A test keeps the two lists equal.

Every message also carries the page's own account of itself (`context`: the page, theme, whether
hands-free is on, which dialog is open, what the board is showing), as fixed words only, so "this
page" and "that one" mean something. The server rejects anything else.

**Asking before drastic things.**

- `sign_out`, `forget_data` and `clear_activity` are put to the user as the page's own confirmation
  dialog and out loud ("Sign out of Watchfloor? Say yes or no."). A spoken yes presses the button a
  tap would have pressed. When the assistant asks for one, the question replaces its reply.
- Removing a standing order by voice finds the order by position or by its words and then opens the
  same confirmation the Remove button opens.
- The three tools that save at once on the server, `set_standing_order`, `remove_standing_order` and
  `update_briefing_preferences`, are held by code until the user has been asked and has answered in a
  later turn (the same discipline `confirm_action` has). A misheard "fifteen" for "fifty" would
  otherwise have been saved as heard. The held write rides in the encrypted pending cookie with a
  fingerprint of exactly its arguments, is good for one turn and one use, and a changed argument is
  held again.

**A better-informed assistant.** The system prompt now says how to decide: take the obvious next step
without asking, chain tools, recommend with the tools' own reasons, resolve "it" and "the first one"
from the conversation, expect misheard names and numbers, speak for ears (no "tap", no ids), and use
`control_app` for the page. Replies are made easy to hear before they are spoken (`speakable`: "$1.2M"
as "1.2 million dollars", ids and links dropped). The offline router answers "where do I start?" with
the top deal.

**What it does not do.** Typing a password to sign in needs the keyboard; sign-in leaves the page,
so hands-free is switched on again afterwards (browsers also need one tap before a page may speak).
Speech recognition is the browser's: in Chrome and Edge the microphone audio is processed by the
browser vendor's speech service while hands-free is on. The Guide and the settings dialog say so;
Watchfloor itself acts only on requests that start with the wake word (or follow a reply) and sends
nothing else anywhere. It needs Chrome or Edge, and stops when the tab is hidden.

## Consequences

- Nothing in the page is out of reach of the voice, and the commonest commands cost no model call.
- Reply speed is the model's for questions and instant for commands.
- About 16 KB gzip more JavaScript for the voice layer on top of ADR-026's page (about 104 KB in all).
- The two places that list the actions must change together; a test fails if they drift.
- Saving a standing order now takes two turns ("Should I go ahead?", then yes). That is the point.

## Alternatives considered

- *A wake-word engine in the page* (an on-device keyword spotter). Better privacy, no cloud
  recogniser while waiting, but a model file and a library to ship, and nothing to say it is better
  than the browser's recogniser at "Watchfloor". Left for a native client.
- *No wake word, always acting.* Simplest, and what turning off "Only act when I say Watchfloor" gives
  you; the wake word is on by default because a room's conversation is not a request.
- *Let the model do all control.* Slower, costs a call per command, does not work offline, and puts
  every "dark mode" at the mercy of a model that might call the wrong tool.
