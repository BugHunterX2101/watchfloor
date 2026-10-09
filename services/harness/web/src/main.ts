// The chat loop: text or voice in, the harness turn, the spoken reply, the view and the latency drawer.

import "./styles.css";
import { converse, logout, session, SignedOut, type Call, type Line } from "./api";
import { clearView, renderView } from "./apphost";
import { renderBanner } from "./banner";
import { canListen, listenOnce, speak, stopSpeaking } from "./speech";

const $ = <T extends HTMLElement>(id: string): T => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`#${id} is missing`);
  return el as T;
};

const transcript = $<HTMLOListElement>("transcript");
const form = $<HTMLFormElement>("composer");
const input = $<HTMLInputElement>("message");
const mic = $<HTMLButtonElement>("mic");
const voice = $<HTMLInputElement>("voice");
const signin = $<HTMLDivElement>("signin");
const engineLabel = $<HTMLSpanElement>("engine");
const logoutButton = $<HTMLButtonElement>("logout");
const viewBox = $<HTMLDivElement>("view");
const calls = $<HTMLOListElement>("calls");

const history: Line[] = [];
let busy = false;

const SIGNIN_ERRORS: Record<string, string> = {
  access_denied: "Sign-in was cancelled.",
  state_mismatch: "That sign-in link expired. Please try again.",
  issuer_mismatch: "The sign-in response came from an unexpected server.",
  token_error: "The sign-in could not be completed. Please try again.",
};

function addLine(kind: "user" | "assistant" | "system", text: string): HTMLLIElement {
  const li = document.createElement("li");
  li.className = `line ${kind}`;
  li.textContent = text;
  transcript.append(li);
  li.scrollIntoView({ block: "end", behavior: "smooth" });
  return li;
}

function showCalls(list: Call[], totalMs: number, source = "Turn"): void {
  const turn = document.createElement("li");
  turn.className = "turn";
  const head = document.createElement("div");
  head.className = "turn-head";
  head.textContent = `${source}: ${Math.round(totalMs)} ms end to end`;
  turn.append(head);
  for (const c of list) {
    const row = document.createElement("div");
    row.className = `call ${c.ok ? "ok" : "error"}`;
    // Confirmation tokens are single-use and short-lived, but still not something to put on screen.
    const shown = { ...c.arguments };
    if ("confirmation_token" in shown) shown.confirmation_token = "•••";
    const args = Object.keys(shown).length ? ` ${JSON.stringify(shown)}` : "";
    row.textContent = `${c.tool}${args}: ${c.ms} ms${c.ok ? "" : " (error)"}`;
    turn.append(row);
  }
  if (!list.length) {
    const none = document.createElement("div");
    none.className = "muted";
    none.textContent = "No tool calls.";
    turn.append(none);
  }
  calls.prepend(turn);
}

function setSignedIn(signedIn: boolean, engine: string | null): void {
  signin.hidden = signedIn;
  form.hidden = !signedIn;
  logoutButton.hidden = !signedIn;
  engineLabel.textContent = engine === "offline" ? "Engine: offline keyword router (no model)" : engine ? `Engine: ${engine}` : "";
}

async function send(message: string): Promise<void> {
  const text = message.trim();
  if (!text || busy) return;
  busy = true;
  stopSpeaking();
  addLine("user", text);
  const pending = addLine("assistant", "…");
  pending.classList.add("pending");
  const t0 = performance.now();
  try {
    const turn = await converse(text, history);
    pending.textContent = turn.reply;
    pending.classList.remove("pending");
    history.push({ role: "user", text }, { role: "assistant", text: turn.reply });
    showCalls(turn.calls, performance.now() - t0);
    if (turn.ui) {
      // Calls the view makes (row tap, Propose, Approve) land in the drawer too.
      renderView(viewBox, turn.ui, (call) => showCalls([call], call.ms, "View")).catch((e: Error) =>
        addLine("system", `The view could not start: ${e.message}`),
      );
    }
    if (voice.checked) speak(turn.reply);
  } catch (err) {
    pending.remove();
    if (err instanceof SignedOut) {
      setSignedIn(false, null);
      addLine("system", "Your session ended. Please sign in again.");
    } else {
      addLine("system", `Something went wrong: ${(err as Error).message}`);
    }
  } finally {
    busy = false;
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
  mic.title = "Voice input needs Chrome";
}
mic.addEventListener("click", async () => {
  if (busy) return;
  mic.classList.add("listening");
  try {
    const heard = await listenOnce((interim) => (input.value = interim));
    input.value = "";
    if (heard) await send(heard);
  } catch (err) {
    addLine("system", `Voice input stopped: ${(err as Error).message}`);
  } finally {
    mic.classList.remove("listening");
  }
});

logoutButton.addEventListener("click", async () => {
  await logout();
  history.length = 0;
  transcript.replaceChildren();
  await clearView(viewBox, "Views from Watchfloor tools appear here.");
  setSignedIn(false, null);
});

async function start(): Promise<void> {
  renderBanner($("banner"));
  const params = new URLSearchParams(location.search);
  const error = params.get("signin");
  if (error) {
    addLine("system", SIGNIN_ERRORS[error] ?? `Sign-in failed (${error}).`);
    window.history.replaceState(null, "", location.pathname);
  }
  try {
    const info = await session();
    setSignedIn(info.signed_in, info.engine);
    if (info.signed_in && !transcript.children.length) {
      addLine("system", 'Signed in. Try "What needs my attention today?"');
    }
  } catch (err) {
    setSignedIn(false, null);
    addLine("system", `The harness API is not reachable: ${(err as Error).message}`);
  }
}

void start();
