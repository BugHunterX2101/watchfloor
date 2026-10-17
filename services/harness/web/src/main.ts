// The app shell: the chat loop (text or voice in, the harness turn, the spoken reply, the view and
// the trace) and the pages around it (Pipeline, Orders, Activity, Guide), the 3D orb, and what the
// page remembers about this viewer. Pages are modules with a mount function; this file connects
// them to the conversation and to each other.

import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
// The optical-size cut: display forms for the big headings, sturdier text forms in the transcript.
import "@fontsource-variable/fraunces/opsz.css";
import "./styles.css";
import { clearActivity, mountActivity, recordTurn } from "./activity";
import { converse, logout, session, SignedOut, type Call, type Line, type Screen } from "./api";
import { clearView, renderView, setDevice, setTheme } from "./apphost";
import { renderBanner } from "./banner";
import { caption } from "./captions";
import { confirmDialog, h, toast } from "./dom";
import { mountGuide } from "./guide";
import { ICONS } from "./icons";
import { greeting, promptSafe, toBriefing, type Briefing } from "./logic";
import { beat, setVoice, voiceState } from "./orb";
import { mountOrb3d } from "./orb3d";
import { mountOrders } from "./orders";
import { mountPipeline } from "./pipeline";
import { mountScene } from "./scene3d";
import { current, initRouter, navigate, onRoute, parseHash, setBadge, type Route } from "./router";
import { mountSettings } from "./settings";
import { earcon, earconFor, setSounds } from "./sound";
import { browserEars, canListen, listenOnce, speak, stopListening, stopSpeaking, voices } from "./speech";
import {
  clearSkyline,
  forgetEverything,
  loadSkyline,
  onSettings,
  pinnedLabels,
  recents,
  rememberAsk,
  saveSkyline,
  settings,
  update,
  type DeviceChoice,
  type ThemeChoice,
} from "./store";
import { clearTrace, pendingTrace, showTrace } from "./trace";
import { NEEDS_YES } from "./voice/actions";
import { removeByVoice, run as runAction, type Host, type Outcome } from "./voice/commander";
import { mountDock } from "./voice/dock";
import { HELP_TEXT, interpret, stripWake, type Context } from "./voice/grammar";
import { createHandsFree, type HandsFree } from "./voice/handsfree";
import { speakable } from "./voice/text";

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
const greet = $<HTMLParagraphElement>("greet");
const idlePins = $<HTMLDivElement>("idle-pins");
const screen = document.querySelector<HTMLElement>(".screen");
const page = (name: Route): HTMLElement => {
  const el = document.querySelector<HTMLElement>(`[data-page="${name}"]`);
  if (!el) throw new Error(`page ${name} is missing`);
  return el;
};

// Phrasings the offline keyword router understands too (harness_api/offline.py), so every
// suggestion works with or without a model.
const STARTERS = [
  "What needs my attention today?",
  "Why is Kestrel at risk?",
  "How's Harbor & Pine doing?",
  "What am I watching?",
];

const SIGNIN_ERRORS: Record<string, string> = {
  access_denied: "Sign-in was canceled.",
  state_mismatch: "That sign-in link expired. Please try again.",
  issuer_mismatch: "The sign-in response came from an unexpected server.",
  token_error: "The sign-in could not be completed. Please try again.",
  no_code: "The sign-in response was incomplete. Please try again.",
};

const history: Line[] = [];
let busy = false;
let listening = false;
let engine: string | null = null; // "offline", the model's name, or null when signed out
let wantFocus = false;
let lastReply = ""; // what "repeat that" says again: the assistant's last answer, or the last page read out
let muteNext = false; // "stop" said while a reply was on its way: show it, don't read it
let speechPending = false; // a reply is being (or about to be) read aloud

const handsFreeButton = $<HTMLButtonElement>("handsfree");
mic.innerHTML = ICONS.mic;
sendButton.innerHTML = ICONS.send;
handsFreeButton.innerHTML = ICONS.handsfree;
$<HTMLButtonElement>("open-settings").innerHTML = ICONS.settings;
for (const el of document.querySelectorAll<HTMLElement>(".nav-ico[data-icon]")) {
  el.innerHTML = ICONS[el.dataset.icon as keyof typeof ICONS] ?? "";
}

