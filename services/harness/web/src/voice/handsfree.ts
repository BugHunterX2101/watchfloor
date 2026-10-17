// Hands-free listening: the microphone stays open and the page decides, utterance by utterance,
// whether the words were meant for it. It works the way a smart speaker does:
//
//   waiting   the wake word ("Watchfloor") has not been heard; nothing else is acted on
//   awake     listening for a request: after the wake word, and for as long as a conversation goes on
//             (CONVERSATION_MS after each reply, QUESTION_MS when the assistant asked something), so a
//             follow-up needs no wake word and nobody has to tap the microphone between turns
//   busy      a request is being worked on
//   speaking  the assistant is talking. "Stop" is heard, and so is a talk-over: the wake word, or a
//             clear request that is not the assistant's own voice coming back in through the microphone
//
// "That's all" (rest) ends the conversation early. It turns itself off after five quiet minutes, and
// everything it needs from the outside (the
// recogniser, the clock, what to do with a request) is handed in, so `npm test` drives it with a
// fake clock and a fake recogniser. Pure: no DOM, no storage.

import { hasWake, stripWake } from "./grammar.ts";
import { isEcho, norm } from "./text.ts";

export type HandsFreeState = "off" | "waiting" | "awake" | "busy" | "speaking";

export const SETTLE_MS = 850; // after the last words: more may follow in the same breath
export const WAKE_WINDOW_MS = 12_000; // after the wake word alone: time to say the request
export const CONVERSATION_MS = 45_000; // after a reply: the conversation goes on without the wake word
export const QUESTION_MS = 90_000; // ...and for longer when the assistant asked something and awaits an answer
export const ECHO_TAIL_MS = 3000; // after the assistant stops: its own last words may still arrive
export const IDLE_OFF_MS = 5 * 60_000; // no request for this long and hands-free switches itself off
export const SPEAKING_LIMIT_MS = 90_000; // a safety net: speech that never reports its end
const IDLE_CHECK_MS = 30_000;
const MAX_UTTERANCE = 500; // the API's own limit on a message
// Talking over the assistant: without the wake word it takes a real sentence that shares few words with
// what is being said, so a fragment of the assistant's own voice never cuts it off.
const TALK_OVER_WORDS = 3;
const TALK_OVER_OVERLAP = 0.34;
const SKIP_SPOKE_MS = 1500; // the cut-off speech reports its end this soon: that end is not a reply finishing

export interface Clock {
  now(): number;
  setTimer(fn: () => void, ms: number): unknown;
  clearTimer(id: unknown): void;
}

export interface EarsHandlers {
  onInterim(text: string): void;
  onFinal(text: string): void;
  onFatal(reason: string): void;
}

export interface Ears {
  start(handlers: EarsHandlers): void;
  stop(): void;
}

export interface HandsFreeDeps {
  ears: Ears;
  clock: Clock;
  /** Whether a request must start with the wake word (outside a follow-up window). */
  wakeRequired(): boolean;
  /** The page has just asked a yes-or-no question: a short answer is then a reply, never an echo of the
   *  question ("Say yes or no" contains both words). */
  answering?(): boolean;
  /** A request to act on, wake word included if it had one. The controller is busy until done() or speaking().
   *  `unheard`: it was said while the previous request was still being worked on, so the user had not yet
   *  heard that reply (it can change their ask, but it cannot be a yes to something they have not heard). */
  onUtterance(text: string, opts?: { unheard?: boolean }): void;
  /** Whether words said while the assistant is working may be held for afterwards. A bare yes or no may not:
   *  it would answer a question the user has not heard. Default: anything of two words or more. */
  canQueue?(text: string): boolean;
  onState(state: HandsFreeState, hint: string): void;
  /** What is being heard now, for the dock (only what is meant for the page). */
  onHeard(text: string): void;
  /** The wake word, alone. */
  onWake(): void;
  /** "Stop", said while the assistant was speaking or working. */
  onStopWord(): void;
  /** The user spoke over the assistant: its voice must stop now (what they said is handled as a request). */
  onTalkOver(): void;
  /** Switched itself off after sitting idle. */
  onAutoOff(): void;
  /** The microphone cannot be used (blocked, or no device). */
  onFatal(reason: string): void;
}

const STOP_WORDS = new Set([
  "stop",
  "cancel",
  "quiet",
  "enough",
  "silence",
  "shush",
  "hush",
  "be quiet",
  "shut up",
  "stop talking",
  "stop speaking",
  "thats enough",
  "pause",
  "never mind",
  "nevermind",
  "okay stop",
  "ok stop",
]);

/** Whether the words are just "stop" (with or without the wake word). */
export function isStopWord(text: string): boolean {
  return STOP_WORDS.has(norm(stripWake(text).text));
}

/** The share of the heard words (three letters or more) that the assistant was itself saying. */
export function overlap(heard: string, spoken: string): number {
  const said = new Set(norm(spoken).split(" "));
  const words = norm(heard)
    .split(" ")
    .filter((w) => w.length > 2);
  return words.length ? words.filter((w) => said.has(w)).length / words.length : 1;
}

