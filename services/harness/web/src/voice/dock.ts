// The hands-free dock: a small bar on every page while the microphone is open, saying in words what
// the page is doing (waiting for "Watchfloor", listening, working, speaking), what it just heard, and
// what it just said, so someone watching can follow and someone not watching loses nothing. It has
// the two controls that matter: "Talk" (the same as the wake word) and "Turn off".

import type { HandsFreeState } from "./handsfree";

export interface Dock {
  render(state: HandsFreeState, hint: string): void;
  /** What the microphone is hearing, for as long as it is meant for the page. */
  heard(text: string): void;
  /** The reply being spoken. */
  said(text: string): void;
}

const el = <T extends HTMLElement>(id: string): T => {
  const node = document.getElementById(id);
  if (!node) throw new Error(`#${id} is missing`);
  return node as T;
};

const clip = (text: string, max = 150): string => (text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text);

export function mountDock(on: { talk(): void; off(): void }): Dock {
  const root = el<HTMLDivElement>("voice-dock");
  const state = el<HTMLParagraphElement>("dock-state");
  const line = el<HTMLParagraphElement>("dock-line");
  el<HTMLButtonElement>("dock-talk").addEventListener("click", on.talk);
  el<HTMLButtonElement>("dock-off").addEventListener("click", on.off);

  return {
    render(next, hint) {
      root.hidden = next === "off";
      root.dataset.state = next;
      document.body.dataset.hf = next === "off" ? "off" : "on";
      state.textContent = hint;
      if (next === "waiting" || next === "off") line.textContent = "";
    },
    heard(text) {
      line.textContent = text ? `“${clip(text)}”` : "";
    },
    said(text) {
      line.textContent = clip(text);
    },
  };
}
