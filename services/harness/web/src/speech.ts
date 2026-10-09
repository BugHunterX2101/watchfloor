// Voice in (Web Speech API recognition, Chrome) and voice out (speechSynthesis). Both are optional:
// without them the harness is a text chat.

interface RecognitionResult {
  readonly isFinal: boolean;
  readonly 0: { readonly transcript: string };
}

interface RecognitionEvent {
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

export interface SpeakEvents {
  onStart?: () => void;
  /** Each word boundary, where the voice reports them (many network voices don't): the word's
   *  character offset and length in the spoken text. */
  onWord?: (index: number, length: number) => void;
  onEnd?: () => void; // finished, cancelled or failed
}

export function speak(text: string, events: SpeakEvents = {}): void {
  if (!("speechSynthesis" in window) || !text) {
    events.onEnd?.();
    return;
  }
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = navigator.language || "en-US";
  u.rate = 1.05;
  u.onstart = () => events.onStart?.();
  u.onboundary = (e) => {
    if (e.name !== "word") return;
    // charLength is missing in some engines: fall back to the run of non-spaces from charIndex.
    const length = e.charLength || (/^\S+/.exec(text.slice(e.charIndex))?.[0].length ?? 1);
    events.onWord?.(e.charIndex, length);
  };
  u.onend = () => events.onEnd?.();
  u.onerror = () => events.onEnd?.();
  window.speechSynthesis.speak(u);
}

export function stopSpeaking(): void {
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
}
