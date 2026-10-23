// What was said, in a few exact phrases: the spoken commands that never need a language model. A
// command here runs at once, offline, in the time it takes to say it. Anything that is not one of
// these (a question, a request to do something with a deal) goes on to the assistant, which has the
// same page controls through `control_app`.
//
// The rules are deliberately strict: a whole utterance must be a command, so "show me why Kestrel
// is at risk" is a question for the assistant and "open Kestrel" is a command. Politeness, a
// leading wake word and a trailing "please" are stripped first. Pure (no DOM, no storage), so
// `npm test` runs it directly.

import type { AppAction, BandName, DeviceName, PageName, SegmentName, SortName } from "./actions.ts";
import { norm, ordinal, phonetic, similarity, toCount } from "./text.ts";

export interface DealRef {
  id: string;
  account: string;
  name: string;
}

export interface RuleRef {
  id: string;
  description: string;
}

/** What the grammar needs to know about the page to read an utterance the way the user meant it. */
export interface Context {
  page: PageName;
  signedIn: boolean;
  /** The open dialog, if any. "confirm" is a yes-or-no question on the screen. */
  dialog: "none" | "settings" | "deal" | "confirm";
  /** The page has asked the user a question out loud and is waiting for yes or no. */
  confirming: boolean;
  deals: readonly DealRef[];
  /** The ids of the deals on the board, in the order the user sees them ("the second one" means the
   *  second of these). When absent, `deals` is taken to be in that order. */
  board?: readonly string[];
  rules: readonly RuleRef[];
  /** The deal whose sheet is open. */
  openDealId: string | null;
  /** The assistant has just read back a change and is waiting for the user's answer (a preview, or a
   *  rule it is holding): a plain "no" or "cancel that" drops it, with no model call. */
  awaiting?: boolean;
}

export type Intent =
  | { kind: "action"; action: AppAction }
  | { kind: "yes" }
  | { kind: "no" }
  | { kind: "wake" }
  | { kind: "back" }
  | { kind: "help" }
  | { kind: "signin" }
  /** "That's all": the conversation is over; the wake word is needed again. */
  | { kind: "rest" }
  | { kind: "remove_rule"; which: string }
  | { kind: "model"; text: string };

// --- the wake word --------------------------------------------------------------------------------

// Recognition hears "Watchfloor" as one word or two. "Watch for" is left alone on purpose: it opens
// real requests ("watch for deals under health thirty-five").
const WAKE = /^\s*(?:(?:hey|hi|ok|okay|yo)[\s,]+)?watch[\s-]?floors?\b[\s,.:;!?-]*/i;

export function hasWake(raw: string): boolean {
  return WAKE.test(raw);
}

/** The utterance without its wake word, and whether it had one. */
export function stripWake(raw: string): { text: string; woke: boolean } {
  const m = WAKE.exec(raw);
  return m ? { text: raw.slice(m[0].length).trim(), woke: true } : { text: raw.trim(), woke: false };
}

// --- cleaning --------------------------------------------------------------------------------------

const LEADS = [
  /^(?:please|kindly|ok|okay|hey|um|uh|so|now|just|actually|alright|right)\s+/,
  /^(?:can|could|would|will)\s+you\s+(?:please\s+)?/,
  /^(?:i\s+want\s+you\s+to|id\s+like\s+you\s+to|i\s+would\s+like\s+you\s+to|i\s+need\s+you\s+to)\s+/,
  /^(?:i\s+want\s+to|i\s+would\s+like\s+to|id\s+like\s+to|i\s+need\s+to|lets|let\s+us)\s+/,
];
const TAIL = /\s+(?:please|thanks|thank\s+you|for\s+me|right\s+now|now)$/;

function tidy(u: string): string {
  let out = u;
  for (let i = 0; i < 4; i++) {
    const before = out;
    for (const re of LEADS) out = out.replace(re, "");
    out = out.replace(TAIL, "");
    if (out === before) break;
  }
  return out.trim();
}

// --- yes and no ------------------------------------------------------------------------------------

const YES =
  /^(?:yes|yeah|yep|yup|sure|ok|okay|go\s+ahead|do\s+it|confirm|confirmed|affirmative|correct|absolutely|proceed|thats\s+right|please\s+do|yes\s+please|yes\s+go\s+ahead|yes\s+do\s+it|remove\s+it|sign\s+me\s+out|clear\s+it)$/;
const NO =
  /^(?:no|nope|nah|cancel|stop|dont|do\s+not|never\s+mind|nevermind|negative|not\s+now|leave\s+it|keep\s+it|wait|hold\s+on|no\s+thanks|no\s+dont)$/;

// Dropping a change the assistant has just read back and is waiting on. Narrower than NO on purpose:
// "wait" and "hold on" ask for more time, and "stop" stops the voice; neither declines the change.
// One phrase or two together ("no, never mind", "never mind, leave it"), as people say them.
const DECLINE_ONE =
  "(?:no(?:\\s+(?:thanks|thank\\s+you|dont|do\\s+not|leave\\s+it|not\\s+now))?|nope|nah|negative|not\\s+now|cancel(?:\\s+(?:that|it|this|the\\s+(?:change|order|rule|update|action|task|draft|email)))?|never\\s*mind(?:\\s+(?:that|it))?|forget\\s+(?:about\\s+)?(?:it|that)|drop\\s+(?:it|that)|scrap\\s+(?:it|that)|skip\\s+(?:it|that)|leave\\s+it|dont\\s+(?:do\\s+(?:it|that)|bother|save\\s+(?:it|that)|worry\\s+about\\s+it)|do\\s+not\\s+(?:do\\s+(?:it|that)|save\\s+(?:it|that)))";
const DECLINE = new RegExp(`^(?:(?:actually|okay|ok)\\s+)?${DECLINE_ONE}(?:\\s+${DECLINE_ONE})?(?:\\s+thanks)?$`);