/** Words heard while the assistant speaks that are meant for it: the request (empty for the wake word
 *  alone), or null when they are the assistant's own voice, a fragment, or not clearly a request. */
export function talkOver(heard: string, spoken: string): { text: string } | null {
  if (isEcho(heard, spoken)) return null;
  const { text, woke } = stripWake(heard);
  if (woke) return { text }; // the wake word is a deliberate call: it needs no more evidence
  const words = norm(text).split(" ").filter(Boolean);
  if (words.length < TALK_OVER_WORDS || overlap(text, spoken) >= TALK_OVER_OVERLAP) return null;
  return { text };
}

const HINT: Record<HandsFreeState, string> = {
  off: "",
  waiting: "Say “Watchfloor” to start",
  awake: "Listening…",
  busy: "Working on it…",
  speaking: "Speaking · talk over me, or say “stop”",
};

export interface HandsFree {
  start(): void;
  stop(): void;
  state(): HandsFreeState;
  /** The assistant has begun to speak `text`. */
  speaking(text: string): void;
  /** The assistant said `text` in passing (a progress remark while working): its echo is not the user. */
  noteSpoken(text: string): void;
  /** The assistant has finished (or been cut off): a follow-up may come. `asked`: it ended on a question. */
  spoke(asked?: boolean): void;
  /** The request is finished and nothing is being said (read-aloud off, or an error). */
  done(asked?: boolean): void;
  /** "That's all": once the reply to it is out, the conversation is over and the wake word is needed
   *  again. False (nothing changes) where there is no wake word to go back to. */
  rest(): boolean;
  /** Stop the microphone for now (the tab is hidden) without ending the session. */
  pause(): void;
  resume(): void;
  /** The wake-word setting changed. */
  refresh(): void;
  /** The user tapped the microphone: the same as saying the wake word. */
  wake(): void;
}

