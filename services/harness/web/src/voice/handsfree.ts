// Hands-free listening: the microphone stays open and the page decides, utterance by utterance,
// whether the words were meant for it. It works the way a smart speaker does:
//
//   waiting   the wake word ("Watchfloor") has not been heard; nothing else is acted on
//   awake     listening for a request (after the wake word, or for a few seconds after a reply, so a
//             follow-up needs no wake word: Alexa's "follow-up mode")
//   busy      a request is being worked on
//   speaking  the assistant is talking; only "stop" is heard, and only if it is not the assistant's
//             own voice coming back in through the microphone
//
// It turns itself off after five quiet minutes, and everything it needs from the outside (the
// recogniser, the clock, what to do with a request) is handed in, so `npm test` drives it with a
// fake clock and a fake recogniser. Pure: no DOM, no storage.

import { hasWake, stripWake } from "./grammar.ts";
import { isEcho, norm } from "./text.ts";

export type HandsFreeState = "off" | "waiting" | "awake" | "busy" | "speaking";

export const SETTLE_MS = 850; // after the last words: more may follow in the same breath
export const WAKE_WINDOW_MS = 8000; // after the wake word alone: time to say the request
export const FOLLOW_UP_MS = 8000; // after a reply: a follow-up needs no wake word
export const ECHO_TAIL_MS = 3000; // after the assistant stops: its own last words may still arrive
export const IDLE_OFF_MS = 5 * 60_000; // no request for this long and hands-free switches itself off
export const SPEAKING_LIMIT_MS = 90_000; // a safety net: speech that never reports its end
const IDLE_CHECK_MS = 30_000;
const MAX_UTTERANCE = 500; // the API's own limit on a message

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
  /** A request to act on, wake word included if it had one. The controller is busy until done() or speaking(). */
  onUtterance(text: string): void;
  onState(state: HandsFreeState, hint: string): void;
  /** What is being heard now, for the dock (only what is meant for the page). */
  onHeard(text: string): void;
  /** The wake word, alone. */
  onWake(): void;
  /** "Stop", said while the assistant was speaking or working. */
  onStopWord(): void;
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

const HINT: Record<HandsFreeState, string> = {
  off: "",
  waiting: "Say “Watchfloor” to start",
  awake: "Listening…",
  busy: "Working on it…",
  speaking: "Speaking · say “stop” to interrupt",
};

export interface HandsFree {
  start(): void;
  stop(): void;
  state(): HandsFreeState;
  /** The assistant has begun to speak `text`. */
  speaking(text: string): void;
  /** The assistant has finished (or been cut off): a follow-up may come. */
  spoke(): void;
  /** The request is finished and nothing is being said (read-aloud off, or an error). */
  done(): void;
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
    set("busy");
    d.onUtterance(text);
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
        // Only "stop" counts, and not when it is the assistant's own voice.
        if (isStopWord(heard) && !isEcho(heard, spoken)) d.onStopWord();
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

  function spoke(): void {
    if (state === "off") return;
    limitTimer = clear(limitTimer);
    spokeAt = d.clock.now();
    lastActive = spokeAt;
    listening(FOLLOW_UP_MS);
  }

  return {
    start() {
      if (state !== "off") return;
      buffer = "";
      spoken = "";
      spokeAt = Number.NEGATIVE_INFINITY;
      paused = false;
      lastActive = d.clock.now();
      set(d.wakeRequired() ? "waiting" : "awake");
      armIdle();
      d.ears.start(handlers);
    },
    stop,
    state: () => state,
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
    done() {
      if (state === "off") return;
      limitTimer = clear(limitTimer);
      spoken = "";
      lastActive = d.clock.now();
      listening(FOLLOW_UP_MS);
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
      else if (state === "awake" && d.wakeRequired()) armWindow(FOLLOW_UP_MS);
    },
    wake() {
      if (state !== "waiting" && state !== "awake") return;
      lastActive = d.clock.now();
      listening(WAKE_WINDOW_MS);
      d.onWake();
    },
  };
}
