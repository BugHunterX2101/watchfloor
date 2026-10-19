// Voice precision: a spoken command never swallows a second request, a name is a name, and "the
// second one" is the second deal on the board the user can see (`npm test`; Node 24 runs the .ts directly).
import assert from "node:assert/strict";
import { test } from "node:test";
import { dealAt, helpText, interpret, interpretAll, plausibleName } from "../src/voice/grammar.ts";

const deals = [
  { id: "dl_kestrel", account: "Kestrel Biotech", name: "Kestrel Biotech · Platform expansion" },
  { id: "dl_harbor", account: "Harbor & Pine", name: "Harbor & Pine · Renewal" },
  { id: "dl_orbital", account: "Orbital Freight", name: "Orbital Freight · Pilot" },
];
const rules = [
  { id: "rule_a", description: "Flag deals over $50,000 with no buyer reply for 7 days." },
  { id: "rule_b", description: "Watch enterprise deals whose health drops to 30 or lower." },
];
const ctx = (over = {}) => ({ page: "pipeline", signedIn: true, dialog: "none", confirming: false, deals, rules, openDealId: null, ...over });
const action = (said, over) => {
  const i = interpret(said, ctx(over));
  return i.kind === "action" ? i.action : i;
};

test("a sort command is the whole sentence, or it is not a sort command", () => {
  assert.deepEqual(action("sort by amount"), { type: "pipeline_view", sort: "amount" });
  assert.deepEqual(action("sort by deal size"), { type: "pipeline_view", sort: "amount" });
  assert.deepEqual(action("sort by risk"), { type: "pipeline_view", sort: "urgency" });
  assert.deepEqual(action("sort alphabetically"), { type: "pipeline_view", sort: "name" });
  assert.deepEqual(action("biggest deals first"), { type: "pipeline_view", sort: "amount" });
  assert.deepEqual(action("lowest health first"), { type: "pipeline_view", sort: "health" });
  // Anything else in the sentence: not run as a sort, so nothing is dropped.
  for (const said of [
    "sort the board by amount and read the first one",
    "sort by amount and tell me about Kestrel",
    "sort by health then open Kestrel and pin it",
    "sort by the thing I care about",
  ]) {
    assert.equal(interpret(said, ctx()).kind, "model", said);
  }
});

test("two requests in one breath are two commands, or one sentence for the assistant", () => {
  const kinds = (said) => interpretAll(said, ctx()).map((i) => (i.kind === "action" ? i.action.type : i.kind));
  assert.deepEqual(kinds("sort by amount and show only at risk"), ["pipeline_view", "pipeline_view"]);
  assert.deepEqual(kinds("sort by health then go to orders"), ["pipeline_view", "navigate"]);
  assert.deepEqual(kinds("dark mode and speak slower"), ["set_theme", "set_speech_rate"]);
  // a part the grammar does not own sends the whole sentence on, so no part is lost
  assert.deepEqual(kinds("sort by amount and tell me about Kestrel"), ["model"]);
  assert.deepEqual(kinds("remove the first rule and add one for deals over a hundred thousand"), ["model"]);
});

test("'call me ...' saves a name, not a sentence", () => {
  assert.deepEqual(action("call me Priya"), { type: "set_name", name: "Priya" });
  assert.deepEqual(action("my name is Ana Maria"), { type: "set_name", name: "Ana Maria" });
  assert.deepEqual(action("you can call me boss"), { type: "set_name", name: "boss" });
  for (const said of ["call me back when the review is done", "call me later", "call me if Kestrel drops", "call me when it finishes"]) {
    assert.equal(interpret(said, ctx()).kind, "model", said);
  }
  assert.equal(plausibleName("one two three four"), null);
  assert.equal(plausibleName("R2D2!"), null);
  assert.equal(plausibleName("O'Brien"), "O'Brien");
});

test("removing an order takes the description, not the rest of the sentence", () => {
  assert.deepEqual(interpret("remove the first rule", ctx()), { kind: "remove_rule", which: "first" });
  assert.equal(interpret("remove the first rule and add one for deals over a hundred thousand", ctx()).kind, "model");
  assert.equal(interpret("delete the enterprise alert then show me the board", ctx()).kind, "model");
});

