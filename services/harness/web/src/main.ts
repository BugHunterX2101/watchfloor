// The app shell: the chat loop (text or voice in, the harness turn, the spoken reply, the view and
// the trace) and the pages around it (Pipeline, Orders, Activity, Guide), the plot on the Watch
// screen, ship's time in the top bar, and what the page remembers about this viewer. Pages are
// modules with a mount function; this file connects them to the conversation and to each other.

// Schibsted Grotesk for the interface, Newsreader (optical sizes) for the assistant's voice and the
// big lines, Martian Mono for instrument readings. All bundled: the page works offline.
import "@fontsource-variable/schibsted-grotesk";
import "@fontsource-variable/martian-mono/standard.css"; // weight and width axes
import "@fontsource-variable/newsreader/opsz.css";
import "./styles.css";
import { clearActivity, mountActivity, recordTurn } from "./activity";
import {
  callTool,
  converse,
  dismiss,
  logout,
  session,
  SignedOut,
  type Awaiting,
  type Call,
  type Line,
  type Screen,
} from "./api";
import { clearView, renderView, setDevice, setTheme } from "./apphost";
import type { Backdrop } from "./backdrop";
import { renderBanner } from "./banner";
import { caption } from "./captions";
import { confirmDialog, h, toast } from "./dom";
import { mountGuide } from "./guide";
import { ICONS } from "./icons";
import {
  BAND_LABEL,
  BAND_ORDER,
  bellWords,
  boardTotals,
  greeting,
  promptSafe,
  relativeTime,
  shipsWatch,
  splitSummary,
  startersFor,
  toBriefing,
  type Briefing,
  type PlotDeal,
} from "./logic";
import { beat, setVoice, voiceState } from "./orb";
import { mountOrders } from "./orders";
import { mountPipeline } from "./pipeline";
import { mountPlot } from "./plot";
import { current, initRouter, navigate, onRoute, parseHash, setBadge, type Route } from "./router";
import { mountSettings } from "./settings";
import { earcon, earconFor, setSounds } from "./sound";
import {
  browserEars,
  canListen,
  listenOnce,
  setCloudVoice,
  setHeardChooser,
  speak,
  stopListening,
  stopSpeaking,
  voices,
} from "./speech";
import {
  clearPlot,
  forgetEverything,
  learn,
  lessons,
  loadPlot,
  onSettings,
  pinnedLabels,
  recents,
  rememberAsk,
  savePlot,
  settings,
  update,
  type DeviceChoice,
  type ThemeChoice,
} from "./store";
import { clearTrace, pendingTrace, showTrace } from "./trace";
import { NEEDS_YES, type AppAction } from "./voice/actions";
import { mountTilt } from "./tilt";
import { removeByVoice, run as runAction, type Host, type Outcome } from "./voice/commander";
import { mountDock } from "./voice/dock";
import { boardOf, profileOf } from "./voice/context";
import { helpText, interpretAll, isAnswer, pickHeard, stripWake, type Context, type Intent } from "./voice/grammar";
import { createHandsFree, type HandsFree } from "./voice/handsfree";
import { applyLessons } from "./voice/lessons";
import { asksSomething, speakable } from "./voice/text";
import { compare, snapshot, type Snapshot } from "./voice/watcher";

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
const idleTitle = $<HTMLParagraphElement>("idle-title");
const idleSub = $<HTMLParagraphElement>("idle-sub");
const plotStamp = $<HTMLParagraphElement>("plot-stamp");
const plotLoad = $<HTMLButtonElement>("plot-load");
const clock = $<HTMLParagraphElement>("clock");
const page = (name: Route): HTMLElement => {
  const el = document.querySelector<HTMLElement>(`[data-page="${name}"]`);
  if (!el) throw new Error(`page ${name} is missing`);
  return el;
};

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
const watchedReviews = new Map<string, number>(); // deal id → the timer of the next check on its deep review
let waiting: Awaiting | null = null; // a change just read back (or held) that a plain "no" drops
let utterances = 0; // requests handed over by hands-free so far, so a late finish can tell it was overtaken
const WATCH_MS = 60_000; // how often hands-free looks at the board while it is on
let watchTimer: number | null = null;
let watchBusy = false;
let baseline: Snapshot | null = null; // the board as it was at the last look; null until the first
const told = new Map<string, number>(); // news key → when it was last said (voice/watcher.ts)
let latest: { briefing: Briefing; at: number } | null = null; // the last briefing seen this visit, from any path
const STALE_MS = 5 * 60_000; // a plot older than this is taken again when Watch comes back into view

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