const reducedMotion = (): boolean =>
  matchMedia("(prefers-reduced-motion: reduce)").matches || settings().motion === "calm";

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

// --- what the screen says before you've asked anything ---

function updateGreeting(): void {
  greet.textContent = greeting(new Date().getHours(), settings().name);
}

function updateIdle(): void {
  updateGreeting();
  const labels = engine !== null ? pinnedLabels().slice(0, 3) : [];
  idlePins.hidden = labels.length === 0;
  idlePins.replaceChildren(
    ...labels.map((label) => {
      const chip = h("button", { type: "button", class: "chip", title: "A deal you pinned" }, label);
      chip.addEventListener("click", () => void send(`How's ${promptSafe(label)} doing?`));
      return chip;
    }),
  );
}

/** Pinned deals first, then your own recent questions, then the starters: each only once. */
function suggestionList(): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  const add = (text: string): void => {
    const key = text.trim().toLowerCase();
    if (key && !seen.has(key) && out.length < 6) {
      seen.add(key);
      out.push(text.trim());
    }
  };
  for (const label of pinnedLabels().slice(0, 2)) add(`How's ${promptSafe(label)} doing?`);
  for (const text of recents().slice(0, 2)) add(text);
  for (const text of STARTERS) add(text);
  return out;
}

function renderSuggestions(): void {
  suggestions.replaceChildren(
    ...suggestionList().map((text) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.textContent = text;
      chip.addEventListener("click", () => void send(text));
      return chip;
    }),
  );
}

// --- signed in / out ---

function setSignedIn(signedIn: boolean, engineName: string | null): void {
  engine = signedIn ? (engineName ?? "model") : null;
  signin.hidden = signedIn;
  form.hidden = !signedIn;
  logoutButton.hidden = !signedIn;
  renderSuggestions();
  suggestions.hidden = !signedIn || history.length > 0;
  engineLabel.textContent =
    engineName === "offline"
      ? "Offline engine · keyword router, no model"
      : engineName
        ? `Model engine · ${engineName}`
        : "Not signed in";
  pipeline.setSignedIn(signedIn);
  orders.setSignedIn(signedIn);
  orders.setEngine(engine);
  guide.refresh();
  if (signedIn) {
    scene?.setDeals(loadSkyline()); // the floor shows the pipeline as it was last seen
  } else {
    setBadge("pipeline", null);
    scene?.setDeals([]);
    clearSkyline(); // a shared browser must not show the last person's pipeline
  }
  updateIdle();
}

function handleSignedOut(): void {
  stopSpeaking();
  setSignedIn(false, null);
  addLine("system", "Your session ended. Please sign in again.", "error");
}

function setBusy(next: boolean): void {
  busy = next;
  sendButton.disabled = next || !navigator.onLine;
  input.disabled = next;
}

/** A request is finished and nothing more will be said: hands-free listens for a follow-up. */
function settle(): void {
  if (hf?.state() === "busy") hf.done();
}

/** Show a reply's words on the dock and read it aloud (if reading aloud is on), telling hands-free
 *  when the assistant starts and stops talking so the microphone knows what it is hearing.
 *  `shown` is the line's text; `spoken` can carry more (a page read out after the answer). */
function deliver(line: HTMLElement | null, shown: string, spoken: string = shown): void {
  dock.said(shown);
  if (muteNext) {
    muteNext = false;
    setVoice("idle");
    settle();
    return;
  }
  const say = speakable(spoken);
  if (!settings().readAloud || !say) {
    setVoice("idle");
    settle();
    return;
  }
  // Word-by-word captions follow the voice only when the spoken words are the written words.
  const cap = line && say === shown ? caption(line, shown) : null;
  speechPending = true;
  speak(say, {
    onStart: () => {
      setVoice("speaking");
      hf?.speaking(say);
    },
    onWord: (index) => {
      beat();
      cap?.word(index);
    },
    onEnd: (interrupted) => {
      speechPending = false;
      cap?.end(interrupted);
      // A reply cut off by a new turn ends after that turn has set "thinking": leave that alone.
      if (voiceState() === "speaking") setVoice("idle");
      hf?.spoke();
    },
  });
}