test("pages by their bare names, and the spellings recognition splits", () => {
  const nav = (said, page) => assert.deepEqual(action(said), { type: "navigate", page }, said);
  nav("pipeline", "pipeline");
  nav("Orders.", "orders");
  nav("standing orders", "orders");
  nav("activity", "activity");
  nav("guide", "guide");
  nav("go to pipe line", "pipeline");
  nav("open the dashboard", "pipeline");
  nav("go to order", "orders");
  nav("go to standing order", "orders");
  // words that also mean something else stay with the assistant
  for (const said of ["watch", "start", "deals", "rules", "overview"]) assert.equal(interpret(said, ctx()).kind, "model", said);
  nav("home", "watch"); // an older rule: "home" has no other meaning
});

test("board filters: reset, a trailing 'only', letters spelt out, and 'the first two'", () => {
  const view = (said, v) => assert.deepEqual(action(said), { type: "pipeline_view", ...v }, said);
  view("clear filters", { band: "all", segment: "default" });
  view("remove the filters", { band: "all", segment: "default" });
  view("reset the board", { band: "all", segment: "default" });
  view("clear the filter", { band: "all" }); // singular: the band only, as before
  view("at risk deals only", { band: "at_risk" });
  view("healthy only", { band: "healthy" });
  view("show me S M B", { segment: "smb" });
  view("only show the first two", { limit: 2 });
});

test("complaints about the screen or the pace are requests for the obvious fix", () => {
  assert.deepEqual(action("too bright"), { type: "set_theme", theme: "dark" });
  assert.deepEqual(action("it's too dark"), { type: "set_theme", theme: "light" });
  assert.deepEqual(action("you're talking too fast"), { type: "set_speech_rate", step: "slower" });
  assert.deepEqual(action("too slow"), { type: "set_speech_rate", step: "faster" });
  assert.deepEqual(action("try the previous voice"), { type: "change_voice", step: "previous" });
  assert.equal(interpret("too many deals", ctx()).kind, "model");
});

test("'the second one' is the second deal on the board the user can see", () => {
  // The briefing's order and the board's differ once the user has sorted or filtered.
  const board = ["dl_orbital", "dl_kestrel", "dl_harbor"];
  const opens = (said, id, over) => assert.equal(action(said, { board, ...over }).deal_id, id, said);
  opens("open the first deal", "dl_orbital");
  opens("open the top deal", "dl_orbital");
  opens("open the second one", "dl_kestrel");
  opens("show me the third deal", "dl_harbor");
  opens("open the last deal", "dl_harbor");
  opens("open deal number two", "dl_kestrel");
  assert.deepEqual(action("pin the second deal", { board }), { type: "pin_deal", deal_id: "dl_kestrel", deal_name: "Kestrel Biotech", on: true });
  assert.deepEqual(action("unpin the first one", { board }), { type: "pin_deal", deal_id: "dl_orbital", deal_name: "Orbital Freight", on: false });
  // nothing in that place: not guessed
  assert.equal(interpret("open the fifth deal", ctx({ board })).kind, "model");
  // without a board, the briefing order stands
  assert.equal(action("open the first deal").deal_id, "dl_kestrel");
  // a name that merely starts like a place is still a name
  assert.equal(dealAt("first bank", { deals, board }), undefined);
  assert.equal(dealAt("second", { deals: [], board: [] }), null);
});

test("help is about the page the user is on", () => {
  assert.match(helpText("pipeline"), /sort by amount/);
  assert.match(helpText("orders"), /remove the first rule/);
  assert.match(helpText("activity"), /show only errors/);
  assert.match(helpText("guide"), /read this page/);
  assert.match(helpText("watch"), /standing order/);
  assert.notEqual(helpText("pipeline"), helpText("orders"));
  for (const page of ["watch", "pipeline", "orders", "activity", "guide"]) assert.match(helpText(page), /that's all/);
  assert.equal(interpret("what can I say here", ctx()).kind, "help");
});
