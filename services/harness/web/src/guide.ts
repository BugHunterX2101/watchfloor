// The Guide page: what this is, scenarios to run, the keyboard, and what the page keeps. Every
// statement here describes how the harness actually behaves; the scenarios use the phrasings the
// offline keyword router understands too, and say plainly which ones need the model engine.

import { confirmDialog, h, icon, toast } from "./dom";
import { ICONS } from "./icons";
import { promptSafe, questionFor, type BriefingDeal } from "./logic";
import { forgetEverything, settings } from "./store";

export interface GuideHost {
  ask(text: string): void;
  /** "offline", a model name, or null when signed out. */
  engine(): string | null;
  openSettings(): void;
  /** The deals in the latest briefing this visit (empty before the first). */
  deals(): readonly BriefingDeal[];
}

interface Scenario {
  title: string;
  says: string;
  why: string;
  needsModel?: boolean;
}

/** The scenarios, about your own deals once a briefing has run: the one that most needs you. Before
 *  that, only the ones that need no deal name are offered. */
function scenarios(deals: readonly BriefingDeal[]): Scenario[] {
  const top = deals.find((d) => d.band === "at_risk") ?? deals.find((d) => d.band === "watch") ?? deals[0];
  const who = top ? promptSafe(top.account) : "";
  return [
    { title: "Your morning briefing", says: "What needs my attention today?", why: "The risk board opens on the screen and the top deals are read out." },
    ...(top
      ? [
          {
            title: top.band === "at_risk" ? "Why is it at risk?" : "How is it doing?",
            says: questionFor(top),
            why: `Health (${top.health} now), the reasons behind it and the trend, in one spoken answer.`,
          },
          { title: "Go deeper", says: `Dig into ${who}`, why: "Starts a background review; Watchfloor tells you when it is ready, so there is nothing to check." },
        ]
      : []),
    { title: "What changed?", says: "What changed since my last briefing?", why: "Only what is new or has moved since you last asked." },
    { title: "What am I watching?", says: "What am I watching?", why: "Lists your standing orders." },
    ...(top
      ? [
          {
            title: "Do something about it",
            says: `Create a follow-up task for the owner of ${who}.`,
            why: "Previews the change, reads it back, and only acts after you say yes. You can undo it for 30 seconds.",
            needsModel: true,
          },
        ]
      : []),
  ];
}

// What can be said, by what it does. Each line is a phrase the page understands on its own (no
// model needed) except the last group, which goes to the assistant. voice/grammar.ts is the source.
// Deal names are your own once a briefing has run; until then "a deal's name" stands in for one.
function sayList(deals: readonly BriefingDeal[], name: string): [string, string[]][] {
  const first = deals[0] ? promptSafe(deals[0].account) : "";
  const second = deals.find((d) => d.account !== deals[0]?.account);
  const other = second ? promptSafe(second.account) : "";
  return [
    ["Move around", ["Go to the pipeline", "Open standing orders", "Show activity", "Open the guide", "Go home", "Go back"]],
    [
      "Work the board",
      ["Show only at-risk deals", "Sort by amount", "Show the top three", "Show enterprise deals", "Clear filters", first ? `Open ${first}` : "Open, then a deal's name", "Open the second one", "Pin the first deal"],
    ],
    ["Look and sound", ["Dark mode", "Speak slower", "Change the voice", "Mute", "Reduce motion", "Hide the sweep", "Hide the background", `Call me ${name || "Sam"}`]],
    ["Run the session", ["What can I say here?", "Stop", "Repeat that", "Read this page", "Remove the first rule", "Clear the conversation", "Sign me out"]],
    [
      "Ask and act",
      [
        "What needs my attention today?",
        "What should I do first?",
        other ? `Show me ${other}` : "Show me, then a deal's name",
        first ? `Create a follow-up task for the owner of ${first}` : "Create a follow-up task for the owner of, then a deal's name",
        "Flag deals over fifty thousand with no reply for a week",
      ],
    ],
  ];
}