/** Something the assistant says that nobody asked for (a confirmation's outcome, hands-free turning off). */
function announce(text: string): void {
  if (!text) return;
  const line = addLine("assistant", text);
  if (current() !== "watch") toast(text);
  deliver(line, text);
}

/** A wait that says what it is as it grows: a quiet "Working on it…" first, then a few seconds in
 *  that it is still going, then past ten seconds that this is longer than usual (NN/g: ~1 s needs an
 *  indicator, ~10 s needs an explanation). Returns a stop function. */
function waitLadder(running: { note(text: string): void }): () => void {
  const steps: [number, string, string][] = [
    [3000, "Still working…", "still working · model and tools"],
    [10000, "Taking longer than usual…", "taking longer than usual"],
  ];
  const timers = steps.map(([ms, voiceText, traceText]) =>
    window.setTimeout(() => {
      if (voiceState() === "thinking") setVoice("thinking", voiceText);
      running.note(traceText);
    }, ms),
  );
  return () => timers.forEach((t) => window.clearTimeout(t));
}

/** A call made outside the chat (a view's tap, or a page): it lands in the trace and in Activity,
 *  and an action that ran or was undone gets its earcon, the same as when it was asked by voice. */
function report(call: Call, source: string): void {
  showTrace(calls, [call], call.ms, source);
  recordTurn(source, [call], call.ms);
  earconFor(call.tool, call.ok);
}

const viewCall = (call: Call): void => report(call, "View");

/** A briefing was shown or loaded: the pipeline tab's badge and the 3D floor's skyline follow it. */
function briefingSeen(b: Briefing): void {
  setBadge("pipeline", b.attention_count);
  const deals = b.deals.map((d) => ({ id: d.deal_id, band: d.band, health: d.health }));
  scene?.setDeals(deals);
  saveSkyline(deals);
}

function noteBriefing(data: string): void {
  try {
    const briefing = toBriefing(JSON.parse(data));
    if (briefing) briefingSeen(briefing);
  } catch {
    // Not JSON: the view still renders; only the badge is skipped.
  }
}

const CONFIRMATION = /^\s*(?:yes|yeah|yep|sure|ok|okay|no|nope|undo|thanks?)\b/i;

async function send(message: string): Promise<void> {
  const text = message.trim();
  if (!text || busy) return;
  setBusy(true);
  stopSpeaking();
  suggestions.hidden = true;
  setVoice("thinking");
  addLine("user", text);
  if (text.length > 3 && !CONFIRMATION.test(text)) rememberAsk(text);
  const pending = pendingLine();
  const running = pendingTrace(calls);
  const stopLadder = waitLadder(running);
  const t0 = performance.now();
  try {
    const turn = await converse(text, history, screenState());
    stopLadder();
    running.remove();
    pending.classList.remove("pending");
    pending.removeAttribute("aria-label");
    pending.textContent = turn.reply;
    history.push({ role: "user", text }, { role: "assistant", text: turn.reply });
    const elapsed = performance.now() - t0;
    showTrace(calls, turn.calls, elapsed);
    recordTurn("Turn", turn.calls, elapsed);
    for (const call of turn.calls) earconFor(call.tool, call.ok);
    if (turn.ui) {
      if (turn.ui.tool === "get_risk_briefing") noteBriefing(turn.ui.data);
      renderView(viewBox, turn.ui, viewCall).catch((e: Error) =>
        addLine("system", `The view could not start: ${e.message}`, "error"),
      );
    }
    // What the assistant asked the page to do (open a deal, change the board, switch the theme). A
    // question like "sign out?" is put to the user by the page and replaces the reply when spoken.
    let question = "";
    const extras: string[] = [];
    for (const act of turn.actions) {
      let out: Outcome;
      try {
        out = await runAction(act, host);
      } catch (err) {
        out = { say: `That didn't work: ${(err as Error).message}`, content: false };
      }
      if (NEEDS_YES.has(act.type)) question = out.say;
      else if (out.content) extras.push(out.say);
    }
    lastReply = [turn.reply, ...extras].join(" ");
    for (const extra of extras) addLine("assistant", extra);
    deliver(pending, turn.reply, question || lastReply);
  } catch (err) {
    stopLadder();
    running.remove();
    pending.remove();
    setVoice("error");
    muteNext = false;
    if (err instanceof SignedOut) handleSignedOut();
    else addLine("system", `Something went wrong: ${(err as Error).message}`, "error");
    window.setTimeout(() => setVoice("idle"), 2400);
    settle();
  } finally {
    setBusy(false);
    if (!form.hidden && current() === "watch") input.focus({ preventScroll: true });
  }
}

