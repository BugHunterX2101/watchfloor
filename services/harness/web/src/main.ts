// The chat loop: text or voice in, the harness turn, the spoken reply, the view and the trace.

import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
// The optical-size cut: display forms for the big headings, sturdier text forms in the transcript.
import "@fontsource-variable/fraunces/opsz.css";
import "./styles.css";
import { converse, logout, session, SignedOut, type Call, type Line } from "./api";
import { clearView, renderView, setDevice, setTheme, type Device } from "./apphost";
import { renderBanner } from "./banner";
import { caption } from "./captions";
import { ICONS } from "./icons";
import { beat, setVoice } from "./orb";
import { earcon, setSounds } from "./sound";
import { canListen, listenOnce, speak, stopListening, stopSpeaking } from "./speech";
import { clearTrace, pendingTrace, showTrace } from "./trace";

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
const hearing = $<HTMLParagraphElement>("hearing");

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
  sendButton.disabled = next || !navigator.onLine;
  input.disabled = next;
}

function reply(line: HTMLElement, text: string): void {
  if (!voice.checked) {
    setVoice("idle");
    return;
  }
  const cap = caption(line, text);
  speak(text, {
    onStart: () => setVoice("speaking"),
    onWord: (index) => {
      beat();
      cap.word(index);
    },
    onEnd: () => {
      cap.end();
      setVoice("idle");
    },
  });
}

/** Calls made from inside the view (row tap, Propose, Approve) land in the trace; an approved
 *  action that ran gets the "done" earcon. */
function viewCall(call: Call): void {
  showTrace(calls, [call], call.ms, "View");
  if (call.tool === "confirm_action" && call.ok) earcon.done();
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
  const running = pendingTrace(calls);
  const t0 = performance.now();
  try {
    const turn = await converse(text, history);
    running.remove();
    pending.classList.remove("pending");
    pending.removeAttribute("aria-label");
    pending.textContent = turn.reply;
    history.push({ role: "user", text }, { role: "assistant", text: turn.reply });
    showTrace(calls, turn.calls, performance.now() - t0);
    if (turn.ui) {
      renderView(viewBox, turn.ui, viewCall).catch((e: Error) =>
        addLine("system", `The view could not start: ${e.message}`, "error"),
      );
    }
    reply(pending, turn.reply);
  } catch (err) {
    running.remove();
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
  earcon.listen();
  try {
    const heard = await listenOnce((interim) => {
      input.value = interim;
      hearing.textContent = interim;
    });
    earcon.stop();
    input.value = "";
    hearing.textContent = "";
    listening = false;
    if (heard) await send(heard);
    else setVoice("idle", "Didn't catch that. Try again?");
  } catch (err) {
    setVoice("error");
    addLine("system", `Voice input stopped: ${(err as Error).message}`, "error");
    window.setTimeout(() => setVoice("idle"), 2400);
  } finally {
    listening = false;
    hearing.textContent = "";
    mic.setAttribute("aria-pressed", "false");
    mic.setAttribute("aria-label", "Speak");
    mic.innerHTML = ICONS.mic;
  }
});

voice.addEventListener("change", () => {
  setSounds(voice.checked);
  if (!voice.checked) stopSpeaking();
});

const typing = (t: EventTarget | null): boolean =>
  t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));

document.addEventListener("keydown", (e) => {
  // "/" jumps to the message box, as in most search-first tools.
  if (e.key === "/" && !e.ctrlKey && !e.metaKey && !e.altKey && !typing(e.target) && !form.hidden) {
    e.preventDefault();
    input.focus();
    return;
  }
  if (e.key !== "Escape") return;
  stopSpeaking();
  if (listening) stopListening();
  if (document.body.dataset.voice === "speaking") setVoice("idle");
});

// Device preview and theme: radio groups (arrow keys move between options, as WAI-ARIA radios do).
function radioGroup(selector: string, onChoose: (b: HTMLButtonElement) => void): (b: HTMLButtonElement) => void {
  const buttons = [...document.querySelectorAll<HTMLButtonElement>(selector)];
  const choose = (button: HTMLButtonElement): void => {
    for (const b of buttons) {
      const on = b === button;
      b.setAttribute("aria-checked", String(on));
      b.tabIndex = on ? 0 : -1;
    }
    onChoose(button);
  };
  buttons.forEach((b, i) => {
    b.tabIndex = b.getAttribute("aria-checked") === "true" ? 0 : -1;
    b.addEventListener("click", () => choose(b));
    b.addEventListener("keydown", (e) => {
      const forward = e.key === "ArrowRight" || e.key === "ArrowDown";
      const back = e.key === "ArrowLeft" || e.key === "ArrowUp";
      if (!forward && !back) return;
      e.preventDefault();
      const next = buttons[(i + (forward ? 1 : -1) + buttons.length) % buttons.length];
      choose(next);
      next.focus();
    });
  });
  return choose;
}

radioGroup(".device [data-device]", (b) => setDevice(b.dataset.device as Device));

type ThemeChoice = "auto" | "light" | "dark";
const THEME_ICON: Record<ThemeChoice, string> = { auto: ICONS.auto, light: ICONS.sun, dark: ICONS.moon };
for (const b of document.querySelectorAll<HTMLButtonElement>(".theme [data-theme-choice]")) {
  b.innerHTML = THEME_ICON[b.dataset.themeChoice as ThemeChoice];
}
const chooseTheme = radioGroup(".theme [data-theme-choice]", (b) => {
  const choice = b.dataset.themeChoice as ThemeChoice;
  setTheme(choice);
  try {
    localStorage.setItem("wf-theme", choice);
  } catch {
    // Storage blocked: the choice lasts for this page only.
  }
});
try {
  const saved = localStorage.getItem("wf-theme");
  const button = document.querySelector<HTMLButtonElement>(`.theme [data-theme-choice="${saved}"]`);
  if (saved && button) chooseTheme(button);
} catch {
  // Storage blocked: follow the system.
}

// Offline: say so plainly and hold the composer, rather than letting a send fail.
function connectivity(): void {
  const offline = !navigator.onLine;
  sendButton.disabled = offline || busy;
  if (offline) setVoice("error", "Offline · check your connection");
  else if (document.body.dataset.voice === "error") setVoice("idle");
}
window.addEventListener("offline", connectivity);
window.addEventListener("online", connectivity);

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
  setSounds(voice.checked);
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
