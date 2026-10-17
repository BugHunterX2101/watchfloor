// The companion layer: changing your mind mid-conversation, preferences by voice, several commands in
// one breath, what the assistant is told about the person, and what it notices on the board (`npm test`).
import assert from "node:assert/strict";
import { test } from "node:test";
import { ACTION_NAMES, parseAction } from "../src/voice/actions.ts";
import { boardOf, parseAwaiting, partOfDay, profileOf } from "../src/voice/context.ts";
import { interpret, interpretAll, isAnswer } from "../src/voice/grammar.ts";
import { COOLDOWN_MS, compare, DROP_POINTS, MAX_ITEMS, snapshot } from "../src/voice/watcher.ts";

const deals = [
  { id: "dl_kestrel", account: "Kestrel Biotech", name: "Kestrel Biotech · Platform expansion" },
  { id: "dl_harbor", account: "Harbor & Pine", name: "Harbor & Pine · Renewal" },
];
const ctx = (over = {}) => ({
  page: "watch",
  signedIn: true,
  dialog: "none",
  confirming: false,
  deals,
  rules: [],
  openDealId: null,
  ...over,
});
const act = (said, over) => {
  const i = interpret(said, ctx(over));
  return i.kind === "action" ? i.action : i;
};

// --- changing your mind ---

test("a plain no, or 'cancel that', drops a change that is waiting, with no model call", () => {
  const waiting = ctx({ awaiting: true });
  for (const said of ["no", "No thanks", "nope", "cancel that", "never mind", "Watchfloor, cancel it", "don't do it", "forget it", "scrap that"]) {
    assert.deepEqual(interpret(said, waiting), { kind: "no" }, said);
  }
});

test("nothing is declined when nothing is waiting, and 'wait' or 'stop' are not a decline", () => {
  assert.notEqual(interpret("no", ctx()).kind, "no");
  assert.notEqual(interpret("cancel that", ctx()).kind, "no");
  for (const said of ["wait", "hold on", "stop", "hmm no wait make it forty", "no make it friday"]) {
    assert.notEqual(interpret(said, ctx({ awaiting: true })).kind, "no", said);
  }
});

test("a correction goes to the assistant, which redoes the change with the new detail", () => {
  for (const said of ["make it forty", "no, for Friday instead", "not that one, Harbor and Pine"]) {
    assert.equal(interpret(said, ctx({ awaiting: true })).kind, "model", said);
  }
});

test("the page's own question still takes yes and no first", () => {
  assert.deepEqual(interpret("yes", ctx({ dialog: "confirm", awaiting: true })), { kind: "yes" });
  assert.deepEqual(interpret("no", ctx({ dialog: "confirm", awaiting: true })), { kind: "no" });
  // A yes to a change the assistant is holding is the assistant's to confirm, not the page's.
  assert.equal(interpret("yes", ctx({ awaiting: true })).kind, "model");
});

test("isAnswer: a bare yes, no or cancel, nothing more", () => {
  for (const said of ["yes", "Yes, go ahead", "no", "cancel that", "Watchfloor, never mind", "okay"]) assert.equal(isAnswer(said), true, said);
  for (const said of ["make it forty", "yes and show orders", "no wait make it friday", "what's on fire"]) assert.equal(isAnswer(said), false, said);
});

// --- preferences by voice ---

test("how long answers are, said the way people say it", () => {
  for (const said of ["keep it short", "Keep your answers brief", "shorter answers", "be concise", "less detail", "just the headlines", "brief mode"]) {
    assert.deepEqual(act(said), { type: "set_style", style: "brief" }, said);
  }
  for (const said of ["detailed answers", "give me longer replies", "be more thorough", "more detail from now on"]) {
    assert.deepEqual(act(said), { type: "set_style", style: "detailed" }, said);
  }
  assert.deepEqual(act("normal length"), { type: "set_style", style: "normal" });
  assert.deepEqual(act("back to normal answers"), { type: "set_style", style: "normal" });
});

test("a question about detail is a question, not a setting", () => {
  for (const said of ["give me more detail on Kestrel", "why is it so brief", "explain more"]) {
    assert.equal(interpret(said, ctx()).kind, "model", said);
  }
});

test("speaking up about changes can be turned off and on", () => {
  for (const said of ["stop interrupting me", "don't interrupt", "no interruptions", "turn off proactive updates", "disable announcements"]) {
    assert.deepEqual(act(said), { type: "set_alerts", on: false }, said);
  }
  for (const said of ["tell me when anything changes", "keep me posted", "turn on the updates"]) {
    assert.deepEqual(act(said), { type: "set_alerts", on: true }, said);
  }
});