export function createHandsFree(d: HandsFreeDeps): HandsFree {
  let state: HandsFreeState = "off";
  let buffer = "";
  let spoken = "";
  let spokeAt = Number.NEGATIVE_INFINITY;
  let lastActive = 0;
  let paused = false;
  let queued = ""; // said while a request was being worked on: taken up when that one is over
  let resting = false; // "that's all" was said: its acknowledgement ends the conversation
  let fresh = false; // just switched on, nothing asked yet: its own "Hands-free is on" opens no conversation
  let skipSpokeUntil = Number.NEGATIVE_INFINITY;
  let settleTimer: unknown = null;
  let windowTimer: unknown = null;
  let idleTimer: unknown = null;
  let limitTimer: unknown = null;

  const clear = (id: unknown): null => {
    if (id !== null) d.clock.clearTimer(id);
    return null;
  };

  function set(next: HandsFreeState, hint?: string): void {
    state = next;
    d.onState(next, hint ?? HINT[next]);
  }

  function armWindow(ms: number): void {
    windowTimer = clear(windowTimer);
    windowTimer = d.clock.setTimer(() => {
      windowTimer = null;
      if (state === "awake" && d.wakeRequired()) set("waiting");
    }, ms);
  }

  function listening(window: number): void {
    // Awake for a while (the wake word or a reply just happened); without a wake word, awake for good.
    set("awake");
    if (d.wakeRequired()) armWindow(window);
  }

  /** A turn is over (spoken, or silent): the conversation goes on for a while, or ends if "that's all" was said. */
  function afterTurn(asked: boolean): void {
    if (resting || fresh) {
      resting = fresh = false;
      queued = "";
      windowTimer = clear(windowTimer);
      set(d.wakeRequired() ? "waiting" : "awake");
      return;
    }
    if (queued) {
      // The user spoke up while that was being worked on: now it is done, take up what they said.
      const next = queued;
      queued = "";
      windowTimer = clear(windowTimer);
      set("busy");
      d.onUtterance(next, { unheard: true });
      return;
    }
    listening(asked ? QUESTION_MS : CONVERSATION_MS);
  }

  function flush(): void {
    settleTimer = null;
    const text = buffer.trim().slice(0, MAX_UTTERANCE);
    buffer = "";
    if (!text || state === "off") return;
    const { text: rest, woke } = stripWake(text);
    lastActive = d.clock.now();
    if (woke && !rest) {
      listening(WAKE_WINDOW_MS);
      d.onWake();
      return;
    }
    windowTimer = clear(windowTimer);
    resting = fresh = false;
    set("busy");
    d.onUtterance(text);
  }

  /** The user spoke over the assistant: its voice stops, and what they said is heard like any request. */
  function talkedOver(heard: string, request: string): void {
    const now = d.clock.now();
    fresh = false;
    skipSpokeUntil = now + SKIP_SPOKE_MS;
    spokeAt = now;
    lastActive = now;
    queued = "";
    limitTimer = clear(limitTimer);
    d.onTalkOver();
    if (!request) {
      listening(WAKE_WINDOW_MS); // "Watchfloor!" alone: it stopped, and now it listens
      d.onWake();
      return;
    }
    listening(CONVERSATION_MS);
    buffer = heard.slice(0, MAX_UTTERANCE);
    settleTimer = clear(settleTimer);
    settleTimer = d.clock.setTimer(flush, SETTLE_MS);
  }

  function armIdle(): void {
    idleTimer = clear(idleTimer);
    idleTimer = d.clock.setTimer(() => {
      idleTimer = null;
      if (state === "off") return;
      if (state !== "busy" && state !== "speaking" && d.clock.now() - lastActive >= IDLE_OFF_MS) {
        stop();
        d.onAutoOff();
        return;
      }
      armIdle();
    }, IDLE_CHECK_MS);
  }

  function stop(): void {
    if (state === "off") return;
    settleTimer = clear(settleTimer);
    windowTimer = clear(windowTimer);
    idleTimer = clear(idleTimer);
    limitTimer = clear(limitTimer);
    buffer = "";
    queued = "";
    resting = false;
    d.ears.stop();
    set("off");
  }

  const handlers: EarsHandlers = {
    onInterim(text) {
      if (state === "off" || state === "speaking" || state === "busy") return;
      if (state === "waiting" && !buffer && !hasWake(text)) return;
      d.onHeard(text);
    },
    onFinal(text) {
      const heard = text.trim();
      if (!heard || state === "off") return;
      if (state === "speaking" || state === "busy") {
        // "Stop" counts, but not when it is the assistant's own voice.
        if (isStopWord(heard) && !isEcho(heard, spoken)) {
          d.onStopWord();
          return;
        }
        // While it speaks, a clear request (or the wake word) cuts it off and is taken up at once.
        const over = state === "speaking" ? talkOver(heard, spoken) : null;
        if (over) {
          talkedOver(heard, over.text);
          return;
        }
        // Spoken up while it is still working: held, and taken up once that request is over. A bare
        // yes or no is not held (it would answer a question not yet heard), nor is the assistant's own
        // progress remark coming back in, nor a single word.
        if (state === "busy" && !isEcho(heard, spoken) && (d.canQueue?.(heard) ?? true)) {
          const words = norm(stripWake(heard).text).split(" ").filter(Boolean);
          if (words.length >= 2) {
            queued = heard.slice(0, MAX_UTTERANCE);
            d.onHeard(heard);
          }
        }
        return;
      }
      // The assistant's last words can reach the recogniser just after it falls silent.
      if (spoken && d.clock.now() - spokeAt < ECHO_TAIL_MS && isEcho(heard, spoken) && !d.answering?.()) return;
      if (state === "waiting" && !buffer && !hasWake(heard)) return; // not for the page
      buffer = `${buffer} ${heard}`.trim().slice(0, MAX_UTTERANCE);
      settleTimer = clear(settleTimer);
      settleTimer = d.clock.setTimer(flush, SETTLE_MS);
    },
    onFatal(reason) {
      stop();
      d.onFatal(reason);
    },
  };

  function spoke(asked = false): void {
    if (state === "off") return;
    if (d.clock.now() < skipSpokeUntil) {
      skipSpokeUntil = Number.NEGATIVE_INFINITY; // the speech that was just cut off reports its end: not a reply finishing
      return;
    }
    limitTimer = clear(limitTimer);
    spokeAt = d.clock.now();
    lastActive = spokeAt;
    afterTurn(asked);
  }

  return {
    start() {
      if (state !== "off") return;
      buffer = "";
      spoken = "";
      spokeAt = Number.NEGATIVE_INFINITY;
      paused = false;
      fresh = true;
      lastActive = d.clock.now();
      set(d.wakeRequired() ? "waiting" : "awake");
      armIdle();
      d.ears.start(handlers);
    },
    stop,
    state: () => state,
    noteSpoken(text) {
      if (state === "off") return;
      spoken = text;
      spokeAt = d.clock.now();
    },
    speaking(text) {
      if (state === "off") return;
      spoken = text;
      buffer = "";
      settleTimer = clear(settleTimer);
      windowTimer = clear(windowTimer);
      limitTimer = clear(limitTimer);
      limitTimer = d.clock.setTimer(() => {
        limitTimer = null;
        if (state === "speaking") spoke();
      }, SPEAKING_LIMIT_MS);
      set("speaking");
    },
    spoke,
    done(asked = false) {
      if (state === "off") return;
      limitTimer = clear(limitTimer);
      spoken = "";
      lastActive = d.clock.now();
      afterTurn(asked);
    },
    rest() {
      if (state === "off" || !d.wakeRequired()) return false;
      resting = true;
      return true;
    },
    pause() {
      if (state === "off" || paused) return;
      paused = true;
      d.ears.stop();
      d.onState(state, "Paused while this tab is hidden");
    },
    resume() {
      if (state === "off" || !paused) return;
      paused = false;
      d.onState(state, HINT[state]);
      d.ears.start(handlers);
    },
    refresh() {
      if (state === "waiting" && !d.wakeRequired()) set("awake");
      else if (state === "awake" && d.wakeRequired()) armWindow(CONVERSATION_MS);
    },
    wake() {
      if (state !== "waiting" && state !== "awake") return;
      fresh = false;
      lastActive = d.clock.now();
      listening(WAKE_WINDOW_MS);
      d.onWake();
    },
  };
}