/** A page hands a question to the conversation: go to Watch and ask it there. */
function ask(text: string): void {
  navigate("watch");
  if (engine === null) {
    toast("Sign in first, then ask.", "error");
    return;
  }
  if (busy) {
    toast("Still working on your last question.", "error");
    return;
  }
  void send(text);
}

// --- what was said or typed: a command the page runs at once, or a question for the assistant ---

function dialogKind(): "none" | "settings" | "deal" | "confirm" {
  const open = document.querySelector<HTMLDialogElement>("dialog[open]");
  if (!open) return "none";
  if (open.classList.contains("small")) return "confirm";
  return open.querySelector("#settings-title") ? "settings" : "deal";
}

/** What the grammar needs to read an utterance the way it was meant. */
function grammarContext(): Context {
  return {
    page: current(),
    signedIn: engine !== null,
    dialog: dialogKind(),
    confirming: false,
    deals: pipeline.deals().map((d) => ({ id: d.deal_id, account: d.account, name: d.name })),
    rules: orders.rules().map((r) => ({ id: r.rule_id, description: r.description })),
    openDealId: pipeline.openDealId(),
  };
}

/** What the assistant is told about the screen with every message (fixed words only). */
function screenState(): Screen {
  const view = pipeline.view();
  return {
    page: current(),
    theme: settings().theme,
    read_aloud: settings().readAloud,
    hands_free: hf !== null && hf.state() !== "off",
    dialog: dialogKind(),
    segment: view.segment,
    band: view.band,
    sort: view.sort,
  };
}

/** A command that ran here: the user's words, the answer, and the answer read aloud. */
async function local(said: string, work: () => Promise<Outcome>, quiet = false): Promise<void> {
  setVoice("thinking");
  if (!quiet) addLine("user", said);
  let out: Outcome;
  try {
    out = await work();
  } catch (err) {
    if (err instanceof SignedOut) {
      handleSignedOut();
      return;
    }
    out = { say: `That didn't work: ${(err as Error).message}`, content: false };
  }
  if (!out.say) {
    setVoice("idle");
    settle();
    return;
  }
  if (out.content) lastReply = out.say;
  const line = addLine("assistant", out.say);
  if (current() !== "watch") toast(out.say);
  deliver(line, out.say);
}

async function handleInput(raw: string): Promise<void> {
  const text = raw.trim();
  if (!text) return;
  const intent = interpret(text, grammarContext());
  const heard = stripWake(text).text || text;
  switch (intent.kind) {
    case "model":
      await send(intent.text || text);
      return;
    case "wake":
      earcon.listen();
      return;
    case "yes":
    case "no": {
      // The page's own dialog is the question; the answer is the button a tap would have pressed.
      const button = document.querySelector<HTMLButtonElement>(
        `dialog.small[open] button[value="${intent.kind === "yes" ? "confirm" : "cancel"}"]`,
      );
      button?.click();
      return;
    }
    case "back":
      stopSpeaking();
      window.history.back();
      return;
    case "signin":
      stopSpeaking();
      try {
        sessionStorage.setItem("wf.return", current());
      } catch {
        // Without session storage the user lands on Watch.
      }
      window.location.assign("/oauth/login");
      return;
    case "help":
      stopSpeaking();
      await local(heard, async () => ({ say: HELP_TEXT, content: true }));
      return;
    case "remove_rule":
      stopSpeaking();
      await local(heard, async () => ({ say: await removeByVoice(intent.which, host), content: false }));
      return;
    case "action": {
      const quiet = intent.action.type === "stop_speaking" || intent.action.type === "scroll";
      stopSpeaking(); // a new command cuts off whatever was being said
      await local(heard, () => runAction(intent.action, host), quiet);
    }
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value;
  input.value = "";
  void handleInput(text);
});

