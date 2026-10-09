// The chat loop: text or voice in, the harness turn, the spoken reply, the view and the trace.

import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import "@fontsource-variable/fraunces";
import "./styles.css";
import { converse, logout, session, SignedOut, type Line } from "./api";
import { clearView, renderView, setDevice, type Device } from "./apphost";
import { renderBanner } from "./banner";
import { ICONS } from "./icons";
import { beat, setVoice } from "./orb";
import { canListen, listenOnce, speak, stopListening, stopSpeaking } from "./speech";
import { clearTrace, showTrace } from "./trace";

const $ = <T extends HTMLElement>(id: string): T => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`#${id} is missing`);
  return el as T;
};

const transcript = $<HTMLOListElement>("transcript");
const form = $<HTMLFormElement>("composer");
const input = $<HTMLInputElement>("message");
const mic = $<HTMLButtonElement>("mic");
const sendButton = $<HTMLButtonElement>("send");
const voice = $<HTMLInputElement>("voice");
const signin = $<HTMLDivElement>("signin");
const engineLabel = $<HTMLParagraphElement>("engine");
const logoutButton = $<HTMLButtonElement>("logout");
const viewBox = $<HTMLDivElement>("view");
const calls = $<HTMLOListElement>("calls");
const suggestions = $<HTMLDivElement>("suggestions");

// Phrasings the offline keyword router understands too (harness_api/offline.py), so every
// suggestion works with or without a model.
const STARTERS = [
  "What needs my attention today?",
  "Why is Kestrel at risk?",
  "How's Harbor & Pine doing?",
  "What am I watching?",
];

const SIGNIN_ERRORS: Record<string, string> = {
  access_denied: "Sign-in was cancelled.",
  state_mismatch: "That sign-in link expired. Please try again.",
  issuer_mismatch: "The sign-in response came from an unexpected server.",
  token_error: "The sign-in could not be completed. Please try again.",
};

const history: Line[] = [];
let busy = false;
let listening = false;

mic.innerHTML = ICONS.mic;
sendButton.innerHTML = ICONS.send;

const reducedMotion = (): boolean => matchMedia("(prefers-reduced-motion: reduce)").matches;

function addLine(kind: "user" | "assistant" | "system", text: string, tone?: "error"): HTMLLIElement {
  const li = document.createElement("li");
  li.className = `line ${kind}${tone ? ` ${tone}` : ""}`;
  li.textContent = text;
  transcript.append(li);
  li.scrollIntoView({ block: "end", behavior: reducedMotion() ? "auto" : "smooth" });
  return li;
}

function pendingLine(): HTMLLIElement {
  const li = addLine("assistant", "");
  li.classList.add("pending");
  li.setAttribute("aria-label", "Working on it");
  const dots = document.createElement("span");
  dots.className = "dots";
  dots.append(document.createElement("i"), document.createElement("i"), document.createElement("i"));
  li.append(dots);
  return li;
}

function renderSuggestions(): void {
  suggestions.replaceChildren(
    ...STARTERS.map((text) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.textContent = text;
      chip.addEventListener("click", () => void send(text));
      return chip;
    }),
  );
}

function setSignedIn(signedIn: boolean, engine: string | null): void {
  signin.hidden = signedIn;
  form.hidden = !signedIn;
  logoutButton.hidden = !signedIn;
  suggestions.hidden = !signedIn || history.length > 0;
  engineLabel.textContent =
    engine === "offline"
      ? "Offline engine · keyword router, no model"
      : engine
        ? `Model engine · ${engine}`
        : "Not signed in";
}

function setBusy(next: boolean): void {
  busy = next;
  sendButton.disabled = next;
  input.disabled = next;
}

function reply(text: string): void {
  if (!voice.checked) {
    setVoice("idle");
    return;
  }
  speak(text, {
    onStart: () => setVoice("speaking"),
    onWord: beat,
    onEnd: () => setVoice("idle"),
  });
}