/** A bare yes or no (to a question on the screen or one just asked aloud). Held back while the
 *  assistant is still working: a "yes" said before the question was heard is not an answer to it. */
export function isAnswer(raw: string): boolean {
  const u = tidy(norm(stripWake(raw).text));
  return YES.test(u) || NO.test(u) || DECLINE.test(u);
}

// The end of a conversation (the microphone stays on, waiting for the wake word); saying goodbye or
// "stop listening" ends hands-free itself. Only as a whole utterance, so "thanks, what about Kestrel"
// carries on. (The cleaner has already taken a trailing "now" off "that's it for now".)
const REST =
  /^(?:(?:no|great|perfect|cool|thanks|thank\s+you)\s+)*(?:thats\s+(?:all|it|enough)(?:\s+for(?:\s+(?:now|today))?)?|(?:im|i\s+am|we\s+are|were|all)\s+done|nothing\s+(?:else|more))(?:\s+(?:thanks|thank\s+you))?$/;

// --- pages -----------------------------------------------------------------------------------------

const PAGE_WORDS: [PageName, string][] = [
  ["watch", "watch|home|main|conversation|chat|assistant|talk|start"],
  ["pipeline", "pipeline|pipe\\s+line|dashboard|board|risk\\s+board|deal\\s+board|deals|risk\\s+briefing|briefing\\s+board|pipeline\\s+board"],
  ["orders", "orders?|standing\\s+orders?|standing\\s+rules?|rules?|alerts?|alert\\s+rules?"],
  ["activity", "activity|activity\\s+log|log|history|trace|tool\\s+calls|metrics|usage"],
  ["guide", "guide|help|help\\s+page|user\\s+guide|tutorial|instructions|how\\s+it\\s+works|shortcuts|keyboard\\s+shortcuts"],
];
const NAV_VERB =
  "(?:go\\s+to|go|open|show|show\\s+me|take\\s+me\\s+to|switch\\s+to|bring\\s+up|navigate\\s+to|jump\\s+to|see|view|visit|pull\\s+up|display)";
const NAV = PAGE_WORDS.map(([name, words]) => ({
  name,
  verbed: new RegExp(`^${NAV_VERB}\\s+(?:the\\s+|my\\s+|our\\s+)?(?:${words})(?:\\s+(?:page|screen|tab|view))?$`),
  named: new RegExp(`^(?:the\\s+)?(?:${words})\\s+(?:page|screen|tab)$`),
}));

// A page named on its own is a request to go there. Only the names that mean nothing else: "watch"
// (a risk band), "home", "start" and "help" are left to the commands and to the assistant.
const BARE_PAGE: [RegExp, PageName][] = [
  [/^(?:pipeline|pipe\s+line)$/, "pipeline"],
  [/^(?:orders?|standing\s+orders?)$/, "orders"],
  [/^activity$/, "activity"],
  [/^guide$/, "guide"],
];

function pageFrom(u: string): PageName | null {
  for (const n of NAV) if (n.verbed.test(u) || n.named.test(u)) return n.name;
  for (const [re, name] of BARE_PAGE) if (re.test(u)) return name;
  if (/^(?:go\s+|take\s+me\s+)?(?:back\s+)?(?:to\s+)?(?:the\s+)?home(?:\s+page)?$/.test(u)) return "watch";
  if (/^(?:go\s+)?(?:back|return)\s+to\s+(?:the\s+)?(?:watch|conversation|main|chat)(?:\s+page|\s+screen)?$/.test(u)) return "watch";
  return null;
}

// --- deals and orders: matching names that speech recognition may have spelt differently ----------

const FILLER_WORDS = new Set(["the", "a", "an", "my", "our", "this", "that", "deal", "deals", "account", "accounts", "details", "sheet", "card", "one"]);

function significant(text: string): string[] {
  return norm(text)
    .split(" ")
    .filter((w) => w && !FILLER_WORDS.has(w));
}

function wordScore(q: string, c: string): number {
  if (q === c) return 1;
  if (phonetic(q) === phonetic(c)) return 0.95;
  if (similarity(phonetic(q), phonetic(c)) >= 0.8 || similarity(q, c) >= 0.8) return 0.85;
  // A clipped word ("bio" for Biotech, "harb" for Harbor) and a long word misheard as another
  // ("castle" for Kestrel): weaker, so it takes a second word that fits to carry a match.
  if (q.length >= 3 && c.length > q.length && c.startsWith(q)) return 0.85;
  return q.length >= 5 && c.length >= 5 && similarity(phonetic(q), phonetic(c)) >= 0.7 ? 0.8 : 0;
}

/** How well a spoken name fits a deal: every spoken word must find a word of the account or deal name. */
function dealScore(query: string, deal: DealRef): number {
  const q = significant(query);
  if (!q.length) return 0;
  let best = 0;
  for (const label of [deal.account, deal.name]) {
    const words = norm(label).split(" ").filter(Boolean);
    if (!words.length) continue;
    const per = q.map((w) => Math.max(0, ...words.map((c) => wordScore(w, c))));
    if (per.some((s) => s === 0)) continue;
    const covered = per.reduce((a, b) => a + b, 0) / per.length;
    // Naming the whole account scores above naming one word of it.
    const share = Math.min(1, q.length / Math.min(words.length, 3));
    best = Math.max(best, covered * (0.85 + 0.15 * share));
  }
  return best;
}

export interface DealMatch {
  deal: DealRef | null;
  ambiguous: DealRef[];
}

/** The deal a spoken name means, among the ones the page knows. Two deals that fit equally well are
 *  "ambiguous": the caller asks which, rather than picking one. */
export function resolveDeal(query: string, deals: readonly DealRef[]): DealMatch {
  const scored = deals
    .map((deal) => ({ deal, score: dealScore(query, deal) }))
    .filter((s) => s.score >= 0.8)
    .sort((a, b) => b.score - a.score);
  if (!scored.length) return { deal: null, ambiguous: [] };
  const close = scored.filter((s) => s.score >= scored[0].score - 0.06);
  return close.length === 1 ? { deal: scored[0].deal, ambiguous: [] } : { deal: null, ambiguous: close.map((s) => s.deal) };
}