if (!canListen()) {
  mic.disabled = true;
  mic.title = "Voice input needs Chrome or Edge";
  mic.setAttribute("aria-label", "Voice input needs Chrome or Edge");
}

mic.addEventListener("click", async () => {
  if (hf && hf.state() !== "off") {
    // Hands-free already has the microphone: a tap is the same as saying "Watchfloor".
    stopSpeaking();
    hf.wake();
    return;
  }
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
    if (heard) await handleInput(heard);
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

voice.addEventListener("change", () => update({ readAloud: voice.checked }));

// --- keyboard ---

const typing = (t: EventTarget | null): boolean =>
  t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));

const GO: Record<string, Route> = { w: "watch", p: "pipeline", o: "orders", a: "activity", g: "guide" };
let gAt = 0;

function focusComposer(): void {
  if (form.hidden) return;
  if (current() !== "watch") {
    wantFocus = true; // applied once the Watch page is showing
    navigate("watch");
  } else {
    input.focus();
  }
}

// --- hands-free: the microphone stays open and the page listens for "Watchfloor" ---

const FATAL_TEXT: Record<string, string> = {
  "not-allowed": "Microphone access is blocked. Allow it for this site in your browser's address bar, then turn hands-free on again.",
  "service-not-allowed": "The browser's speech service is blocked. Allow it for this site, then turn hands-free on again.",
  "audio-capture": "No microphone was found.",
  "language-not-supported": "This browser's speech recognition doesn't support your language.",
  unstable: "The speech service keeps disconnecting. Check your connection, then turn hands-free on again.",
};

const dock = mountDock({
  talk: () => {
    stopSpeaking();
    hf?.wake();
  },
  off: () => announce(setHandsFree(false)),
});

const hf: HandsFree | null = (() => {
  const ears = browserEars();
  if (!ears) return null;
  return createHandsFree({
    ears,
    clock: {
      now: () => performance.now(),
      setTimer: (fn, ms) => window.setTimeout(fn, ms),
      clearTimer: (id) => window.clearTimeout(id as number),
    },
    wakeRequired: () => settings().wakeWord,
    answering: () => dialogKind() === "confirm",
    onUtterance: (text) => {
      void handleInput(text)
        .catch((err: Error) => addLine("system", `Something went wrong: ${err.message}`, "error"))
        .finally(() => {
          if (!speechPending) settle();
        });
    },
    onState: (state, hint) => {
      dock.render(state, hint);
      const on = state !== "off";
      handsFreeButton.setAttribute("aria-pressed", String(on));
      handsFreeButton.setAttribute("aria-label", on ? "Stop hands-free listening" : "Start hands-free listening");
      // The orb listens while the microphone is open for a request.
      if (state === "awake" && voiceState() === "idle" && !listening) setVoice("listening");
      else if ((state === "waiting" || state === "off") && voiceState() === "listening" && !listening) setVoice("idle");
      if (!on) hearing.textContent = "";
    },
    onHeard: (text) => {
      dock.heard(text);
      hearing.textContent = text;
    },
    onWake: () => {
      stopSpeaking();
      earcon.listen();
    },
    onStopWord: () => {
      if (voiceState() === "speaking") stopSpeaking();
      else {
        muteNext = true; // a reply is on its way: show it, don't read it
        toast("Okay, I'll keep quiet about that one.");
      }
    },
    onAutoOff: () => announce("Hands-free turned itself off after a few quiet minutes."),
    onFatal: (reason) => toast(FATAL_TEXT[reason] ?? `Hands-free stopped (${reason}).`, "error"),
  });
})();

if (!hf) {
  handsFreeButton.disabled = true;
  handsFreeButton.title = "Hands-free needs Chrome or Edge";
  handsFreeButton.setAttribute("aria-label", "Hands-free needs Chrome or Edge");
}

