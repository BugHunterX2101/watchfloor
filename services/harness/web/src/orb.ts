// The voice state shared by the plot's lamp, the small lamp by the conversation and the composer
// (body[data-voice]).
// Each state is also written out in #voice-state, so it never depends on motion or colour alone.

export type VoiceState = "idle" | "listening" | "thinking" | "speaking" | "error";

const LABELS: Record<VoiceState, string> = {
  idle: "Ready",
  listening: "Listening…",
  thinking: "Working on it…",
  speaking: "Speaking",
  error: "Something went wrong",
};

let label: HTMLElement | null = null;
let title = document.title;

/** The tab title while idle: the router sets it per page. */
export function setBaseTitle(next: string): void {
  title = next;
  if (voiceState() === "idle") document.title = title;
}

export function setVoice(state: VoiceState, text?: string): void {
  label ??= document.getElementById("voice-state");
  document.body.dataset.voice = state;
  if (label) label.textContent = text ?? LABELS[state];
  // The tab says it too, so a demo running in a background tab still shows it is listening.
  document.title = state === "idle" ? title : `${text ?? LABELS[state]} · Watchfloor`;
}

export function voiceState(): VoiceState {
  return (document.body.dataset.voice as VoiceState | undefined) ?? "idle";
}

/** One pulse of the lamps, on each spoken word boundary: the motion follows the real speech. */
export function beat(): void {
  for (const orb of document.querySelectorAll<Element>(".orb, .lamp")) {
    orb.classList.add("beat");
    window.setTimeout(() => orb.classList.remove("beat"), 110);
  }
}