const POSITION =
  /^(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th|top|last|bottom)(?:\s+(?:one|deal|account))?$|^(?:deal\s+)?(?:number|no)\s+(one|two|three|four|five|[1-5])$/;
const POSITION_N: Record<string, number> = { first: 1, "1st": 1, top: 1, second: 2, "2nd": 2, third: 3, "3rd": 3, fourth: 4, "4th": 4, fifth: 5, "5th": 5, last: -1, bottom: -1 };

/** "The second one" as the deal in that place on the board. undefined: not a place at all (a name,
 *  say); null: a place, but nothing is there (the board is empty or shorter). */
export function dealAt(phrase: string, ctx: Pick<Context, "deals" | "board">): DealRef | null | undefined {
  const m = POSITION.exec(phrase);
  if (!m) return undefined;
  const n = m[1] ? POSITION_N[m[1]] : toCount(m[2]);
  if (!n) return null;
  const byId = new Map(ctx.deals.map((d) => [d.id, d]));
  const ordered = ctx.board?.length ? ctx.board.map((id) => byId.get(id)).filter((d): d is DealRef => !!d) : [...ctx.deals];
  return (n === -1 ? ordered[ordered.length - 1] : ordered[n - 1]) ?? null;
}

const RULE_STOP = new Set(["the", "a", "an", "my", "that", "this", "standing", "order", "orders", "rule", "rules", "alert", "alerts", "about", "for", "on", "called", "named", "one", "number", "of", "with", "to"]);

export interface RuleMatch {
  rule: RuleRef | null;
  ambiguous: RuleRef[];
}

/** The standing order a spoken phrase means: "the first one", "the last rule", or words from its text. */
export function resolveRule(which: string, rules: readonly RuleRef[]): RuleMatch {
  if (!rules.length) return { rule: null, ambiguous: [] };
  const words = norm(which).split(" ").filter(Boolean);
  const content = words.filter((w) => !RULE_STOP.has(w));
  const position = words.map(ordinal).find((n): n is number => n !== null);
  if (position !== undefined && content.every((c) => ordinal(c) !== null)) {
    const rule = position === -1 ? rules[rules.length - 1] : rules[position - 1];
    return { rule: rule ?? null, ambiguous: [] };
  }
  if (!content.length) return rules.length === 1 ? { rule: rules[0], ambiguous: [] } : { rule: null, ambiguous: [...rules] };
  const scored = rules
    .map((rule) => {
      const dw = norm(rule.description).split(" ").filter(Boolean);
      const hit = content.map((w) => Math.max(0, ...dw.map((d) => wordScore(w, d))));
      return { rule, score: hit.reduce((a, b) => a + b, 0) / content.length };
    })
    .filter((s) => s.score >= 0.6)
    .sort((a, b) => b.score - a.score);
  if (!scored.length) return { rule: null, ambiguous: [] };
  const close = scored.filter((s) => s.score >= scored[0].score - 0.15);
  return close.length === 1 ? { rule: scored[0].rule, ambiguous: [] } : { rule: null, ambiguous: close.map((s) => s.rule) };
}

// --- the commands ----------------------------------------------------------------------------------

const act = (action: AppAction): Intent => ({ kind: "action", action });
const view = (v: Omit<Extract<AppAction, { type: "pipeline_view" }>, "type">): Intent => act({ type: "pipeline_view", ...v });

const BAND_WORDS: Record<string, BandName> = {
  "at risk": "at_risk",
  risky: "at_risk",
  red: "at_risk",
  critical: "at_risk",
  "on watch": "watch",
  watch: "watch",
  amber: "watch",
  yellow: "watch",
  healthy: "healthy",
  green: "healthy",
  safe: "healthy",
  all: "all",
  everything: "all",
  every: "all",
};
// A segment named alone is a segment only where the name is unmistakable.
const SEGMENT_WORDS: Record<string, SegmentName> = {
  enterprise: "enterprise",
  smb: "smb",
  "small business": "smb",
  "small businesses": "smb",
  "mid market": "mid",
  midmarket: "mid",
  "s m b": "smb", // recognition spells an acronym out letter by letter
};
const SEGMENT_NAMED: Record<string, SegmentName> = { small: "smb", mid: "mid", medium: "mid", all: "all", every: "all", default: "default" };
const VIEW_VERB = /^(?:show|display|list|filter(?:\s+by|\s+to|\s+on)?|see|give|only|just|focus\s+on|view|switch\s+to|use)\b\s*(.*)$/;
const VIEW_FILLER = new Set(["the", "me", "my", "only", "just", "deals", "deal", "ones", "accounts", "account", "customers", "band", "segment", "segments", "list", "on", "to", "by"]);

/** What is left of "show me only the at-risk deals" once the verb and filler words are gone. */
function viewPhrase(u: string): string | null {
  // "at risk deals only" is "only at risk deals": the word may come last.
  const body = VIEW_VERB.exec(u)?.[1] ?? /^(.+)\s+only$/.exec(u)?.[1];
  return body === undefined ? null : body.split(" ").filter((w) => w && !VIEW_FILLER.has(w)).join(" ");
}

// What may follow "sort by": the whole phrase must be a sort key. A phrase with anything else in it
// ("amount and show only at risk", "health then open Kestrel") is not a sort command: the rest of the
// sentence would be dropped without a word, so it goes on to the compound splitter or the assistant.
const SORT_PHRASES: [RegExp, SortName][] = [
  [/^(?:the\s+)?(?:urgency|urgent|priority|risk)$/, "urgency"],
  [/^(?:the\s+)?health(?:\s+score)?$/, "health"],
  [/^(?:the\s+)?(?:amount|value|size|money|worth|(?:deal|dollar)\s+(?:size|value|amount)|deal\s+worth)$/, "amount"],
  [/^(?:the\s+)?(?:name|(?:account|company)\s+name|alphabet|alphabetical(?:ly)?(?:\s+order)?)$/, "name"],
];
const SORT_VERB = "(?:sort|order|rank|arrange)(?:\\s+the)?(?:\\s+(?:board|deals|list|them|it|pipeline))?";