async function send(message: string): Promise<void> {
  const text = message.trim();
  if (!text || busy) return;
  setBusy(true);
  stopSpeaking();
  suggestions.hidden = true;
  setVoice("thinking");
  addLine("user", text);
  const pending = pendingLine();
  const t0 = performance.now();
  try {
    const turn = await converse(text, history);
    pending.classList.remove("pending");
    pending.removeAttribute("aria-label");
    pending.textContent = turn.reply;
    history.push({ role: "user", text }, { role: "assistant", text: turn.reply });
    showTrace(calls, turn.calls, performance.now() - t0);
    if (turn.ui) {
      // Calls the view makes (row tap, Propose, Approve) land in the trace too.
      renderView(viewBox, turn.ui, (call) => showTrace(calls, [call], call.ms, "View")).catch((e: Error) =>
        addLine("system", `The view could not start: ${e.message}`, "error"),
      );
    }
    reply(turn.reply);
  } catch (err) {
    pending.remove();
    setVoice("error");
    if (err instanceof SignedOut) {
      setSignedIn(false, null);
      addLine("system", "Your session ended. Please sign in again.", "error");
    } else {
      addLine("system", `Something went wrong: ${(err as Error).message}`, "error");
    }
    window.setTimeout(() => setVoice("idle"), 2400);
  } finally {
    setBusy(false);
    if (!form.hidden) input.focus({ preventScroll: true });
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value;
  input.value = "";
  void send(text);
});

if (!canListen()) {
  mic.disabled = true;
  mic.title = "Voice input needs Chrome or Edge";
  mic.setAttribute("aria-label", "Voice input needs Chrome or Edge");
}

mic.addEventListener("click", async () => {
  if (listening) {
    stopListening();
    return;
  }
  if (busy) return;
  stopSpeaking();
  listening = true;
  mic.setAttribute("aria-pressed", "true");
  mic.setAttribute("aria-label", "Stop listening");
  mic.innerHTML = ICONS.stop;
  setVoice("listening");
  try {
    const heard = await listenOnce((interim) => (input.value = interim));
    input.value = "";
    listening = false;
    if (heard) await send(heard);
    else setVoice("idle", "Didn't catch that. Try again?");
  } catch (err) {
    setVoice("error");
    addLine("system", `Voice input stopped: ${(err as Error).message}`, "error");
    window.setTimeout(() => setVoice("idle"), 2400);
  } finally {
    listening = false;
    mic.setAttribute("aria-pressed", "false");
    mic.setAttribute("aria-label", "Speak");
    mic.innerHTML = ICONS.mic;
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  stopSpeaking();
  if (listening) stopListening();
  if (document.body.dataset.voice === "speaking") setVoice("idle");
});

// Device preview: a radio group (arrow keys move between sizes, as WAI-ARIA radios do).
const deviceButtons = [...document.querySelectorAll<HTMLButtonElement>(".device [data-device]")];

function chooseDevice(button: HTMLButtonElement): void {
  for (const b of deviceButtons) {
    const on = b === button;
    b.setAttribute("aria-checked", String(on));
    b.tabIndex = on ? 0 : -1;
  }
  setDevice(button.dataset.device as Device);
}

deviceButtons.forEach((b, i) => {
  b.tabIndex = b.getAttribute("aria-checked") === "true" ? 0 : -1;
  b.addEventListener("click", () => chooseDevice(b));
  b.addEventListener("keydown", (e) => {
    const forward = e.key === "ArrowRight" || e.key === "ArrowDown";
    const back = e.key === "ArrowLeft" || e.key === "ArrowUp";
    if (!forward && !back) return;
    e.preventDefault();
    const next = deviceButtons[(i + (forward ? 1 : -1) + deviceButtons.length) % deviceButtons.length];
    chooseDevice(next);
    next.focus();
  });
});

logoutButton.addEventListener("click", async () => {
  stopSpeaking();
  await logout();
  history.length = 0;
  transcript.replaceChildren();
  clearTrace(calls);
  await clearView(viewBox);
  setVoice("idle");
  setSignedIn(false, null);
});

async function start(): Promise<void> {
  renderBanner($("banner"));
  renderSuggestions();
  setVoice("idle");
  const params = new URLSearchParams(location.search);
  const error = params.get("signin");
  if (error) {
    addLine("system", SIGNIN_ERRORS[error] ?? `Sign-in failed (${error}).`, "error");
    window.history.replaceState(null, "", location.pathname);
  }
  try {
    const info = await session();
    setSignedIn(info.signed_in, info.engine);
    if (info.signed_in && !transcript.children.length) {
      addLine("system", "Signed in · ask anything, or pick a suggestion");
    }
  } catch (err) {
    setSignedIn(false, null);
    setVoice("error", "Harness API unreachable");
    addLine("system", `The harness API is not reachable: ${(err as Error).message}`, "error");
  }
}

void start();