/** Hands-free on or off; returns the sentence that says what happened. */
function setHandsFree(on: boolean): string {
  if (on) {
    if (!hf) return "Hands-free needs Chrome or Edge, which provide speech recognition.";
    if (hf.state() !== "off") return "Hands-free is already on.";
    stopListening();
    stopSpeaking();
    const wasMuted = !settings().readAloud;
    if (wasMuted) update({ readAloud: true }); // there is no hands-free without hearing the answers
    hf.start();
    const how = settings().wakeWord ? "Say “Watchfloor”, then your request." : "I'm listening.";
    return `Hands-free is on. ${how}${wasMuted ? " I turned read-aloud on so you can hear me." : ""}`;
  }
  if (!hf || hf.state() === "off") return "Hands-free is already off.";
  hf.stop();
  return "Hands-free is off.";
}

function toggleHandsFree(): void {
  const turnOn = !hf || hf.state() === "off";
  const text = setHandsFree(turnOn);
  if (!hf) toast(text, "error");
  else announce(text);
}

handsFreeButton.addEventListener("click", toggleHandsFree);
document.addEventListener("visibilitychange", () => (document.hidden ? hf?.pause() : hf?.resume()));
window.addEventListener("pagehide", () => hf?.stop());

const settingsDialog = mountSettings({
  handsFree: {
    available: hf !== null,
    active: () => hf !== null && hf.state() !== "off",
    set: (on) => announce(setHandsFree(on)),
  },
});
$<HTMLButtonElement>("open-settings").addEventListener("click", () => settingsDialog.open());

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    stopSpeaking();
    if (listening) stopListening();
    if (document.body.dataset.voice === "speaking") setVoice("idle");
    return;
  }
  if (e.ctrlKey || e.metaKey || e.altKey || typing(e.target) || document.querySelector("dialog[open]")) return;
  // "g" then a letter jumps between pages, as in most keyboard-first tools.
  if (gAt && Date.now() - gAt < 1000) {
    gAt = 0;
    const route = GO[e.key.toLowerCase()];
    if (route) {
      e.preventDefault();
      navigate(route);
      return;
    }
  }
  if (e.key === "g") {
    gAt = Date.now();
  } else if (e.key === "/") {
    // "/" jumps to the message box, as in most search-first tools.
    if (!form.hidden) {
      e.preventDefault();
      focusComposer();
    }
  } else if (e.key === "?") {
    e.preventDefault();
    navigate("guide");
  } else if (e.key === ",") {
    e.preventDefault();
    settingsDialog.open();
  } else if (e.key === "h") {
    e.preventDefault();
    toggleHandsFree();
  }
});

// Device preview and theme: radio groups (arrow keys move between options, as WAI-ARIA radios do).
function radioGroup(
  selector: string,
  read: (b: HTMLButtonElement) => string,
  onChoose: (value: string) => void,
): { show(value: string): void } {
  const buttons = [...document.querySelectorAll<HTMLButtonElement>(selector)];
  const show = (value: string): void => {
    for (const b of buttons) {
      const on = read(b) === value;
      b.setAttribute("aria-checked", String(on));
      b.tabIndex = on ? 0 : -1;
    }
  };
  buttons.forEach((b, i) => {
    b.addEventListener("click", () => {
      show(read(b));
      onChoose(read(b));
    });
    b.addEventListener("keydown", (e) => {
      const forward = e.key === "ArrowRight" || e.key === "ArrowDown";
      const back = e.key === "ArrowLeft" || e.key === "ArrowUp";
      if (!forward && !back) return;
      e.preventDefault();
      const next = buttons[(i + (forward ? 1 : -1) + buttons.length) % buttons.length];
      show(read(next));
      onChoose(read(next));
      next.focus();
    });
  });
  return { show };
}

const deviceRadio = radioGroup(
  ".device [data-device]",
  (b) => b.dataset.device ?? "fit",
  (v) => update({ device: v as DeviceChoice }),
);

const THEME_ICON: Record<ThemeChoice, string> = { auto: ICONS.auto, light: ICONS.sun, dark: ICONS.moon };
for (const b of document.querySelectorAll<HTMLButtonElement>(".theme [data-theme-choice]")) {
  b.innerHTML = THEME_ICON[b.dataset.themeChoice as ThemeChoice];
}
const themeRadio = radioGroup(
  ".theme [data-theme-choice]",
  (b) => b.dataset.themeChoice ?? "auto",
  (v) => update({ theme: v as ThemeChoice }),
);