function sortFrom(u: string): SortName | null {
  const by = new RegExp(`^${SORT_VERB}\\s+by\\s+(.+)$`).exec(u);
  if (by) {
    for (const [re, key] of SORT_PHRASES) if (re.test(by[1])) return key;
    return null;
  }
  if (new RegExp(`^${SORT_VERB}\\s+alphabetical(?:ly)?$`).test(u)) return "name";
  const first = /^(?:the\s+)?(most\s+urgent|largest|biggest|highest(?:\s+(?:value|amount))?|lowest\s+health|sickest|alphabetical)(?:\s+deals?)?\s+first$/.exec(u);
  if (!first) return null;
  const w = first[1];
  return /^most\s+urgent/.test(w) ? "urgency" : /health|sickest/.test(w) ? "health" : w === "alphabetical" ? "name" : "amount";
}

const DEVICE_SIZES: Record<string, DeviceName> = { "5": "compact", five: "compact", "8": "standard", eight: "standard", "15": "large", fifteen: "large" };
const MOTION = "(?:animations?|motion)";
const THEME_VERB = "(?:switch|turn|change|set|go|use|enable|activate|put)";

// "Call me back when the review is done" is a request, not a name. A name is one to three words, none
// of which is a word that starts a sentence or a time: anything else is left to the assistant, because
// what is saved here is spoken back in every greeting.
const NOT_A_NAME = new Set(
  `a an and or but so if when whenever then than that this it its you me i we they them up down out over
  back later after before once soon now again please about on at in to for with from by tomorrow tonight
  today tonight next first anything something everything nothing whatever whoever when what why how`.split(/\s+/),
);

