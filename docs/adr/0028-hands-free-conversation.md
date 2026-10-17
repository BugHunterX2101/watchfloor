# ADR-028: Hands-free is a conversation, not a series of requests

- **Status:** Accepted (2026-10-17)
- **Refines:** ADR-027 (voice-only control)
- **Refined by:** ADR-029 (speech while it works is held, not dropped; the board is watched; changes can be dropped or amended)

## Context

ADR-027 made the page usable by voice, but hands-free still behaved like a smart speaker's
single-shot mode. After a reply there were eight seconds to follow up; after that the wake word, or a
tap on the microphone, was needed again. The assistant could not be interrupted except by the word
"stop". A turn that took ten or twenty seconds was silent for someone who was not looking at the
screen, and a deep review (about twenty seconds) had to be asked for again once it was done. Someone
who turns hands-free on wants to talk to it, not to operate it.

## Decision

**The conversation stays open** (`voice/handsfree.ts`). After a reply the microphone stays in the
awake state for 45 seconds (`CONVERSATION_MS`), or 90 (`QUESTION_MS`) when the reply ended on a
question or left a yes-or-no on the screen (`asksSomething`). Every exchange restarts the window. When
it lapses the state goes back to *waiting* for the wake word; it does not switch hands-free off.
"That's all", "I'm done" and "nothing else" end the conversation at once (`rest`): the reply to them
is spoken, then it waits for the wake word. Saying goodbye or "stop listening" still ends hands-free
itself. The page's own "Hands-free is on" opens no conversation, so chatter right after switching on
is not taken for a request.

**It can be talked over.** While the assistant speaks, the wake word (alone: it stops and listens;
followed by a request: it stops and takes the request) or a clear request cuts it off. A request
without the wake word must be at least three words and share fewer than a third of its words with
what is being said (`talkOver`, `overlap`); a fragment or the assistant's own voice coming back in
through the speakers does not interrupt it. The speech that was cut off reports its end a moment
later; that end is not mistaken for a reply finishing.

**It says when it is still working.** In hands-free a turn that takes more than six seconds says
"Still working on that." once, and at fourteen "This one's taking a little longer." (neither contains a
stop word, so the microphone cannot take them for an interruption).

**It reports back by itself.** After a turn starts a deep review, the page checks for the result every
four seconds, for up to two minutes, through the same allow-listed `get_triage_result` call the views
use, and says the result once the assistant is not in the middle of something else. With hands-free
off the result is added to the transcript and shown as a toast, not spoken unasked. The announcement is
added to the conversation history, so "yes, set that up" refers to the review that was just read out.

**The assistant is told it is in a conversation** (`prompts/alexa_style.md`): what the user said last
wins over an earlier ask, several requests in one breath are all done in one turn, short answers are
read against the assistant's last message, fragments and side talk get "I didn't catch a request"
rather than a guess, and each reply may offer one next step. Nothing about the safety rules changes:
changes still need the user's spoken yes, and sign-out and forgetting data still ask first.

## Consequences

- Hands-free no longer needs a tap or a repeated wake word inside a conversation. The cost is that
  anything said in the room during an open window is taken as a request. Changes are never made on that
  alone (every write waits for a yes), and "that's all" closes the window.
- Talking over the assistant is deliberately conservative. On speakers an echo can still be
  mistaken for a request in rare cases; headphones remove the problem. The wake word always works.
- The model's judgement (what a request means, which tools to chain) is the assistant's, as before; the
  offline keyword router still answers when no model is available, and says so.
- Unit tests drive the whole state machine with a fake clock (`handsfree.test.mjs`); a browser script
  drives the same behaviours with scripted speech against the running stack.

## Alternatives considered

- *No window, no wake word, always acting.* What turning off "Only act when I say Watchfloor" already
  gives; the wake word remains the default because a room's conversation is not a request.
- *Interrupt on any speech while the assistant talks.* Natural in a headset, but on speakers the
  assistant would cut itself off with its own voice.
- *Stream the model's reply as it is written.* Would shorten the wait, but the reply path is a single
  request through API Gateway and Lambda, which buffers; spoken progress covers the wait instead.
