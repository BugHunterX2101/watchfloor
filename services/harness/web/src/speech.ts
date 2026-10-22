// Voice in (Web Speech API recognition, Chrome) and voice out (speechSynthesis). Both are optional:
// without them the harness is a text chat.

import { settings } from "./store";
import type { Ears, EarsHandlers } from "./voice/handsfree";

/** The voices this browser offers (empty until it has loaded them, or without speech synthesis). */
export function voices(): SpeechSynthesisVoice[] {
  return "speechSynthesis" in window ? window.speechSynthesis.getVoices() : [];
}

interface RecognitionResult {
  readonly isFinal: boolean;
  readonly length: number;
  readonly [index: number]: { readonly transcript: string };
}

interface RecognitionEvent {
  readonly resultIndex: number;
  readonly results: ArrayLike<RecognitionResult>;
}

interface Recognition {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  maxAlternatives: number;
  onresult: ((e: RecognitionEvent) => void) | null;
  onend: (() => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  start(): void;
  stop(): void;
}

type RecognitionCtor = new () => Recognition;

function recognitionCtor(): RecognitionCtor | null {
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export const canListen = (): boolean => recognitionCtor() !== null;

// The commands and the assistant work in English: the user's own English (en-IN, en-GB) when the
// browser lists one, so their accent is heard as such, and American English when it lists none.
// (Phrase biasing, `SpeechRecognition.phrases`, is not used: it is experimental, and the spec's
// "phrases-not-supported" error is what the platform tests expect from a cloud recognizer, which is
// the one Chrome uses by default. The guesses are rescored instead: see `pickHeard`.)
export function englishLang(): string {
  const listed = [...(navigator.languages ?? []), navigator.language].filter(Boolean);
  return listed.find((l) => /^en\b/i.test(l)) ?? "en-US";
}

const ALTERNATIVES = 4;

/** Picks what was said from the recognizer's guesses, best first; the page sets one that knows its
 *  commands and deal names (`pickHeard`). Until then, the best guess. */
let choose: (alternatives: string[]) => string = (a) => a[0] ?? "";

export function setHeardChooser(fn: (alternatives: string[]) => string): void {
  choose = fn;
}

function heard(result: RecognitionResult): string {
  const all: string[] = [];
  for (let i = 0; i < result.length; i++) all.push(result[i].transcript);
  try {
    return choose(all);
  } catch {
    return all[0] ?? ""; // a chooser that fails must not lose what was heard
  }
}

/** Listen for one utterance. Resolves with the final transcript ("" if nothing was heard). */
export function listenOnce(onInterim: (text: string) => void): Promise<string> {
  const Ctor = recognitionCtor();
  if (!Ctor) return Promise.reject(new Error("Speech recognition is not available in this browser."));
  return new Promise((resolve, reject) => {
    const rec = new Ctor();
    active = rec;
    rec.lang = englishLang();
    rec.interimResults = true;
    rec.continuous = false;
    rec.maxAlternatives = ALTERNATIVES;
    let final = "";
    rec.onresult = (e) => {
      let interim = "";
      final = "";
      for (let i = 0; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) final += heard(r);
        else interim += r[0].transcript;
      }
      onInterim((final + interim).trim());
    };
    rec.onerror = (e) => (e.error === "no-speech" || e.error === "aborted" ? resolve("") : reject(new Error(e.error)));
    rec.onend = () => {
      active = null;
      resolve(final.trim());
    };
    rec.start();
  });
}

let active: Recognition | null = null;

/** Stop listening early; whatever was already heard is still delivered. */
export function stopListening(): void {
  active?.stop();
}

const FATAL = new Set(["not-allowed", "service-not-allowed", "audio-capture", "language-not-supported"]);
const RESTART_WINDOW_MS = 15_000;
const RESTART_LIMIT = 12;

/** A microphone that stays open for hands-free use: the browser ends a session after a stretch of
 *  silence or a network hiccup, so the next is opened at once (backing off while the network is down)
 *  until it is stopped. A blocked or missing microphone is reported once and ends it. */
export function browserEars(): Ears | null {
  const Found = recognitionCtor();
  if (!Found) return null;
  const Ctor: RecognitionCtor = Found; // narrowed for the closures below
  let rec: Recognition | null = null;
  let wanted = false;
  let handlers: EarsHandlers | null = null;
  let restarts: number[] = [];
  let backoff = 0;
  let timer = 0;

  const fatal = (reason: string): void => {
    wanted = false;
    window.clearTimeout(timer);
    handlers?.onFatal(reason);
  };

  function begin(): void {
    if (!wanted || !handlers) return;
    const r = new Ctor();
    rec = r;
    r.lang = englishLang();
    r.interimResults = true;
    r.continuous = true;
    r.maxAlternatives = ALTERNATIVES;
    r.onresult = (e) => {
      backoff = 0;
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const result = e.results[i];
        if (result.isFinal) handlers?.onFinal(heard(result));
        else interim += result[0].transcript;
      }
      if (interim.trim()) handlers?.onInterim(interim.trim());
    };
    r.onerror = (e) => {
      if (e.error === "no-speech" || e.error === "aborted") return;
      if (FATAL.has(e.error)) fatal(e.error);
      else backoff = Math.min(5000, backoff ? backoff * 2 : 500); // the network: wait a little longer each time
    };
    r.onend = () => {
      if (rec === r) rec = null;
      if (!wanted) return;
      const now = Date.now();
      restarts = [...restarts.filter((t) => now - t < RESTART_WINDOW_MS), now];
      if (restarts.length > RESTART_LIMIT) {
        fatal("unstable");
        return;
      }
      timer = window.setTimeout(begin, backoff || 120);
    };
    try {
      r.start();
    } catch {
      timer = window.setTimeout(begin, 400); // "already started": try again shortly
    }
  }