test("the new actions are on the list and checked like the rest", () => {
  assert.ok(ACTION_NAMES.includes("set_style") && ACTION_NAMES.includes("set_alerts"));
  assert.deepEqual(parseAction({ type: "set_style", style: "brief", x: 1 }), { type: "set_style", style: "brief" });
  assert.equal(parseAction({ type: "set_style", style: "rude" }), null);
  assert.equal(parseAction({ type: "set_style" }), null);
  assert.deepEqual(parseAction({ type: "set_alerts", on: false }), { type: "set_alerts", on: false });
  assert.equal(parseAction({ type: "set_alerts", on: "no" }), null);
});

// --- several commands in one breath ---

test("two or more commands in one sentence run in order", () => {
  const intents = interpretAll("go to the pipeline and show only at risk", ctx());
  assert.deepEqual(
    intents.map((i) => i.action),
    [
      { type: "navigate", page: "pipeline" },
      { type: "pipeline_view", band: "at_risk" },
    ],
  );
  const three = interpretAll("dark mode, then speak slower, and go to orders", ctx());
  assert.deepEqual(three.map((i) => i.action.type), ["set_theme", "set_speech_rate", "navigate"]);
});

test("a sentence with a part that needs the assistant goes to it whole", () => {
  for (const said of [
    "go to the pipeline and tell me what is wrong with Kestrel",
    "dark mode and sign me out",
    "go to the pipeline and tell me why",
  ]) {
    const all = interpretAll(said, ctx());
    assert.equal(all.length, 1, said);
    assert.notEqual(all[0].kind, "action", said);
  }
});

test("a deal whose name contains 'and' is still one command", () => {
  const all = interpretAll("open Harbor and Pine", ctx());
  assert.equal(all.length, 1);
  assert.deepEqual(all[0].action, { type: "open_deal", deal_id: "dl_harbor", deal_name: "Harbor & Pine" });
});

test("a single command is still one intent, and at most four are chained", () => {
  assert.equal(interpretAll("go to orders", ctx()).length, 1);
  assert.equal(interpretAll("go to orders", ctx())[0].kind, "action");
  assert.equal(interpretAll("go to orders, then activity, then guide, then pipeline, then watch", ctx()).length, 1);
});

// --- who it is talking to ---

test("the time of day, in the greeting's own boundaries", () => {
  assert.deepEqual(
    [4, 5, 11, 12, 17, 18, 21, 22, 23, 0].map(partOfDay),
    ["night", "morning", "morning", "afternoon", "afternoon", "evening", "evening", "night", "night", "night"],
  );
});

test("the profile carries only what the server accepts, trimmed", () => {
  const friday = new Date(2026, 9, 23, 19, 5); // Fri 23 Oct 2026, 19:05 local
  assert.deepEqual(profileOf({ name: "  Priya   Shah ", style: "brief" }, ["Kestrel Biotech", "  ", "Harbor & Pine"], friday), {
    day: "Friday",
    part: "evening",
    style: "brief",
    name: "Priya Shah",
    pins: ["Kestrel Biotech", "Harbor & Pine"],
  });
  const bare = profileOf({ name: "", style: "normal" }, [], friday);
  assert.equal("name" in bare, false);
  assert.equal("pins" in bare, false);
  const many = profileOf({ name: "x".repeat(100), style: "normal" }, Array.from({ length: 9 }, (_, i) => `Deal ${i}`), friday);
  assert.equal(many.name.length, 40);
  assert.equal(many.pins.length, 5);
});

test("the board as listed: the first five, ids the tools can use, health a whole number", () => {
  const rows = Array.from({ length: 7 }, (_, i) => ({
    deal_id: `dl_${i}`,
    account: `Account ${i}`,
    name: `Account ${i} · Renewal`,
    band: "watch",
    health: 40.6 + i,
  }));
  const listed = boardOf([{ deal_id: "../x", account: "Bad", name: "Bad", band: "watch", health: 1 }, ...rows]);
  assert.equal(listed.length, 5);
  assert.deepEqual(listed[0], { id: "dl_0", name: "Account 0", band: "watch", health: 41 });
  assert.deepEqual(boardOf([{ deal_id: "dl_x", account: "", name: "Name Only", band: "healthy", health: 140 }]), [
    { id: "dl_x", name: "Name Only", band: "healthy", health: 100 },
  ]);
});

test("what the server says is waiting is checked before the page believes it", () => {
  assert.equal(parseAwaiting(null), null);
  assert.equal(parseAwaiting({ kind: "hack", summary: "x", until: 1 }), null);
  assert.equal(parseAwaiting({ kind: "preview", summary: "x", until: "soon" }), null);
  assert.deepEqual(parseAwaiting({ kind: "preview", summary: "Call Maya.", until: 1_800_000_000, fresh: true }), {
    kind: "preview",
    summary: "Call Maya.",
    until: 1_800_000_000,
    fresh: true,
  });
  assert.equal(parseAwaiting({ kind: "undo", summary: "Done.", until: 5 }).fresh, false);
});