/** "The scope shows 5 of the 7 that need you: 2 at risk · 3 on watch, across 4 stages.": the plot in
 *  words, beside it. The briefing's own summary counts every deal that needs you; the scope only
 *  the ones the briefing listed, so the line says which. */
function plotWords(b: Briefing): string {
  const by = boardTotals(b.deals).byBand;
  const parts = BAND_ORDER.filter((band) => by[band] > 0).map((band) =>
    band === "watch" ? `${by[band]} on watch` : `${by[band]} ${BAND_LABEL[band].toLowerCase()}`,
  );
  const stages = new Set(b.deals.map((d) => d.stage).filter(Boolean)).size;
  if (!parts.length) return "No deals in this briefing.";
  const needing = b.deals.filter((d) => d.band !== "healthy").length;
  const unseen = b.attention_count - needing;
  const lead = unseen > 0 && needing > 0 ? `The scope shows ${needing} of the ${b.attention_count} that need you: ` : "";
  return `${lead}${parts.join(" · ")}${stages > 1 ? `, across ${stages} stages` : ""}. Nearest the centre needs you most.`;
}

/** What the screen says beside the plot: the live board when there is one, else how to get it. */
function renderPlotState(): void {
  const live = engine !== null ? latest : null;
  if (live) {
    // The first sentence is the headline; the rest of the summary and the plot in words sit under it.
    const { head, rest } = splitSummary(live.briefing.summary || (live.briefing.all_clear ? "All clear." : "Your pipeline."));
    idleTitle.textContent = head;
    idleSub.textContent = [rest, plotWords(live.briefing)].filter(Boolean).join(" ");
    plotStamp.textContent = `Bearing taken ${relativeTime(live.at, Date.now())}`;
    plotLoad.textContent = "Take a fresh bearing";
  } else {
    idleTitle.textContent = "Ask about your pipeline.";
    idleSub.textContent =
      engine === null
        ? "Sign in, then speak or type. Your deals appear on this scope: the nearer the centre, the more a deal needs you."
        : plot.count()
          ? "The scope shows your board as you last saw it, without names. Ask anything, or take a bearing for the live board."
          : "Speak or type, or take a bearing to put your live board on the scope.";
    plotStamp.textContent = "";
    plotLoad.textContent = "Take a bearing";
  }
  plotLoad.hidden = engine === null;
}

/** Load the live board onto the plot. It counts as a briefing, the same as asking. */
async function takeBearing(): Promise<void> {
  if (engine === null) return;
  plotLoad.disabled = true;
  plotStamp.textContent = "Taking a bearing…";
  try {
    await pipeline.refresh();
  } finally {
    plotLoad.disabled = false;
    renderPlotState();
  }
}
plotLoad.title = "Loads the live board. Counts as a briefing, the same as asking.";
plotLoad.addEventListener("click", () => void takeBearing());

/** Ship's time in the top bar: the watch, the bells struck in it (as pips in pairs), the hour. */
function tickClock(): void {
  const now = new Date();
  const { watch, bells } = shipsWatch(now.getHours(), now.getMinutes());
  const time = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const name = clock.querySelector(".clock-watch");
  const pips = clock.querySelector(".clock-bells");
  const at = clock.querySelector<HTMLTimeElement>(".clock-time");
  if (name) name.replaceChildren(watch, h("span", { class: "sr-only" }, `, ${bellWords(bells)}, `));
  if (pips)
    pips.replaceChildren(
      ...Array.from({ length: 8 }, (_, i) => h("i", { class: i < bells ? "on" : "", "data-pair": String(Math.floor(i / 2)) })),
    );
  if (at) {
    at.textContent = time;
    at.dateTime = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
  }
  clock.title = `${watch}, ${bellWords(bells)}. Bells are struck each half hour, eight to end a watch.`;
}

