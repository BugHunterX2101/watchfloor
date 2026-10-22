# ADR-033: Hearing the recogniser's runner-up guesses, and Amazon Polly's voice

- **Status:** Accepted (2026-10-22)
- **Refines:** ADR-027 (voice-only control), ADR-030 (voice precision), ADR-031 (learning from corrections)

## Context

An audit of the page's voice path found three ways it lost what the user said, and one way it sounded
worse than it had to:

1. **Only the recogniser's first guess was read.** Chrome and Edge return several transcripts for one
   utterance, best first, but the page asked for one. "Open castle bio" for "open Kestrel Biotech", or
   "watch for" for "Watchfloor", went to the model as written, even when the second guess was right.
2. **Everyday phrasings missed the grammar.** "Take a fresh look", "turn the sound off", "slow down",
   "keep it shorter", "show me only the errors" and similar went to the model, which is slower and
   needs quota, although each is a page command.
3. **Questions could look like changes.** On the offline engine a question that mentioned a
   preference ("can you brief me on fewer deals?") could match the change patterns.
4. **The browser's voice.** `speechSynthesis` voices differ by machine and are often robotic. The
   account has Amazon Polly, whose neural voices sound natural and return the timing of each word.

## Decision

**The page asks for four guesses and chooses among them with care** (`pickHeard`, `grammar.ts`). The
first guess stands when it is already a command. Otherwise a runner-up wins only when it is nearly the
same words (similarity at least 0.6) and is a harmless page command: never anything that asks the user
a question, and never clearing the conversation, hands-free, the wake word or a name. "Clean" heard as
"clear" must not wipe anything. Failing that, a runner-up that names a deal on the page is preferred
over a first guess that names none. The choice is wrapped so a recogniser that returns something odd
leaves the first guess in place. `SpeechRecognition.phrases` (contextual biasing) is not used: it is
experimental, and support in the cloud recognisers that Chrome and Edge use is unconfirmed.

**The grammar covers the everyday phrasings** above (wake word on and off, a fresh look, sound and
voice on and off, slower and faster, shorter and longer answers, the activity filters). The offline
engine's `ASKING` pattern makes a question skip the change patterns.

**Replies are read by Amazon Polly when the server offers it** (`voice.py`, `POST /api/speak`). It is
opt-in with `WF_POLLY_VOICE` (a neural voice id, such as `Ruth`). Without it, `/api/session` says
`"voice": null` and the browser speaks as before. The neural engine is used, not generative, because
it returns word speech marks, which keep the captions and the lamp in step with the voice. Polly gives
those marks as UTF-8 byte offsets; the server converts them to the page's UTF-16 offsets.

What the endpoint can spend is bounded, because the page is public:

- a signed-in session is required;
- a reply is at most 1,500 characters;
- one person may ask 20 times a minute;
- the process spends at most `WF_POLLY_DAILY_CHARS` (default 30,000) characters a UTC day. Audio and
  marks are two billed requests, so a reply counts twice. That is about 0.9 million a month, inside
  the neural free tier of one million.

Any refusal or failure (404 no voice, 401, 400, 429, 503) makes the page speak with the browser's voice
instead; 401 and 404 also stop it asking again. The page waits at most 6 seconds for the audio. The
client is built when the server starts, so the first reply does not also pay for importing boto3.
Choosing a browser voice in Settings always uses that voice, and "change voice" cycles through
"the default voice" (Polly where offered) and the browser's voices. The page's CSP allows
`media-src 'self' blob:`, and nothing else new.

## Evidence

- `test/voice.test.mjs`: the everyday phrasings, misheard deal names chosen from a runner-up, and
  `pickHeard` never swapping in a confirmation, a clear or a sign-out, nor a different question.
- `test_voice.py`: audio with its words timed; marks as UTF-16 offsets for text with curly quotes; a
  session and a sensible request are required; no voice configured is a 404; Polly failing is a 503;
  the per-minute limit; the shared daily allowance counting audio and marks; the voice on only when a
  voice id is named; the CSP allowing blob audio.
- Live, against account 162599956323 (2026-10-22, local server, `Ruth`): `/api/speak` returned 200
  with a 33,596-byte MP3 and word marks that landed on the page's words, in time order. That first
  call took 4.5 s; the 3 s wait the page had then was raised to 6 s, and the client is now built at
  start-up.

## Not verified

- **Warm latency.** Only one cold call was timed. A warm reply should be well under the 6 s wait;
  if it is not, the page falls back to the browser's voice, which is the old behaviour.
- **Real recognisers' runner-up guesses.** `pickHeard` was tested with scripted alternatives. How
  often Chrome or Edge actually puts the right words second is measured only by live use.
- **Autoplay.** A browser that blocks audio without a gesture makes the page fall back; this was not
  tried on every browser.

## Consequences

- One more AWS service (Polly) and one more endpoint, both off unless `WF_POLLY_VOICE` is set.
- The deployment's IAM user needs `polly:SynthesizeSpeech` only.
- Polly costs nothing inside the free tier and the daily cap; past the cap the browser speaks.