// --- the 3D orb ---

const orb3d = mountOrb3d($<HTMLCanvasElement>("orb3d"));

function applyOrb(): void {
  if (screen) screen.dataset.orb = orb3d && settings().orb3d ? "3d" : "css";
}

// --- the 3D floor behind the page ---

const scene = mountScene($<HTMLCanvasElement>("scene"));

function applyScene(): void {
  document.body.dataset.scene = scene && settings().scene ? "on" : "off";
}

// --- settings: applied at start, and again whenever one changes ---

function applyMotion(): void {
  document.documentElement.dataset.motion = settings().motion;
}

onSettings((s, changed) => {
  if (changed.includes("theme")) {
    setTheme(s.theme);
    themeRadio.show(s.theme);
    orb3d?.refresh();
  }
  if (changed.includes("device")) {
    setDevice(s.device);
    deviceRadio.show(s.device);
  }
  if (changed.includes("readAloud")) {
    voice.checked = s.readAloud;
    setSounds(s.readAloud);
    if (!s.readAloud) stopSpeaking();
  }
  if (changed.includes("motion")) {
    applyMotion();
    orb3d?.refresh();
    scene?.refresh();
  }
  if (changed.includes("orb3d")) applyOrb();
  if (changed.includes("scene")) {
    applyScene();
    scene?.refresh();
  }
  if (changed.includes("name")) updateGreeting();
  if (changed.includes("wakeWord")) hf?.refresh();
});