function updateIdle(): void {
  updateGreeting();
  renderPlotState();
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
  // Made from the board as it is (logic.ts), in phrasings the offline router understands too.
  for (const text of startersFor(latest?.briefing ?? null)) add(text);
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
    if (!latest) plot.set(loadPlot()); // the scope shows the board's shape as it was last seen
  } else {
    hf?.stop(); // nobody is signed in: the microphone must not stay open for them
    for (const timer of watchedReviews.values()) window.clearTimeout(timer);
    watchedReviews.clear();
    setBadge("pipeline", null);
    latest = null;
    plot.set([]);
    clearPlot(); // a shared browser must not show the last person's pipeline
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

/** A request is finished and nothing more will be said: hands-free listens for a follow-up
 *  (`asked`: the reply ended on a question, so it waits longer for the answer). */
function settle(asked = false): void {
  if (hf?.state() === "busy") hf.done(asked);
}

/** Show a reply's words on the dock and read it aloud (if reading aloud is on), telling hands-free
 *  when the assistant starts and stops talking so the microphone knows what it is hearing.
 *  `shown` is the line's text; `spoken` can carry more (a page read out after the answer). */
function deliver(line: HTMLElement | null, shown: string, spoken: string = shown): void {
  dock.said(shown);
  // A reply that ends on a question (or leaves a yes-or-no on the screen) is waiting for an answer.
  const asked = asksSomething(shown) || asksSomething(spoken) || dialogKind() === "confirm";
  if (muteNext) {
    muteNext = false;
    setVoice("idle");
    settle(asked);
    return;
  }
  const say = speakable(spoken);
  if (!settings().readAloud || !say) {
    setVoice("idle");
    settle(asked);
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
      hf?.spoke(asked);
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
  // Someone talking to a hands-free assistant may not be looking: a wait that long is also said, once
  // each (not by "stop" words, so the microphone cannot mistake it for an interruption).
  const aloud: [number, string][] = [
    [6000, "Still working on that."],
    [14000, "This one's taking a little longer."],
  ];
  const spoken = aloud.map(([ms, text]) =>
    window.setTimeout(() => {
      if (hf && hf.state() !== "off" && settings().readAloud && !muteNext) {
        hf.noteSpoken(text); // its echo in the microphone is not the user speaking up
        speak(text);
      }
    }, ms),
  );
  return () => [...timers, ...spoken].forEach((t) => window.clearTimeout(t));
}

/** A call made outside the chat (a view's tap, or a page): it lands in the trace and in Activity,
 *  and an action that ran or was undone gets its earcon, the same as when it was asked by voice. */
function report(call: Call, source: string): void {
  showTrace(calls, [call], call.ms, source);
  recordTurn(source, [call], call.ms);
  earconFor(call.tool, call.ok);
}

const viewCall = (call: Call): void => report(call, "View");

/** A briefing was shown or loaded (asked for, on the board, or looked at by hands-free): the
 *  pipeline tab's badge, the plot, the words beside it and the suggestions all follow it. */
function briefingSeen(b: Briefing): void {
  setBadge("pipeline", b.attention_count);
  latest = { briefing: b, at: Date.now() };
  const deals: PlotDeal[] = b.deals.map((d) => ({
    id: d.deal_id,
    band: d.band,
    health: d.health,
    account: d.account,
    stage: d.stage,
    amount_cents: d.amount_cents,
  }));
  plot.set(deals);
  savePlot(deals);
  renderPlotState();
  renderSuggestions();
}

function noteBriefing(data: string): void {
  try {
    const briefing = toBriefing(JSON.parse(data));
    if (briefing) briefingSeen(briefing);
  } catch {
    // Not JSON: the view still renders; only the badge is skipped.
  }
}

// A deep review takes about twenty seconds. The assistant says it has started; the page then checks
// on it and tells the user when it is ready, so nobody has to remember to ask (and a hands-free user
// never has to look).
const REVIEW_CHECK_MS = 4000;
const REVIEW_GIVE_UP_MS = 120_000;

function watchReview(dealId: string): void {
  if (watchedReviews.has(dealId)) return;
  const until = Date.now() + REVIEW_GIVE_UP_MS;
  const check = async (): Promise<void> => {
    let again = false;
    try {
      if (engine === null) return;
      const result = await callTool("get_triage_result", { deal_id: dealId });
      const status = result.structuredContent?.status;
      if (status === "queued" || status === "running") {
        again = Date.now() < until; // too long: the user can still ask
        return;
      }
      report(result.call, "Review");
      const summary = result.structuredContent?.summary;
      if (typeof summary === "string" && summary) tellWhenFree(summary);
    } catch (err) {
      if (err instanceof SignedOut) handleSignedOut();
      // Any other failure: the user can still ask for the result.
    } finally {
      // The entry stays while a check is in flight, so a second request for the same deal adds no second watcher.
      if (again && engine !== null) watchedReviews.set(dealId, window.setTimeout(() => void check(), REVIEW_CHECK_MS));
      else watchedReviews.delete(dealId);
    }
  };
  watchedReviews.set(dealId, window.setTimeout(() => void check(), REVIEW_CHECK_MS));
}

/** Say a finished review once the assistant is not in the middle of something else. Spoken only when
 *  hands-free is on (it is the voice-first mode); otherwise it appears in the transcript and as a toast. */
function tellWhenFree(text: string, tries = 0): void {
  if (engine === null) return;
  const handsFree = hf !== null && hf.state() !== "off";
  const occupied =
    busy || listening || voiceState() === "speaking" || (handsFree && (hf?.state() === "busy" || hf?.state() === "speaking"));
  if (occupied && tries < 20) {
    window.setTimeout(() => tellWhenFree(text, tries + 1), 1500);
    return;
  }
  history.push({ role: "assistant", text }); // "yes, set that up" must mean the review that was just read out
  if (handsFree) {
    announce(text);
  } else {
    addLine("assistant", text);
    toast(text);
  }
}

const CONFIRMATION = /^\s*(?:yes|yeah|yep|sure|ok|okay|no|nope|undo|thanks?)\b/i;

/** A change is waiting for the user's answer only while the question just asked is about it. */
function liveWaiting(): Awaiting | null {
  return waiting && (waiting.kind === "preview" || waiting.kind === "ask") && waiting.until * 1000 > Date.now()
    ? waiting
    : null;
}

async function send(message: string, opts: { unheard?: boolean } = {}): Promise<void> {
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
    const turn = await converse(text, history, {
      context: screenState(),
      profile: profileOf(settings(), pinnedLabels(), new Date(), lessons()),
      unheard: opts.unheard,
    });
    waiting = turn.awaiting?.fresh ? turn.awaiting : null;
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
    // A standing order the assistant saved or removed shows on the Orders page at once, so a spoken "remove the
    // first rule" next is about what is really there.
    if (turn.calls.some((c) => c.ok && (c.tool === "set_standing_order" || c.tool === "remove_standing_order"))) {
      orders.reload();
    }
    for (const call of turn.calls) {
      if (call.tool === "start_deep_triage" && call.ok && typeof call.arguments.deal_id === "string") {
        watchReview(call.arguments.deal_id);
      }
    }
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
    if (muteNext && liveWaiting()) {
      // "Stop" or "cancel" was said while this was being worked on: a change they never heard is not kept waiting.
      waiting = null;
      await dismiss().catch(() => undefined);
      addLine("system", "Dropped that change because you said stop. Nothing was changed.");
    }
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

/** The dialog on top: a confirmation can be asked over Settings or a deal, and it is the one being answered. */
const topDialog = (): HTMLDialogElement | null => [...document.querySelectorAll<HTMLDialogElement>("dialog[open]")].at(-1) ?? null;

function dialogKind(): "none" | "settings" | "deal" | "confirm" {
  const open = topDialog();
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
    board: pipeline.shown().map((d) => d.deal_id),
    rules: orders.rules().map((r) => ({ id: r.rule_id, description: r.description })),
    openDealId: pipeline.openDealId(),
    awaiting: liveWaiting() !== null,
  };
}

// Of the recognizer's guesses, the one that is a command or names a deal on the page.
setHeardChooser((alternatives) => pickHeard(alternatives, grammarContext()));

/** What the assistant is told about the screen with every message (fixed words only). */
function screenState(): Screen {
  const view = pipeline.view();
  const open = pipeline.openDealId();
  const board = boardOf(pipeline.shown());
  return {
    ...(open && /^[A-Za-z0-9_-]{1,64}$/.test(open) ? { open_deal: open } : {}),
    ...(board.length ? { board } : {}),
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

/** "No" to a change the assistant is waiting on: the server forgets it, so a later yes cannot confirm it. */
async function declineWaiting(said: string): Promise<void> {
  const what = liveWaiting();
  waiting = null;
  const reply = what?.kind === "ask" ? "Okay, I won't save it." : "Okay, dropped. Nothing was changed.";
  await local(said, async () => {
    await dismiss(); // a SignedOut here is handled by local()
    history.push({ role: "user", text: said }, { role: "assistant", text: reply });
    return { say: reply, content: false };
  });
}

/** Several commands in one sentence, run in order, answered in one breath. */
async function runTogether(said: string, intents: Intent[]): Promise<void> {
  stopSpeaking();
  // "Only at-risk, biggest first" is one change to the board, answered once (not one sentence per half).
  const acts: AppAction[] = [];
  for (const i of intents) {
    if (i.kind !== "action") continue;
    const prev = acts.at(-1);
    if (prev?.type === "pipeline_view" && i.action.type === "pipeline_view") acts[acts.length - 1] = { ...prev, ...i.action };
    else acts.push(i.action);
  }
  await local(said, async () => {
    const parts: string[] = [];
    let spoken = false;
    for (const act of acts) {
      const out = await runAction(act, host);
      if (out.say) parts.push(out.say);
      spoken ||= out.content;
    }
    return { say: parts.join(" "), content: spoken };
  });
}

async function handleInput(raw: string, opts: { unheard?: boolean } = {}): Promise<void> {
  // A mishearing the user corrected before is put right before anything reads the words, and the
  // transcript says so: the page never quietly acts on words the user did not say.
  const fixed = applyLessons(raw.trim(), lessons());
  const text = fixed.text.trim();
  if (!text) return;
  if (fixed.used.length) {
    const what = fixed.used.map((l) => `“${l.heard}” as ${l.meant}`).join(", ");
    addLine("system", `Taking ${what}, as you taught me. Settings lists what I've learned.`);
  }
  const intents = interpretAll(text, grammarContext());
  const intent = intents[0];
  const heard = stripWake(text).text || text;
  if (intents.length > 1) {
    await runTogether(heard, intents);
    return;
  }
  switch (intent.kind) {
    case "model":
      await send(intent.text || text, opts);
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
      if (button) button.click();
      else if (intent.kind === "no" && liveWaiting()) await declineWaiting(heard);
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
      await local(heard, async () => ({ say: helpText(current()), content: true }));
      return;
    case "rest":
      stopSpeaking();
      // "That's all": answered, and then the wake word is needed again (always-listening mode stays as it is).
      await local(heard, async () => ({
        say: hf?.rest() ? "Okay. Say Watchfloor when you need me." : "Okay.",
        content: false,
      }));
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
    answering: () => dialogKind() === "confirm" || liveWaiting() !== null,
    canQueue: (text) => !isAnswer(text),
    onUtterance: (text, opts) => {
      const mine = ++utterances;
      void handleInput(text, opts)
        .catch((err: Error) => addLine("system", `Something went wrong: ${err.message}`, "error"))
        .finally(() => {
          // Not when a request said meanwhile has already taken over: it settles itself.
          if (!speechPending && mine === utterances) settle();
        });
    },
    onState: (state, hint) => {
      dock.render(state, hint);
      const on = state !== "off";
      if (on) startWatching();
      else stopWatching();
      handsFreeButton.setAttribute("aria-pressed", String(on));
      handsFreeButton.setAttribute("aria-label", on ? "Stop hands-free listening" : "Start hands-free listening");
      // The lamp listens while the microphone is open for a request.
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
    onTalkOver: () => stopSpeaking(),
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

// --- keeping an eye on the board while hands-free is on ---

/** One look at the board. The first look is the baseline; after that, anything new and worth saying is
 *  said once the assistant is free (voice/watcher.ts decides what is worth it). */
async function watchBoard(): Promise<void> {
  if (engine === null || document.hidden || !settings().alerts || !hf || hf.state() === "off" || watchBusy) return;
  watchBusy = true;
  try {
    const r = await callTool("get_risk_briefing", { limit: 5 });
    const b = r.isError ? null : toBriefing(r.structuredContent);
    if (!b) return;
    briefingSeen(b); // the tab's badge and the plot stay current too
    const next = snapshot(b);
    const prev = baseline;
    baseline = next;
    if (!prev) return;
    const news = compare(prev, next, told, Date.now());
    if (!news) return;
    for (const key of news.keys) told.set(key, Date.now());
    report(r.call, "Watching"); // only a look that led to something being said is logged
    tellWhenFree(news.say);
  } catch (err) {
    if (err instanceof SignedOut) handleSignedOut();
    // Any other failure: try again at the next look.
  } finally {
    watchBusy = false;
  }
}

function startWatching(): void {
  if (watchTimer !== null) return;
  baseline = null;
  watchTimer = window.setInterval(() => void watchBoard(), WATCH_MS);
  void watchBoard();
}

function stopWatching(): void {
  if (watchTimer !== null) window.clearInterval(watchTimer);
  watchTimer = null;
  baseline = null;
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
    const watching = settings().alerts ? " I'll tell you if the board changes." : "";
    return `${greeting(new Date().getHours(), settings().name)} Hands-free is on. ${how}${watching}${wasMuted ? " I turned read-aloud on so you can hear me." : ""}`;
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
  // A held key repeats: "h" would otherwise flip hands-free on and off for as long as it is down.
  if (e.repeat || e.ctrlKey || e.metaKey || e.altKey || typing(e.target) || document.querySelector("dialog[open]")) return;
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

// --- the chart behind the pages (ADR-035): loaded once the page has painted. Until then, and
// wherever WebGL is missing, the CSS chart room (.atmosphere) stands in. ---

let backdrop: Backdrop | null = null;

function startBackdrop(): void {
  const host = document.querySelector<HTMLElement>(".atmosphere");
  if (!host) return;
  import("./backdrop")
    .then(({ mountBackdrop }) => {
      backdrop = mountBackdrop(host);
      backdrop.setEnabled(settings().backdrop);
    })
    .catch(() => {
      // The chunk did not arrive (offline after an update): the CSS chart room stays.
    });
}

if ("requestIdleCallback" in window) requestIdleCallback(startBackdrop, { timeout: 1500 });
else setTimeout(startBackdrop, 300);
mountTilt();

// --- the plot's two moving parts, each with its own switch (Calm and reduced motion stop both) ---

function applyLamp(): void {
  document.body.dataset.lamp = settings().orb3d ? "on" : "off";
}

function applySweep(): void {
  document.body.dataset.sweep = settings().scene ? "on" : "off";
  requestAnimationFrame(() => plot.sync());
}

// --- settings: applied at start, and again whenever one changes ---

function applyMotion(): void {
  document.documentElement.dataset.motion = settings().motion;
}

onSettings((s, changed) => {
  if (changed.includes("theme")) {
    setTheme(s.theme);
    themeRadio.show(s.theme);
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
    requestAnimationFrame(() => plot.sync());
  }
  if (changed.includes("orb3d")) applyLamp();
  if (changed.includes("scene")) applySweep();
  if (changed.includes("backdrop")) backdrop?.setEnabled(s.backdrop);
  if (changed.includes("name")) updateGreeting();
  if (changed.includes("wakeWord")) hf?.refresh();
  if (changed.includes("alerts")) baseline = null; // a fresh look, so what changed while it was off is not "news"
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
  applyLamp();
  applySweep();
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
const orders = mountOrders(page("orders"), {
  ask,
  report,
  signedOut: handleSignedOut,
  deals: () => latest?.briefing.deals ?? [],
});
const activity = mountActivity(page("activity"), { toWatch: () => navigate("watch") });
const guide = mountGuide(page("guide"), {
  ask,
  engine: () => engine,
  openSettings: () => settingsDialog.open(),
  deals: () => latest?.briefing.deals ?? [],
});

// The plot on the Watch screen: choosing a contact opens that deal's explanation, as the board does.
const plot = mountPlot($<HTMLDivElement>("plot"), {
  label:
    "Your pipeline on a scope. Each deal is a contact: the nearer the centre, the lower its health; its bearing is its stage; its size is its amount.",
  pick: (id, account) =>
    void pipeline.open({ deal_id: id, deal_name: account }).then((said) => {
      if (!said.startsWith("Opening")) toast(said, "error");
    }),
});

// What voice (and the assistant's control_app tool) does to the page, one handle per page.
const host: Host = {
  page: current,
  go(next) {
    for (const d of document.querySelectorAll<HTMLDialogElement>("dialog[open]")) d.close();
    navigate(next);
  },
  dialog: dialogKind,
  closeDialog: () => topDialog()?.close(),
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
  learn(heard, meant) {
    const ok = learn({ heard, meant });
    if (ok) addLine("system", `Learned: “${heard}” means ${meant}. You can remove it in Settings.`);
    return ok;
  },
};

onRoute((route) => {
  pipeline.setActive(route === "pipeline");
  orders.setActive(route === "orders");
  if (route === "activity") activity.refresh();
  if (route === "guide") guide.refresh();
  if (route === "watch") {
    updateIdle();
    renderSuggestions();
    // Hidden pages restart their animations together; put the echoes back on the sweep's clock.
    requestAnimationFrame(() => plot.sync());
    // A plot older than five minutes is taken again (only once one was taken this visit, so simply
    // opening the page never counts as a briefing on its own).
    if (engine !== null && latest && !busy && Date.now() - latest.at > STALE_MS) void takeBearing();
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
  tickClock();
  // Every 15 seconds: the clock, the greeting, and how long ago the bearing was taken.
  window.setInterval(() => {
    tickClock();
    updateGreeting();
    if (latest) renderPlotState();
  }, 15_000);
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
    setCloudVoice(info.signed_in && info.voice === "polly");
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
