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
  readonly 0: { readonly transcript: string };
}

interface RecognitionEvent {
  readonly resultIndex: number;
  readonly results: ArrayLike<RecognitionResult>;
}

interface Recognition {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
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

/** Listen for one utterance. Resolves with the final transcript ("" if nothing was heard). */
export function listenOnce(onInterim: (text: string) => void): Promise<string> {
  const Ctor = recognitionCtor();
  if (!Ctor) return Promise.reject(new Error("Speech recognition is not available in this browser."));
  return new Promise((resolve, reject) => {
    const rec = new Ctor();
    active = rec;
    rec.lang = navigator.language || "en-US";
    rec.interimResults = true;
    rec.continuous = false;
    let final = "";
    rec.onresult = (e) => {
      let interim = "";
      for (let i = 0; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) final += r[0].transcript;
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
    r.lang = navigator.language || "en-US";
    r.interimResults = true;
    r.continuous = true;
    r.onresult = (e) => {
      backoff = 0;
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const result = e.results[i];
        if (result.isFinal) handlers?.onFinal(result[0].transcript);
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

/** The utterance in flight; stopping it marks it cut, so its end reports an interruption. */
let speaking: { cut: boolean } | null = null;

export function speak(text: string, events: SpeakEvents = {}): void {
  if (!("speechSynthesis" in window) || !text) {
    events.onEnd?.(false);
    return;
  }
  if (speaking) speaking.cut = true;
  window.speechSynthesis.cancel();
  const state = { cut: false };
  speaking = state;
  const end = (): void => {
    if (speaking === state) speaking = null;
    events.onEnd?.(state.cut);
  };
  const u = new SpeechSynthesisUtterance(text);
  u.lang = navigator.language || "en-US";
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
  if (speaking) speaking.cut = true;
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
}