{
  const s = settings();
  setTheme(s.theme);
  themeRadio.show(s.theme);
  setDevice(s.device);
  deviceRadio.show(s.device);
  voice.checked = s.readAloud;
  setSounds(s.readAloud);
  applyMotion();
  applyOrb();
  applyScene();
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
window.addEventListener("wf:signedout", handleSignedOut);

/** Everything the Watch page holds from a conversation: the lines, the trace, the open view. */
async function resetConversation(): Promise<void> {
  history.length = 0;
  lastReply = "";
  transcript.replaceChildren();
  clearTrace(calls);
  await clearView(viewBox);
  suggestions.hidden = engine === null;
  renderSuggestions();
  updateIdle();
}

/** Sign out. False (with a message in the transcript) when the server could not be told. */
async function signOutNow(): Promise<boolean> {
  stopSpeaking();
  try {
    await logout();
  } catch (err) {
    // The session cookie may still be set, so the page must not pretend it signed out.
    addLine("system", `Couldn't sign out (${(err as Error).message}). Check your connection and try again.`, "error");
    return false;
  }
  await resetConversation();
  clearActivity();
  setVoice("idle");
  setSignedIn(false, null);
  return true;
}

logoutButton.addEventListener("click", () => void signOutNow());

// Signing in leaves the page; remember where the user was so they come back to it.
document.addEventListener("click", (e) => {
  if (!(e.target instanceof Element) || !e.target.closest('a[href="/oauth/login"]')) return;
  try {
    sessionStorage.setItem("wf.return", current());
  } catch {
    // Without session storage the user simply lands on Watch.
  }
});

// The sticky header's height, so the conversation rail can sit just beneath it.
const chrome = document.querySelector<HTMLElement>(".chrome");
if (chrome) {
  const measure = (): void => document.documentElement.style.setProperty("--chrome-h", `${chrome.offsetHeight}px`);
  new ResizeObserver(measure).observe(chrome);
  measure();
}

// --- pages ---

const pipeline = mountPipeline(page("pipeline"), { ask, report, signedOut: handleSignedOut, briefing: briefingSeen });
const orders = mountOrders(page("orders"), { ask, report, signedOut: handleSignedOut });
const activity = mountActivity(page("activity"), { toWatch: () => navigate("watch") });
const guide = mountGuide(page("guide"), {
  ask,
  engine: () => engine,
  openSettings: () => settingsDialog.open(),
});

// What voice (and the assistant's control_app tool) does to the page, one handle per page.
const host: Host = {
  page: current,
  go(next) {
    for (const d of document.querySelectorAll<HTMLDialogElement>("dialog[open]")) d.close();
    navigate(next);
  },
  dialog: dialogKind,
  closeDialog: () => document.querySelector<HTMLDialogElement>("dialog[open]")?.close(),
  openSettings: () => settingsDialog.open(),
  settings,
  update,
  voices() {
    const all = voices();
    const here = (navigator.language || "en").slice(0, 2).toLowerCase();
    const mine = all.filter((v) => v.lang.toLowerCase().startsWith(here));
    return [...mine, ...all.filter((v) => !mine.includes(v))];
  },
  pipeline,
  orders,
  activity,
  guide,
  watchSummary: () =>
    lastReply
      ? `The last thing I said: ${lastReply}`
      : "Nothing has been said yet. Ask about your pipeline, or ask: what can I say?",
  lastReply: () => lastReply,
  clearConversation: () => void resetConversation(),
  signedIn: () => engine !== null,
  async signOut() {
    if (!(await signOutNow())) throw new Error("the server could not be reached");
  },
  forgetData: forgetEverything,
  clearActivity,
  pinsChanged() {
    updateIdle();
    renderSuggestions();
  },
  setHandsFree,
  scroll(direction) {
    const behavior = reducedMotion() ? "auto" : "smooth";
    if (direction === "top") window.scrollTo({ top: 0, behavior });
    else if (direction === "bottom") window.scrollTo({ top: document.documentElement.scrollHeight, behavior });
    else window.scrollBy({ top: (direction === "down" ? 1 : -1) * Math.round(window.innerHeight * 0.8), behavior });
  },
  confirm(options, yes) {
    void confirmDialog(options).then(async (ok) => {
      if (!ok) {
        announce("Okay, I won't.");
        return;
      }
      try {
        announce(await yes());
      } catch (err) {
        announce(`That didn't work: ${(err as Error).message}`);
      }
    });
  },
  announce,
};

onRoute((route) => {
  pipeline.setActive(route === "pipeline");
  orders.setActive(route === "orders");
  if (route === "activity") activity.refresh();
  if (route === "guide") guide.refresh();
  if (route === "watch") {
    updateIdle();
    renderSuggestions();
    // Not while a turn is in flight: its reply isn't in the history yet, but the conversation has begun.
    suggestions.hidden = engine === null || history.length > 0 || busy;
    transcript.lastElementChild?.scrollIntoView({ block: "end" });
    if (wantFocus && !form.hidden) input.focus();
  }
  wantFocus = false;
});

async function start(): Promise<void> {
  renderBanner($("banner"));
  renderSuggestions();
  setVoice("idle");
  updateIdle();
  window.setInterval(updateGreeting, 5 * 60_000);
  initRouter();
  const params = new URLSearchParams(location.search);
  const error = params.get("signin");
  if (error) {
    // The value comes from the URL, so a link can carry any text: show only what we wrote for known codes.
    addLine(
      "system",
      Object.hasOwn(SIGNIN_ERRORS, error) ? SIGNIN_ERRORS[error]! : "The sign-in could not be completed. Please try again.",
      "error",
    );
    window.history.replaceState(null, "", location.pathname + location.hash);
  }
  try {
    const info = await session();
    setSignedIn(info.signed_in, info.engine);
    if (info.signed_in) {
      if (!transcript.children.length) addLine("system", "Signed in · ask anything, or pick a suggestion");
      try {
        const back = sessionStorage.getItem("wf.return");
        sessionStorage.removeItem("wf.return");
        const route = back ? parseHash(`#/${back}`) : null;
        // Back to the page the user left to sign in, unless they've since navigated somewhere.
        if (route && route !== "watch" && !location.hash.replace(/^#\/?/, "")) navigate(route);
      } catch {
        // No session storage: stay on Watch.
      }
    }
  } catch (err) {
    setSignedIn(false, null);
    setVoice("error", "Harness API unreachable");
    addLine("system", `The harness API is not reachable: ${(err as Error).message}`, "error");
  }
}

void start();