export function plausibleName(text: string): string | null {
  const words = text.trim().split(/\s+/);
  if (!words[0] || words.length > 3) return null;
  const ok = words.every((w) => /^\p{L}[\p{L}'’.-]*$/u.test(w) && !NOT_A_NAME.has(w.toLowerCase()));
  return ok ? words.join(" ") : null;
}

type Rule = (u: string, ctx: Context, raw: string) => Intent | null;

const RULES: Rule[] = [
  // a bare "back", and closing what is open
  (u) => (/^(?:go\s+)?back$/.test(u) ? { kind: "back" } : null),
  (u, ctx) =>
    ctx.dialog !== "none" &&
    /^(?:close|dismiss|hide|exit|done|ok\s+close|close\s+(?:this|that|it|the\s+(?:dialog|sheet|window|popup|settings|details)))$/.test(u)
      ? act({ type: "close_dialog" })
      : null,
  (u, ctx) => (ctx.dialog !== "none" && /^(?:cancel|never\s*mind)$/.test(u) ? act({ type: "close_dialog" }) : null),

  // stopping, repeating, reading
  (u) =>
    /^(?:stop|quiet|silence|enough|shush|hush|be\s+quiet|shut\s+up|stop\s+talking|stop\s+speaking|thats\s+enough|okay\s+stop|pause|cancel|never\s*mind)$/.test(u)
      ? act({ type: "stop_speaking" })
      : null,
  (u) =>
    /^(?:repeat(?:\s+that|\s+it|\s+the\s+last\s+(?:answer|reply|one))?|say\s+(?:that|it)\s+again|what\s+did\s+you\s+say|come\s+again|pardon(?:\s+me)?|sorry\s+what|once\s+more|again|say\s+again)$/.test(u)
      ? act({ type: "repeat" })
      : null,
  (u) =>
    /^(?:read|describe|summari[sz]e|narrate)(?:\s+out)?(?:\s+(?:this|the|my))?(?:\s+current)?(?:\s+(?:page|screen|board|list))?(?:\s+(?:to\s+me|aloud|out\s+loud))?$/.test(u) ||
    /^what(?:s|\s+is)\s+(?:on\s+)?(?:this|the|my)\s+(?:page|screen|board)$/.test(u) ||
    /^(?:what\s+am\s+i\s+looking\s+at|where\s+am\s+i|which\s+page\s+is\s+this|what\s+page\s+is\s+this)$/.test(u)
      ? act({ type: "read_page" })
      : null,
  (u) =>
    /^(?:help|help\s+me|what\s+can\s+i\s+say(?:\s+(?:here|on\s+this\s+page))?|what\s+can\s+you\s+do(?:\s+here)?|what\s+do\s+i\s+say|commands|show\s+commands|voice\s+commands|how\s+do\s+i\s+use\s+(?:this|you))$/.test(u)
      ? { kind: "help" }
      : null,

  // hands-free itself
  (u) =>
    /^(?:stop\s+listening|go\s+to\s+sleep|sleep|hands\s?free\s+off|turn\s+off\s+hands\s?free|good\s*bye|bye(?:\s+bye)?|see\s+you(?:\s+later)?|talk\s+to\s+you\s+later|stand\s+down|end\s+(?:the\s+)?(?:session|conversation)|stop\s+hands\s?free)$/.test(u)
      ? act({ type: "hands_free", on: false })
      : null,
  (u) =>
    /^(?:(?:start|turn\s+on|enable|go|switch\s+to|begin|activate)\s+hands\s?free(?:\s+mode)?|hands\s?free(?:\s+mode)?(?:\s+on)?)$/.test(u)
      ? act({ type: "hands_free", on: true })
      : null,
  (u) =>
    /^(?:(?:turn\s+on|enable|require|use)(?:\s+the)?\s+wake\s+word|turn(?:\s+the)?\s+wake\s+word\s+on|(?:start\s+)?(?:listen(?:ing)?|wait(?:ing)?)\s+(?:only\s+)?for(?:\s+the)?\s+wake\s+word|only\s+(?:listen\s+)?when\s+i\s+say\s+watch\s?floor)$/.test(u)
      ? act({ type: "wake_word", on: true })
      : /^(?:(?:turn\s+off|disable|dont\s+(?:require|need|use)|no)(?:\s+the)?\s+wake\s+word|turn(?:\s+the)?\s+wake\s+word\s+off|always\s+listen|listen\s+all\s+the\s+time)$/.test(u)
        ? act({ type: "wake_word", on: false })
        : null,

  // sessions and data (the page asks before it does any of these)
  (u, ctx) => (/^(?:sign|log)\s+(?:me\s+)?in$/.test(u) && !ctx.signedIn ? { kind: "signin" } : null),
  (u) => (/^(?:(?:sign|log)\s+(?:me\s+)?out(?:\s+of\s+watch\s?floor)?|log\s+off)$/.test(u) ? act({ type: "sign_out" }) : null),
  (u) =>
    /^forget\s+(?:everything|me|my\s+data|what\s+you\s+know\s+about\s+me|all\s+(?:of\s+)?(?:it|that))(?:\s+in\s+this\s+browser)?$/.test(u) ||
    /^(?:clear|erase|delete|wipe)\s+(?:all\s+)?(?:my\s+)?(?:saved|stored|local|browser)\s+(?:data|settings|preferences)$/.test(u)
      ? act({ type: "forget_data" })
      : null,
  (u) =>
    /^(?:clear|reset|wipe)(?:\s+the)?\s+(?:conversation|chat|transcript)$|^(?:start\s+over|new\s+conversation|start\s+a\s+new\s+conversation|clear)$/.test(u)
      ? act({ type: "clear_conversation" })
      : null,
  (u) => (/^(?:clear|reset|wipe)(?:\s+the)?\s+(?:activity(?:\s+log)?|log|trace)$/.test(u) ? act({ type: "clear_activity" }) : null),

  // pages
  (u) => {
    const name = pageFrom(u);
    return name ? act({ type: "navigate", page: name }) : null;
  },
  (u) =>
    /^(?:open|show|change|edit|go\s+to)(?:\s+(?:the|my))?\s+(?:settings|preferences)$|^(?:settings|preferences)$/.test(u)
      ? act({ type: "open_settings" })
      : null,
  (u) =>
    /^(?:refresh|reload|update|renew|check\s+again)(?:\s+(?:this|the))?(?:\s+(?:page|board|pipeline|list|orders|briefing|activity))?$/.test(u) ||
    /^take\s+(?:a\s+)?(?:fresh|new|another|second)\s+look(?:\s+at\s+(?:the|this|my)\s+(?:page|board|pipeline|list))?$/.test(u)
      ? act({ type: "refresh" })
      : null,
  (u) => {
    const m =
      /^scroll\s+(up|down)(?:\s+a\s+(?:bit|little))?$|^page\s+(up|down)$|^(?:scroll\s+to|go\s+to)\s+the\s+(top|bottom)(?:\s+of\s+the\s+page)?$|^(?:to\s+the\s+)?(top|bottom)$/.exec(u);
    const dir = m ? (m[1] ?? m[2] ?? m[3] ?? m[4]) : null;
    return dir ? act({ type: "scroll", direction: dir as "up" | "down" | "top" | "bottom" }) : null;
  },

  // how it looks and sounds
  (u) => {
    const m = new RegExp(
      `^(?:${THEME_VERB}(?:\\s+it)?(?:\\s+back)?(?:\\s+on)?(?:\\s+to)?(?:\\s+the)?\\s+)?(dark|light|night|day|system|auto|automatic)(?:\\s+(?:mode|theme|colors?))?(?:\\s+on)?$`,
    ).exec(u);
    if (!m) return /^follow\s+(?:the\s+|my\s+)?system(?:\s+theme|\s+setting)?$/.test(u) ? act({ type: "set_theme", theme: "auto" }) : null;
    // A bare "light" or "auto" could be anything; bare "dark" and "light" are taken as the theme.
    const bare = !new RegExp(`^${THEME_VERB}`).test(u) && !/(?:mode|theme|colors?)/.test(u);
    if (bare && m[1] !== "dark" && m[1] !== "light") return null;
    const word = m[1];
    return act({ type: "set_theme", theme: word === "dark" || word === "night" ? "dark" : word === "light" || word === "day" ? "light" : "auto" });
  },
  // A complaint about the screen or the pace is a request for the obvious fix.
  (u) => {
    const m = /^(?:(?:its|this\s+is|the\s+screen\s+is)\s+)?too\s+(bright|dark)$/.exec(u);
    return m ? act({ type: "set_theme", theme: m[1] === "bright" ? "dark" : "light" }) : null;
  },
  (u) => {
    const m = /^(?:(?:youre|you\s+are|you)\s+(?:talking|speaking|going|talk|speak)\s+)?too\s+(fast|quickly|slow|slowly)$/.exec(u);
    return m ? act({ type: "set_speech_rate", step: m[1].startsWith("fast") || m[1] === "quickly" ? "slower" : "faster" }) : null;
  },
  (u) =>
    /^(?:mute|be\s+silent|silence\s+replies|stop\s+reading(?:\s+replies|\s+aloud)?|turn\s+off(?:\s+the)?\s+(?:voice|sound|sounds|speech|read\s+aloud|reading\s+aloud)|turn(?:\s+the)?\s+(?:voice|sound|sounds|speech|read\s+aloud)\s+off|dont\s+(?:read|speak)(?:\s+replies|\s+aloud)?|no\s+(?:voice|sound))$/.test(u)
      ? act({ type: "set_read_aloud", on: false })
      : /^(?:unmute|turn\s+on(?:\s+the)?\s+(?:voice|sound|sounds|speech|read\s+aloud)|turn(?:\s+the)?\s+(?:voice|sound|sounds|speech|read\s+aloud)\s+(?:back\s+)?on|read(?:\s+replies)?\s+aloud|speak\s+(?:replies|again)|start\s+(?:reading|speaking))$/.test(u)
        ? act({ type: "set_read_aloud", on: true })
        : null,
  (u) => {
    const m =
      /^(?:speak|talk|read|go)\s+(?:a\s+(?:little|bit)\s+)?(slower|faster|more\s+slowly|more\s+quickly)$|^(slow\s+down|speed\s+up)(?:\s+(?:your|the))?(?:\s+(?:voice|speech|talking|speaking|reading))?(?:\s+a\s+(?:little|bit))?$|^(slow|speed)\s+(?:your\s+|the\s+)?(?:voice|speech)\s+(?:down|up)(?:\s+a\s+(?:little|bit))?$/.exec(u);
    if (m) return act({ type: "set_speech_rate", step: /slow/.test(m[1] ?? m[2] ?? m[3]) ? "slower" : "faster" });
    return /^(?:speak|talk)\s+(?:at\s+)?(?:normal|default)\s+speed$|^normal\s+speed$/.test(u) ? act({ type: "set_speech_rate", rate: 1.05 }) : null;
  },
  (u) =>
    new RegExp(`^(?:calm|calm\\s+mode|reduce\\s+motion|stop(?:\\s+the)?\\s+${MOTION}|turn\\s+off(?:\\s+the)?\\s+${MOTION})$`).test(u)
      ? act({ type: "set_motion", motion: "calm" })
      : new RegExp(`^(?:turn\\s+on|enable|restore|allow)(?:\\s+the)?\\s+${MOTION}$`).test(u)
        ? act({ type: "set_motion", motion: "auto" })
        : null,
  (u) => {
    // The lamp (once "the 3D orb"), the sweep, and the chart behind the pages (the background, the
    // 3D scene, the sea floor: ADR-035). "Orb" still means the lamp.
    const m = /^(?:turn|switch)\s+(on|off)(?:\s+the)?(?:\s+3d)?\s+(orb|lamp|floor|background|backdrop|scene|sweep|radar|plot)(?:\s+(?:animation|sweep))?$|^(show|hide|enable|disable|stop|start)(?:\s+the)?(?:\s+3d)?\s+(orb|lamp|floor|background|backdrop|scene|sweep|radar|plot)(?:\s+(?:animation|sweep))?$/.exec(u);
    if (!m) return null;
    const on = m[1] !== undefined ? m[1] === "on" : m[3] === "show" || m[3] === "enable" || m[3] === "start";
    const target = m[2] ?? m[4];
    if (target === "orb" || target === "lamp") return act({ type: "set_orb", on });
    if (target === "sweep" || target === "radar" || target === "plot") return act({ type: "set_floor", on });
    return act({ type: "set_backdrop", on });
  },
  (u) => {
    const size = /^(?:(?:show|use|set|switch\s+to|preview|try|go\s+to)\s+)?(?:the\s+)?echo\s+show\s+(5|five|8|eight|15|fifteen)(?:\s+(?:preview|size))?$/.exec(u);
    if (size) return act({ type: "set_device", device: DEVICE_SIZES[size[1]] });
    const named = /^(compact|standard|large)\s+(?:preview|size)$/.exec(u);
    if (named) return act({ type: "set_device", device: named[1] as DeviceName });
    return /^(?:fit|fit\s+to\s+(?:the\s+)?window|auto\s+size)$/.test(u) ? act({ type: "set_device", device: "fit" }) : null;
  },
  (u) =>
    /^(?:change|switch|next|different|another|try\s+(?:a\s+)?(?:different|another|new)|use\s+(?:a\s+)?(?:different|another|new))(?:\s+(?:the|your))?\s+voice$|^next\s+voice$/.test(u)
      ? act({ type: "change_voice", step: "next" })
      : /^(?:(?:try|use|go\s+to|switch\s+to)\s+)?(?:the\s+)?(?:previous|last)\s+voice$/.test(u)
        ? act({ type: "change_voice", step: "previous" })
        : null,
  (_u, _ctx, raw) => {
    const m = /^\s*(?:you\s+can\s+)?(?:call\s+me|my\s+name\s+is|address\s+me\s+as)\s+(.{1,40}?)[\s.!]*$/i.exec(raw);
    const name = m ? plausibleName(m[1]) : null;
    return name ? act({ type: "set_name", name }) : null;
  },
  // how long the replies are
  (u) =>
    /^(?:keep\s+(?:it|answers|replies|your\s+answers|them)\s+(?:short|brief|quick)|short(?:er)?\s+(?:answers|replies)|be\s+(?:brief|short|concise|quicker)|brief(?:\s+(?:mode|answers|replies))?|concise(?:\s+(?:mode|answers))?|less\s+detail|(?:just\s+)?the\s+(?:headline|headlines|short\s+version|gist)|too\s+(?:long|wordy))$/.test(u)
      ? act({ type: "set_style", style: "brief" })
      : /^make\s+(?:your\s+|the\s+|my\s+)?(?:answers|replies|responses|them|it)\s+(?:shorter|briefer|short|brief|more\s+concise)$/.test(u)
        ? act({ type: "set_style", style: "brief" })
      : /^make\s+(?:your\s+|the\s+|my\s+)?(?:answers|replies|responses|them|it)\s+(?:longer|more\s+detailed|detailed)$|^(?:more\s+detail\s+from\s+now\s+on|detailed\s+(?:answers|replies|mode)|(?:give\s+me\s+)?longer\s+(?:answers|replies)|be\s+(?:more\s+)?(?:detailed|thorough)|give\s+me\s+(?:the\s+)?(?:full|detailed)\s+(?:answers|replies|story)\s*(?:from\s+now\s+on)?)$/.test(u)
        ? act({ type: "set_style", style: "detailed" })
        : /^(?:normal|default|usual|standard|regular)\s+(?:length|answers|replies|mode)$|^back\s+to\s+normal\s+(?:length|answers|replies)$/.test(u)
          ? act({ type: "set_style", style: "normal" })
          : null,
  // speaking up about changes on the board
  (u) =>
    /^(?:stop\s+interrupting(?:\s+me)?|dont\s+interrupt(?:\s+me)?|no\s+interruptions|(?:dont|do\s+not)\s+(?:speak|say\s+anything)\s+unless\s+(?:i\s+ask|im\s+asked|asked)|only\s+(?:speak|talk)\s+when\s+(?:i\s+ask|spoken\s+to)|(?:turn\s+off|disable|stop|mute)\s+(?:the\s+)?(?:proactive\s+|spoken\s+)?(?:updates|announcements|notifications|proactive\s+alerts|spoken\s+alerts)|no\s+(?:updates|announcements|proactive\s+(?:updates|alerts)))$/.test(u)
      ? act({ type: "set_alerts", on: false })
      : /^(?:(?:turn\s+on|enable|start|allow)\s+(?:the\s+)?(?:proactive\s+|spoken\s+)?(?:updates|announcements|notifications|proactive\s+alerts|spoken\s+alerts)|tell\s+me\s+(?:when|if)\s+(?:anything|something|things)\s+changes?|keep\s+me\s+(?:posted|updated|informed)|speak\s+up\s+when\s+(?:anything|something)\s+changes?)$/.test(u)
        ? act({ type: "set_alerts", on: true })
        : null,

  // the Activity page
  (u, ctx) => {
    // "Show me activity for errors only" names the page too: it opens Activity and filters it.
    const m =
      /^(?:show|filter|list|see)(?:\s+me)?(?:\s+(?:the\s+)?(activity)(?:\s+(?:log|page))?(?:\s+(?:for|with|to))?)?(?:\s+only)?(?:\s+the)?\s+(all|writes|errors|failures|failed|changes)(?:\s+calls)?(?:\s+only)?$/.exec(u);
    if (!m || (m[2] === "all" && !m[1] && ctx.page !== "activity")) return null;
    return act({ type: "activity_view", filter: m[2] === "all" ? "all" : m[2] === "writes" || m[2] === "changes" ? "writes" : "errors" });
  },

  // the Pipeline board
  (u) => {
    const phrase = viewPhrase(u);
    if (phrase === null) return null;
    const segmentWord = /\bsegments?\b/.test(u);
    if (phrase in SEGMENT_WORDS) return view({ segment: SEGMENT_WORDS[phrase] });
    if (segmentWord && phrase in SEGMENT_NAMED) return view({ segment: SEGMENT_NAMED[phrase] });
    return null;
  },
  (u) => {
    const phrase = viewPhrase(u);
    if (phrase !== null && phrase in BAND_WORDS) return view({ band: BAND_WORDS[phrase] });
    if (/^(?:clear|reset|remove)\s+the\s+filter$/.test(u)) return view({ band: "all" });
    // Plural, or the whole view: every filter goes, the segment as well as the band.
    return /^(?:clear|reset|remove)(?:\s+(?:the|all|my))?\s+filters$|^reset\s+the\s+(?:board|view|pipeline)$/.test(u)
      ? view({ band: "all", segment: "default" })
      : null;
  },
  (u) => {
    const sort = sortFrom(u);
    return sort ? view({ sort }) : null;
  },
  (u) => {
    const m = /^(?:only\s+)?(?:(?:show|load|give|list|get)(?:\s+me)?(?:\s+the)?\s+)?(?:top\s+|first\s+)?(one|two|three|four|five|six|seven|eight|nine|ten|\d{1,2})(?:\s+deals?|\s+items?|\s+accounts?)?$/.exec(u);
    if (!m || !/\b(?:show|load|give|list|get|top|first|deals?)\b/.test(u)) return null;
    const n = toCount(m[1]);
    return n && n >= 1 ? view({ limit: Math.min(5, n) }) : null;
  },

  // pins and deals the board knows by name
  (u, ctx) => {
    const m = /^(unpin|pin)\s+(?:the\s+)?(.+?)(?:\s+deal)?$/.exec(u);
    if (!m) return null;
    const on = m[1] === "pin";
    if (/^(?:this|it|that)$/.test(m[2])) {
      const open = ctx.deals.find((d) => d.id === ctx.openDealId);
      return open ? act({ type: "pin_deal", deal_id: open.id, deal_name: open.account, on }) : null;
    }
    const at = dealAt(m[2], ctx);
    if (at !== undefined) return at ? act({ type: "pin_deal", deal_id: at.id, deal_name: at.account, on }) : null;
    const hit = resolveDeal(m[2], ctx.deals);
    return hit.deal ? act({ type: "pin_deal", deal_id: hit.deal.id, deal_name: hit.deal.account, on }) : null;
  },
  (u, ctx) => {
    const m = /^(?:open|show\s+me|show|pull\s+up|bring\s+up|look\s+at|zoom\s+in\s+on|details?\s+(?:for|on|of))\s+(?:the\s+)?(.+?)(?:\s+(?:deal|details|sheet|card|account))?$/.exec(u);
    if (!m) return null;
    const at = dealAt(m[1], ctx);
    if (at !== undefined) return at ? act({ type: "open_deal", deal_id: at.id, deal_name: at.account }) : null;
    const hit = resolveDeal(m[1], ctx.deals);
    return hit.deal ? act({ type: "open_deal", deal_id: hit.deal.id, deal_name: hit.deal.account }) : null;
  },

  // a standing order, named in words
  (u) => {
    const m = /^(?:remove|delete|cancel|stop|turn\s+off|get\s+rid\s+of|drop)(?:\s+(?:the|my|that|this))?\s+(?:(.*?)\s+)?(?:standing\s+)?(?:orders?|rules?|alerts?)(?:\s+(.*))?$/.exec(u);
    if (!m) return null;
    const which = `${m[1] ?? ""} ${m[2] ?? ""}`.replace(/\bstanding\b/g, "").replace(/\s+/g, " ").trim();
    // "Remove the first rule and add one for ...": a second request is not part of the description.
    return /\b(?:and|then|also|but|plus)\b/.test(which) ? null : { kind: "remove_rule", which };
  },
];

/** What the user meant: a command the page can run now, or words for the assistant. */
export function interpret(raw: string, ctx: Context): Intent {
  const { text, woke } = stripWake(raw);
  const u = tidy(norm(text));
  if (!u) return woke ? { kind: "wake" } : { kind: "model", text: raw.trim() };
  if (ctx.confirming || ctx.dialog === "confirm") {
    if (YES.test(u)) return { kind: "yes" };
    if (NO.test(u)) return { kind: "no" };
  } else if (ctx.awaiting && DECLINE.test(u)) {
    return { kind: "no" };
  }
  if (REST.test(u)) return { kind: "rest" };
  for (const rule of RULES) {
    const hit = rule(u, ctx, text);
    if (hit) return hit;
  }
  return { kind: "model", text };
}

// Several commands in one breath ("go to pipeline and show only at-risk"). Only when every part is a
// command of its own and none of them asks a question of the user (sign out, forget, clear): one part
// that needs the assistant sends the whole sentence to it, which can do all of it.
const JOINERS = /\s+(?:and\s+then|and\s+also|then|and|also)\s+|\s*[,;]\s*(?:and\s+|then\s+)?/;
const MAX_PARTS = 4;
const ASKS_USER = new Set(["sign_out", "forget_data", "clear_activity"]);
// Commands that change the session, taken only as the recognizer's best guess (see `pickHeard`).
const NOT_GUESSED = new Set(["clear_conversation", "hands_free", "wake_word", "set_name"]);

/** What was meant, as one intent or, for a sentence of several commands, one intent per command. */
export function interpretAll(raw: string, ctx: Context): Intent[] {
  const whole = interpret(raw, ctx);
  if (whole.kind !== "model") return [whole];
  const { text } = stripWake(raw);
  const parts = text.split(JOINERS).map((p) => p.trim()).filter(Boolean);
  if (parts.length < 2 || parts.length > MAX_PARTS) return [whole];
  const intents = parts.map((p) => interpret(p, ctx));
  const ok = intents.every((i) => i.kind === "action" && !ASKS_USER.has(i.action.type));
  return ok ? intents : [whole];
}

/** Whether the words name a deal the page knows, in a run of one to three of them. */
function namesDeal(text: string, deals: readonly DealRef[]): boolean {
  const words = significant(text);
  for (let len = 1; len <= 3; len++) {
    for (let i = 0; i + len <= words.length; i++) {
      const hit = resolveDeal(words.slice(i, i + len).join(" "), deals);
      if (hit.deal || hit.ambiguous.length) return true;
    }
  }
  return false;
}

/** Which of the recognizer's guesses (best first) the user said. Its best guess stands unless it
 *  means nothing here and a close runner-up is a command or names a deal on the page: recognition
 *  hears "open castle bio" for "open Kestrel Biotech", or "watch for" for "Watchfloor". Only a
 *  near-identical guess may win, so a question is never swapped for a different command. */
export function pickHeard(alternatives: readonly string[], ctx: Context): string {
  const top = alternatives[0] ?? "";
  if (alternatives.length < 2) return top;
  if (interpretAll(top, ctx)[0].kind !== "model") return top;
  // A runner-up may only be a harmless page command: never a yes, nor anything that ends, clears or
  // signs out ("clean" heard as "clear" must not wipe the conversation).
  const harmless = (t: string): boolean =>
    interpretAll(t, ctx).every((i) => i.kind === "action" && !ASKS_USER.has(i.action.type) && !NOT_GUESSED.has(i.action.type));
  const near = alternatives.slice(1).filter((t) => t.trim() && similarity(norm(top), norm(t)) >= 0.6);
  const better = near.find(harmless);
  if (better) return better;
  return namesDeal(top, ctx.deals) ? top : (near.find((t) => namesDeal(t, ctx.deals)) ?? top);
}

const PAGE_HELP: Record<PageName, string> = {
  watch: "On Watch, ask about your pipeline: what needs my attention, why is a deal at risk, dig into a deal, or save a standing order.",
  pipeline:
    "On the Pipeline you can say: show only at-risk deals, show enterprise, sort by amount, show the top three, open the first deal, or open a deal by name, then pin it.",
  orders: "On Orders you can say: remove the first rule, or remove the rule about a deal. To add one, tell me what to watch for.",
  activity: "On Activity you can say: show only errors, show only the changes, show all calls, or clear the log.",
  guide: "On the Guide you can say: read this page, scroll down, or open settings.",
};

/** What to say next, for the page the user is on, followed by what works everywhere. */
export function helpText(page: PageName): string {
  return `${PAGE_HELP[page]} Anywhere, you can say: go to pipeline, orders or activity; dark mode; speak slower; read this page; repeat that; stop. ` +
    "Say keep it short or more detail from now on to set how long my answers are. " +
    "If I'm waiting on a change, say no or cancel that, or tell me what to change. " +
    "In hands-free mode, just keep talking; say that's all when you're done.";
}

export const HELP_TEXT =
  "You can say: go to pipeline, orders, activity or the guide. Show only at-risk deals. Sort by amount. " +
  "Open a deal by name. Dark mode. Speak slower. Read this page. Stop. Repeat that. " +
  "Say keep it short or more detail to set how long my answers are, and stop interrupting me to quiet my updates. " +
  "Or ask anything about your pipeline, like what needs my attention, or why is a deal at risk. " +
  "If I'm waiting on a change, say no or cancel that, or tell me what to change. " +
  "In hands-free mode, just keep talking; say that's all when you're done.";