const KEYS: [string, string][] = [
  ["/", "Jump to the message box"],
  ["Enter", "Send"],
  ["h", "Start or stop hands-free listening"],
  ["Esc", "Stop speaking or listening"],
  ["g then w", "Go to Watch"],
  ["g then p", "Go to Pipeline"],
  ["g then o", "Go to Orders"],
  ["g then a", "Go to Activity"],
  ["g then g", "Go to this guide"],
  [",", "Open settings"],
  ["?", "Open this guide"],
];

export function mountGuide(root: HTMLElement, host: GuideHost): { refresh(): void; describe(): string } {
  root.replaceChildren();

  const head = h(
    "header",
    { class: "page-head" },
    h(
      "div",
      null,
      h("p", { class: "eyebrow" }, "Start here"),
      h("h1", { class: "page-title", "data-page-title": true }, "Guide"),
      h("p", { class: "lede" }, "Watchfloor tells you which deals are slipping, why, and what to do next. You can ask out loud or type."),
    ),
  );

  const scenarioPanel = h("section", { class: "panel", "aria-labelledby": "guide-try" });
  function renderScenarios(): void {
    const engine = host.engine();
    const offline = engine === "offline";
    const signedIn = engine !== null;
    const note = !signedIn
      ? "Sign in on Watch first; scenarios run against your own workspace."
      : offline
        ? "This run uses the offline keyword router, so the scenario that needs a language model is switched off."
        : "A language model is answering, so all of these work.";
    const own = host.deals().length
      ? " The ones about a single deal use the deal that needs you most in your latest briefing."
      : signedIn
        ? " Once a briefing has run, scenarios about your own top deal appear here too."
        : "";
    scenarioPanel.replaceChildren(
      h("h2", { class: "panel-title", id: "guide-try" }, "Try it"),
      h("p", { class: "muted" }, note + own),
      h(
        "ul",
        { class: "scenarios" },
        ...scenarios(host.deals()).map((s) => {
          const blocked = !signedIn || (s.needsModel === true && offline);
          const run = h("button", { type: "button", class: "btn btn-quiet", disabled: blocked });
          run.append(icon(ICONS.play), "Run it");
          run.addEventListener("click", () => host.ask(s.says));
          return h(
            "li",
            { class: "scenario" },
            h(
              "div",
              null,
              h("h3", { class: "scenario-title" }, s.title, s.needsModel ? h("span", { class: "tag" }, "model engine") : null),
              h("p", { class: "scenario-says" }, `“${s.says}”`),
              h("p", { class: "muted" }, s.why),
            ),
            run,
          );
        }),
      ),
    );
  }

  const sayGrid = h("div", { class: "say-grid" });
  function renderSay(): void {
    sayGrid.replaceChildren(
      ...sayList(host.deals(), settings().name).map(([group, phrases]) =>
        h(
          "div",
          { class: "say-group" },
          h("h3", { class: "mini-title" }, group),
          h("ul", { class: "say-list" }, ...phrases.map((p) => h("li", null, `“${p}”`))),
        ),
      ),
    );
  }

  const voice = h(
    "section",
    { class: "panel", "aria-labelledby": "guide-voice" },
    h("h2", { class: "panel-title", id: "guide-voice" }, "Run everything by voice"),
    h(
      "p",
      null,
      "Press ",
      h("kbd", { class: "kbd solid" }, "h"),
      " or choose the sound-bars button in the top bar, then say “Watchfloor” and what you want. Everything on every page can be done this way except typing a password to sign in.",
    ),
    h(
      "ul",
      { class: "plain" },
      h("li", null, "Once you have asked something the conversation stays open: keep talking, answer its questions or change your mind without saying “Watchfloor” or touching the microphone again. After about 45 seconds of quiet (90 if it asked you something) it waits for “Watchfloor” again."),
      h("li", null, "Talk over it, or say “stop”, to interrupt. Say “that's all” when you are finished, or “stop listening” to turn hands-free off."),
      h("li", null, "When a deep review finishes it tells you out loud, without being asked. About once a minute it also looks at your board and tells you if a deal slips to at risk, drops sharply, recovers, or a standing order triggers (that look reads your briefing, so it counts as seen; say “stop interrupting me” to quiet it)."),
      h("li", null, "Anything that signs you out, clears data or changes your orders is read back first and waits for your yes. Change your mind at any point: say “make it forty” to change a detail, or “no” or “cancel that” to drop it. If you speak while it is still working, it takes that up as soon as it has finished, and it never treats a yes said before you heard the question as your answer."),
      h("li", null, "If it mishears a name, correct it (“no, I said Kestrel”). It redoes the request and remembers the correction in this browser, so the same mishearing is put right next time; Settings lists what it has learned, and each can be removed. Numbers and yes or no are never learned: those are always read back."),
      h("li", null, "Say “keep it short” or “more detail from now on” to set how long answers are (a plain “more detail” is a follow-up question about the last answer), and tell it your name in Settings, or just say “call me …”. “What can I say here?” lists what works on the page you are on."),
      h("li", null, "Hands-free switches itself off after five minutes without a request."),
    ),
    sayGrid,
    h(
      "p",
      { class: "muted" },
      "What it hears: your browser's own speech recognition listens while hands-free is on. In Chrome and Edge that audio is processed by the browser's speech service, not by Watchfloor. Watchfloor itself acts only on requests that start with the wake word (or come during a conversation you started) and sends nothing else anywhere. It needs Chrome or Edge, and pauses while the tab is hidden.",
    ),
  );

  const how = h(
    "section",
    { class: "panel", "aria-labelledby": "guide-how" },
    h("h2", { class: "panel-title", id: "guide-how" }, "What you're looking at"),
    h("p", null, "This is a stand-in for the Alexa+ app: a simulated assistant you can talk to. Everything behind it is the real Watchfloor:"),
    h(
      "ol",
      { class: "flow" },
      h("li", null, h("b", null, "This page"), " takes your voice or text and shows the views, the way an Echo Show would."),
      h("li", null, h("b", null, "The harness API"), " signs you in with OAuth and PKCE, the same account linking Alexa+ uses, and keeps your tokens in a cookie this page can't read."),
      h("li", null, h("b", null, "The Watchfloor MCP server"), " runs the tools: risk briefing, explanations, deep reviews, previewed actions and standing orders."),
      h("li", null, h("b", null, "The risk engine"), " scores every deal on four factors: responsiveness, intent, governance and schedule."),
    ),
    h("p", { class: "muted" }, "Anything that changes data is previewed first and runs only after you say yes."),
    h(
      "p",
      { class: "muted" },
      "What is made up, and what is not: the sales team, the companies, the contacts and the deals are a sample (the Lumen Ridge workspace), so nothing you do here reaches a real customer. Every score and every reason you are shown is worked out by the real risk engine from that sample, never typed in by hand.",
    ),
    h(
      "p",
      { class: "muted" },
      "The scope on Watch is your pipeline. After a briefing each deal is a contact on it: the nearer the centre, the lower its health (the rings at 35 and 60 are where At risk and Watch begin), its bearing is its stage, its size its amount, and its shape says its band. Choose one to open it. The lamp at the centre is the assistant: it breathes while ready, brightens while listening, turns while it thinks and pulses on every word it speaks. Every fact on the scope is also in words beside it.",
    ),
  );

  const keys = h(
    "section",
    { class: "panel", "aria-labelledby": "guide-keys" },
    h("h2", { class: "panel-title", id: "guide-keys" }, "Keyboard"),
    h(
      "dl",
      { class: "keys" },
      ...KEYS.flatMap(([k, what]) => [
        h("dt", null, ...k.split(" ").map((part) => (part === "then" ? ` ${part} ` : h("kbd", { class: "kbd solid" }, part)))),
        h("dd", null, what),
      ]),
    ),
  );

  const forget = h("button", { type: "button", class: "btn btn-quiet danger-text" }, "Forget everything in this browser");
  forget.addEventListener("click", async () => {
    const yes = await confirmDialog({
      title: "Forget everything in this browser?",
      body: "Your name, voice and motion choices, recent questions, pinned deals and corrections are removed. Your Watchfloor account and data are not touched.",
      confirm: "Forget",
      danger: true,
    });
    if (!yes) return;
    forgetEverything();
    toast("Cleared. This browser has forgotten you.");
  });
  const settingsButton = h("button", { type: "button", class: "btn btn-quiet" }, "Open settings");
  settingsButton.addEventListener("click", host.openSettings);

  const data = h(
    "section",
    { class: "panel", "aria-labelledby": "guide-data" },
    h("h2", { class: "panel-title", id: "guide-data" }, "What this page keeps"),
    h("p", null, "Only in this browser, and only what makes it feel like yours:"),
    h(
      "ul",
      { class: "plain" },
      h("li", null, "the name you give it, for the greeting"),
      h("li", null, "your theme, voice, speed, motion and background choices"),
      h("li", null, "your last few questions and up to five pinned deals"),
      h("li", null, "the corrections you teach it by voice (Settings lists each one)"),
      h("li", null, "the shape of your last board, with no names or amounts, so the scope has something to show when you come back (it is cleared when you sign out)"),
    ),
    h("p", { class: "muted" }, "None of it is sent anywhere. Your sign-in lives in a separate cookie that scripts can't read."),
    h("div", { class: "sheet-actions left" }, settingsButton, forget),
  );

  const access = h(
    "section",
    { class: "panel", "aria-labelledby": "guide-a11y" },
    h("h2", { class: "panel-title", id: "guide-a11y" }, "Made to be used any way you like"),
    h(
      "ul",
      { class: "plain" },
      h("li", null, "Everything works from the keyboard, and the page tells a screen reader where you've landed."),
      h("li", null, "Risk is never colour alone: each band is a word, a colour and a shape."),
      h("li", null, "Motion follows your system setting, and “Calm” in settings holds the sweep, the lamp, the chart behind the pages and the lean of the deal cards still. The chart is decoration: it shows nothing about your deals, and its colours are kept dim enough that text keeps its contrast anywhere on it."),
      h("li", null, "The Echo Show previews on Watch show the view at 960, 1280 and 1920 pixels wide."),
    ),
  );

  // A long page gets a way round it. Buttons, not links: a "#section" address would be taken by the
  // router as a page that does not exist.
  const parts: [string, HTMLElement][] = [
    ["Try it", scenarioPanel],
    ["By voice", voice],
    ["How it works", how],
    ["Keyboard", keys],
    ["What it keeps", data],
    ["Accessibility", access],
  ];
  const jump = h(
    "nav",
    { class: "jump chip-row", "aria-label": "On this page" },
    ...parts.map(([text, panel]) => {
      const b = h("button", { type: "button", class: "chip" }, text);
      b.addEventListener("click", () => {
        const calm = matchMedia("(prefers-reduced-motion: reduce)").matches || settings().motion === "calm";
        panel.scrollIntoView({ block: "start", behavior: calm ? "auto" : "smooth" });
        const title = panel.querySelector<HTMLElement>(".panel-title");
        title?.setAttribute("tabindex", "-1");
        title?.focus({ preventScroll: true });
      });
      return b;
    }),
  );

  root.append(head, jump, scenarioPanel, voice, how, keys, data, access);
  renderScenarios();
  renderSay();
  return {
    refresh() {
      renderScenarios();
      renderSay();
    },
    describe: () =>
      "This is the guide. It has scenarios to try, everything you can say, how the pieces fit together, the keyboard shortcuts, what this page keeps, and accessibility notes. To hear what you can say, ask: what can I say?",
  };
}