  return {
    start(h) {
      handlers = h;
      wanted = true;
      restarts = [];
      backoff = 0;
      begin();
    },
    stop() {
      wanted = false;
      window.clearTimeout(timer);
      const r = rec;
      rec = null;
      if (r) {
        r.onend = null;
        r.onresult = null;
        r.onerror = null;
        try {
          r.stop();
        } catch {
          // Already stopped.
        }
      }
    },
  };
}

export interface SpeakEvents {
  onStart?: () => void;
  /** Each word boundary, where the voice reports them (many network voices don't): the word's
   *  character offset and length in the spoken text. */
  onWord?: (index: number, length: number) => void;
  /** Finished, cancelled or failed; `interrupted` when it was cut off (Escape, the mic, a new turn). */
  onEnd?: (interrupted: boolean) => void;
}

/** The utterance in flight; stopping it marks it cut, so its end reports an interruption. A reply in
 *  the server's voice also has a request or an audio element to stop: `halt` does that, and ends it. */
interface Speaking {
  cut: boolean;
  halt?: () => void;
}
let speaking: Speaking | null = null;

// The server's voice (Amazon Polly, `/api/speak`), when it offers one: the default voice then, and
// the browser's own voice whenever the server's can't speak a reply (too long, too slow to start, a
// refusal, the network, autoplay). Picking a browser voice in Settings always uses that voice.
let cloud = false;
const CLOUD_MAX = 1500; // the server's own limit (voice.py MAX_CHARS)
// Past this the browser's voice starts instead. A warm reply takes one to two seconds; the first after
// the server starts also opens its connection to AWS, so the wait covers that and no more.
const CLOUD_WAIT_MS = 6000;

export function setCloudVoice(on: boolean): void {
  cloud = on;
}

export function speak(text: string, events: SpeakEvents = {}): void {
  if (!text) {
    events.onEnd?.(false);
    return;
  }
  stopSpeaking();
  const state: Speaking = { cut: false };
  speaking = state;
  let ended = false;
  const end = (): void => {
    if (ended) return;
    ended = true;
    if (speaking === state) speaking = null;
    events.onEnd?.(state.cut);
  };
  if (cloud && !settings().voiceURI && text.length <= CLOUD_MAX && navigator.onLine !== false) {
    void cloudSpeak(text, events, state, end);
  } else {
    browserSpeak(text, events, state, end);
  }
}