// --- what it notices on the board ---

const deal = (id, account, band, health) => ({ deal_id: id, account, name: `${account} · Deal`, band, health });
const board = (list, rules = []) =>
  snapshot({ summary: "", all_clear: false, generated_at: "", new_count: 0, attention_count: 0, triggered_rules: rules, deals: list });
const NOW = 1_000_000;

test("nothing is said when nothing changed", () => {
  const a = board([deal("d1", "Kestrel", "at_risk", 38), deal("d2", "Harbor", "watch", 55)]);
  assert.equal(compare(a, a, new Map(), NOW), null);
  const wobble = board([deal("d1", "Kestrel", "at_risk", 36), deal("d2", "Harbor", "watch", 52)]);
  assert.equal(compare(a, wobble, new Map(), NOW), null, "a small move inside the band is not news");
});

test("a deal slipping to at risk is said first, with its health", () => {
  const before = board([deal("d1", "Kestrel", "watch", 52), deal("d2", "Harbor", "healthy", 80)]);
  const after = board([deal("d1", "Kestrel", "at_risk", 38), deal("d2", "Harbor", "watch", 66)]);
  const news = compare(before, after, new Map(), NOW);
  assert.ok(news);
  assert.equal(news.say, "Heads up. Kestrel slipped to at risk, health 38. Harbor moved to watch, health 66.");
  assert.deepEqual([...news.keys].sort(), ["d1:risk", "d2:watch"]);
});

test("a sharp fall inside a band, a recovery, and a new at-risk deal each get a line", () => {
  const before = board([deal("d1", "Kestrel", "watch", 70), deal("d2", "Harbor", "at_risk", 30)]);
  const after = board([
    deal("d1", "Kestrel", "watch", 70 - DROP_POINTS),
    deal("d2", "Harbor", "healthy", 82),
    deal("d3", "Northwind", "at_risk", 25),
  ]);
  const news = compare(before, after, new Map(), NOW);
  assert.ok(news);
  assert.match(news.say, /A new at-risk deal: Northwind, health 25\./);
  assert.match(news.say, /Kestrel dropped 10 points, to health 60\./);
  assert.match(news.say, /Good news: Harbor is healthy again, health 82\./);
  assert.ok(news.say.indexOf("Northwind") < news.say.indexOf("Kestrel") && news.say.indexOf("Kestrel") < news.say.indexOf("Good news"));
});

test("a standing order that has just triggered is said once", () => {
  const rule = { rule_id: "rule_1", description: "Flag deals over $50,000 with no buyer reply for 7 days.", deal_ids: ["d1"] };
  const before = board([deal("d1", "Kestrel", "watch", 52)]);
  const after = board([deal("d1", "Kestrel", "watch", 52)], [rule]);
  const news = compare(before, after, new Map(), NOW);
  assert.ok(news);
  assert.match(news.say, /Your standing order “Flag deals over \$50,000 with no buyer reply for 7 days\.” just triggered\./);
  assert.equal(compare(after, after, new Map(), NOW), null, "still triggered is not new");
});

test("the same news is not repeated within the cooldown, and comes back after it", () => {
  const before = board([deal("d1", "Kestrel", "watch", 52)]);
  const after = board([deal("d1", "Kestrel", "at_risk", 38)]);
  const news = compare(before, after, new Map(), NOW);
  assert.ok(news);
  const told = new Map(news.keys.map((k) => [k, NOW]));
  assert.equal(compare(before, after, told, NOW + COOLDOWN_MS - 1), null);
  assert.ok(compare(before, after, told, NOW + COOLDOWN_MS));
});

test("a long list is trimmed to the few that matter, and the rest are counted", () => {
  const ids = ["a", "b", "c", "d", "e"];
  const before = board(ids.map((id) => deal(id, id.toUpperCase(), "watch", 55)));
  const after = board(ids.map((id) => deal(id, id.toUpperCase(), "at_risk", 30)));
  const news = compare(before, after, new Map(), NOW);
  assert.ok(news);
  assert.equal((news.say.match(/slipped to at risk/g) ?? []).length, MAX_ITEMS);
  assert.match(news.say, /And 2 more changes on the board\.$/);
  assert.equal(news.keys.length, 5);
});

test("the wording is made for the ear: no ids, no markup", () => {
  const before = board([deal("dl_01J9KESTREL00000000000000", "Kestrel Biotech", "watch", 52)]);
  const after = board([deal("dl_01J9KESTREL00000000000000", "Kestrel Biotech", "at_risk", 38)]);
  const news = compare(before, after, new Map(), NOW);
  assert.ok(news);
  assert.equal(/dl_|[*_#`]/.test(news.say), false);
});