interface Spoken {
  audio: string;
  type: string;
  /** [milliseconds into the audio, offset in the text, length]: one per word. */
  marks: [number, number, number][];
}

async function cloudSpeak(text: string, events: SpeakEvents, state: Speaking, end: () => void): Promise<void> {
  const ctl = new AbortController();
  let audio: HTMLAudioElement | null = null;
  let url = "";
  let tick = 0;
  let started = false;
  const cleanup = (): void => {
    window.clearInterval(tick);
    if (audio) {
      audio.onplay = audio.onended = audio.onerror = null;
      audio.pause();
    }
    if (url) URL.revokeObjectURL(url);
    url = "";
  };
  state.halt = () => {
    ctl.abort();
    cleanup();
    end();
  };
  const fallback = (): void => {
    cleanup();
    state.halt = undefined;
    if (state.cut) end();
    else browserSpeak(text, events, state, end);
  };

  let body: Spoken;
  const timer = window.setTimeout(() => ctl.abort(), CLOUD_WAIT_MS);
  try {
    const r = await fetch("/api/speak", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text }),
      credentials: "same-origin",
      signal: ctl.signal,
    });
    if (r.status === 401 || r.status === 404) cloud = false; // signed out, or no voice: stop asking
    if (!r.ok) return fallback();
    body = (await r.json()) as Spoken;
    if (typeof body.audio !== "string" || !Array.isArray(body.marks)) return fallback();
  } catch {
    return fallback(); // aborted (too slow, or stopped: `fallback` then just ends it) or the network
  } finally {
    window.clearTimeout(timer);
  }
  if (state.cut) return;

  const bytes = Uint8Array.from(atob(body.audio), (c) => c.charCodeAt(0));
  url = URL.createObjectURL(new Blob([bytes], { type: body.type || "audio/mpeg" }));
  const player = new Audio(url);
  audio = player;
  player.playbackRate = settings().rate; // the pitch is kept (preservesPitch defaults to true)
  let next = 0;
  player.onplay = () => {
    if (started) return;
    started = true;
    events.onStart?.();
    // The marks are in the audio's own time, so they hold at any playback rate.
    tick = window.setInterval(() => {
      const ms = player.currentTime * 1000;
      while (next < body.marks.length && body.marks[next][0] <= ms) {
        const [, index, length] = body.marks[next++];
        events.onWord?.(index, length);
      }
    }, 40);
  };
  player.onended = () => {
    cleanup();
    end();
  };
  player.onerror = () => {
    if (!started) return fallback();
    state.cut = true;
    cleanup();
    end();
  };
  try {
    await player.play();
  } catch {
    if (!started) fallback(); // autoplay refused, or the audio could not be decoded
  }
}

function browserSpeak(text: string, events: SpeakEvents, state: Speaking, end: () => void): void {
  if (!("speechSynthesis" in window)) {
    end();
    return;
  }
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = englishLang();
  const { rate, voiceURI } = settings();
  u.rate = rate;
  const chosen = voiceURI ? voices().find((v) => v.voiceURI === voiceURI) : undefined;
  if (chosen) {
    u.voice = chosen;
    u.lang = chosen.lang;
  }
  u.onstart = () => events.onStart?.();
  u.onboundary = (e) => {
    if (e.name !== "word") return;
    // charLength is missing in some engines: fall back to the run of non-spaces from charIndex.
    const length = e.charLength || (/^\S+/.exec(text.slice(e.charIndex))?.[0].length ?? 1);
    events.onWord?.(e.charIndex, length);
  };
  u.onend = end;
  u.onerror = (e) => {
    if (e.error === "interrupted" || e.error === "canceled") state.cut = true;
    end();
  };
  window.speechSynthesis.speak(u);
}

export function stopSpeaking(): void {
  const was = speaking;
  if (was) {
    was.cut = true;
    was.halt?.();
  }
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
}
