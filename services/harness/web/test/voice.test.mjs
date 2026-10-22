// Voice: what the page makes of what it heard (`npm test`; Node 24 runs the .ts directly).
import assert from "node:assert/strict";
import { test } from "node:test";
import { ACTION_NAMES, MAX_ACTIONS, NEEDS_YES, parseAction, parseActions } from "../src/voice/actions.ts";
import { hasWake, interpret, resolveDeal, resolveRule, stripWake } from "../src/voice/grammar.ts";
import { asksSomething, isEcho, levenshtein, norm, ordinal, phonetic, similarity, speakable, toCount } from "../src/voice/text.ts";

const deals = [
  { id: "dl_kestrel", account: "Kestrel Biotech", name: "Kestrel Biotech · Platform expansion" },
  { id: "dl_harbor", account: "Harbor & Pine", name: "Harbor & Pine · Renewal" },
  { id: "dl_north", account: "Northwind Traders", name: "Northwind Traders · Pilot" },
  { id: "dl_north2", account: "Northwind Logistics", name: "Northwind Logistics · Expansion" },
];
const rules = [
  { id: "rule_a", description: "Flag deals over $50,000 with no buyer reply for 7 days." },
  { id: "rule_b", description: "Watch enterprise deals whose health drops to 30 or lower." },
  { id: "rule_c", description: "Alert me about negotiation-stage deals that go quiet for 5 days." },
];
const ctx = (over = {}) => ({
  page: "watch",
  signedIn: true,
  dialog: "none",
  confirming: false,
  deals,
  rules,
  openDealId: null,
  ...over,
});
const action = (said, over) => {
  const i = interpret(said, ctx(over));
  return i.kind === "action" ? i.action : i;
};

// --- text ---

test("norm folds case, punctuation, apostrophes and ampersands", () => {
  assert.equal(norm("  Harbor & Pine's  Renewal!! "), "harbor and pines renewal");
  assert.equal(norm("Mid-market, SMB"), "mid market smb");
});

test("levenshtein and similarity", () => {
  assert.equal(levenshtein("kitten", "sitting"), 3);
  assert.equal(levenshtein("", "abc"), 3);
  assert.equal(similarity("same", "same"), 1);
  assert.equal(similarity("", ""), 1);
  assert.ok(similarity("abc", "xyz") < 0.01);
});

test("phonetic folding meets the spellings speech recognition picks", () => {
  assert.equal(phonetic("kestral"), phonetic("kestrel"));
  assert.equal(phonetic("phoenix"), phonetic("fenix"));
  assert.notEqual(phonetic("harbor"), phonetic("kestrel"));
});

test("counts and ordinals", () => {
  assert.equal(toCount("three"), 3);
  assert.equal(toCount("4"), 4);
  assert.equal(toCount("banana"), null);
  assert.equal(ordinal("Second"), 2);
  assert.equal(ordinal("last"), -1);
  assert.equal(ordinal("sixth"), null);
});

test("speakable turns a screen reply into something a voice reads well", () => {
  assert.equal(speakable("**Kestrel** is at risk — health 38/100."), "Kestrel is at risk, health 38 out of 100.");
  assert.equal(
    speakable("Amount: $1.2M, then $48k, then $12,400, then $950."),
    "Amount: 1.2 million dollars, then 48 thousand dollars, then 12,400 dollars, then 950 dollars.",
  );
  assert.equal(speakable("Down 12% from last week."), "Down 12 percent from last week.");
  assert.equal(speakable("See [the board](https://x.test/a?b=1) or https://y.test/z"), "See the board or a link");
  assert.equal(speakable("Rule rule_01J9ABCD is set for deal dl_01J9HARBORPINE."), "Rule is set for deal .");
  assert.equal(speakable("- one\n- two\n1. three"), "one two three");
  assert.equal(speakable("Harbor & Pine"), "Harbor and Pine");
  assert.equal(speakable("Owner · Maya"), "Owner. Maya");
  assert.equal(speakable("```js\ncode()\n``` after"), "after");
});

test("speakable never reads out any kind of id (ids.py prefixes)", () => {
  for (const prefix of ["ws", "us", "ac", "ct", "dl", "act", "hist", "pr", "tk", "job", "rule", "al", "aud"]) {
    assert.equal(speakable(`Saved ${prefix}_01J9ABCDEF today`), "Saved today", prefix);
  }
  // Ordinary words with an underscore-free shape are left alone.
  assert.equal(speakable("The pr team asked about act two"), "The pr team asked about act two");
});

test("isEcho spots the assistant's own voice coming back in", () => {
  const said = "Kestrel Biotech is at risk. Health thirty eight. The buyer has not replied for eleven days.";
  assert.equal(isEcho("health thirty eight", said), true);
  assert.equal(isEcho("the buyer hasnt replied for eleven days", said), true); // a recognition slip
  assert.equal(isEcho("stop", "Kestrel is at risk."), false);
  assert.equal(isEcho("open the pipeline", said), false);
  assert.equal(isEcho("", said), true);
});

// --- actions ---

test("every action name is accepted when well-formed and refused when not", () => {
  assert.equal(new Set(ACTION_NAMES).size, ACTION_NAMES.length);
  assert.equal(parseAction(null), null);
  assert.equal(parseAction({ type: "rm_rf" }), null);
  assert.equal(parseAction({ type: "navigate", page: "admin" }), null);
  assert.deepEqual(parseAction({ type: "navigate", page: "orders", extra: 1 }), { type: "navigate", page: "orders" });
  assert.equal(parseAction({ type: "set_theme", theme: "neon" }), null);
  assert.equal(parseAction({ type: "set_read_aloud", on: "yes" }), null);
  assert.equal(parseAction({ type: "set_speech_rate", rate: 9 }), null);
  assert.deepEqual(parseAction({ type: "set_speech_rate", rate: 1.2345 }), { type: "set_speech_rate", rate: 1.23 });
  assert.deepEqual(parseAction({ type: "set_speech_rate", step: "slower" }), { type: "set_speech_rate", step: "slower" });
  assert.equal(parseAction({ type: "set_name", name: "   " }), null);
  assert.equal(parseAction({ type: "set_name", name: "Pri\u0000ya" }).name, "Pri ya");
  assert.equal(parseAction({ type: "pipeline_view" }), null);
  assert.equal(parseAction({ type: "pipeline_view", limit: 9 }), null);
  assert.deepEqual(parseAction({ type: "pipeline_view", band: "at_risk", limit: 3 }), { type: "pipeline_view", band: "at_risk", limit: 3 });
  assert.equal(parseAction({ type: "open_deal" }), null);
  assert.equal(parseAction({ type: "open_deal", deal_id: "../../x" }), null);
  assert.deepEqual(parseAction({ type: "pin_deal", deal_name: "Kestrel" }), { type: "pin_deal", deal_name: "Kestrel", on: true });
  assert.equal(parseAction({ type: "pin_deal", deal_name: "Kestrel", on: false }).on, false);
  assert.deepEqual(parseAction({ type: "change_voice" }), { type: "change_voice", step: "next" });
  assert.equal(parseAction({ type: "activity_view", filter: "all" }).filter, "all");
  assert.equal(parseAction({ type: "scroll", direction: "sideways" }), null);
  assert.deepEqual(parseAction({ type: "sign_out", whatever: true }), { type: "sign_out" });
  assert.deepEqual(parseAction({ type: "hands_free", on: true }), { type: "hands_free", on: true });
});

test("a turn's actions are checked one by one and capped", () => {
  const six = Array.from({ length: 6 }, () => ({ type: "refresh" }));
  assert.equal(parseActions(six).length, MAX_ACTIONS);
  assert.deepEqual(parseActions([{ type: "nope" }, { type: "refresh" }, 7]), [{ type: "refresh" }]);
  assert.deepEqual(parseActions("refresh"), []);
  assert.deepEqual([...NEEDS_YES].sort(), ["clear_activity", "forget_data", "sign_out"]);
});

// --- the wake word ---

test("the wake word, spoken one word or two", () => {
  assert.equal(hasWake("Watchfloor, open orders"), true);
  assert.equal(hasWake("hey watch floor open orders"), true);
  assert.equal(hasWake("OK Watchfloor"), true);
  assert.equal(hasWake("watch for deals under health thirty five"), false); // a request, not a wake word
  assert.equal(hasWake("the watchfloor is great"), false);
  assert.deepEqual(stripWake("Hey Watchfloor, open orders"), { text: "open orders", woke: true });
  assert.deepEqual(stripWake("open orders"), { text: "open orders", woke: false });
  assert.deepEqual(interpret("Watchfloor", ctx()), { kind: "wake" });
  assert.deepEqual(interpret("  ", ctx()), { kind: "model", text: "" });
});

// --- commands ---

test("pages, however they are asked for", () => {
  const nav = (said, page) => assert.deepEqual(action(said), { type: "navigate", page }, said);
  nav("go to the pipeline", "pipeline");
  nav("Open pipeline.", "pipeline");
  nav("take me to the orders page", "orders");
  nav("show me my standing orders", "orders");
  nav("could you please open the activity log", "activity");
  nav("watchfloor, go to the guide", "guide");
  nav("show history", "activity");
  nav("go home", "watch");
  nav("back to watch", "watch");
  nav("pipeline page", "pipeline");
  nav("open the board please", "pipeline");
  nav("open help", "guide");
});

test("settings, close, back and the keyboard-free basics", () => {
  assert.deepEqual(action("open settings"), { type: "open_settings" });
  assert.deepEqual(action("close", { dialog: "settings" }), { type: "close_dialog" });
  assert.deepEqual(action("never mind", { dialog: "deal" }), { type: "close_dialog" });
  assert.deepEqual(action("never mind"), { type: "stop_speaking" }); // nothing open: it is "stop"
  assert.deepEqual(action("go back"), { kind: "back" });
  assert.deepEqual(action("close"), { kind: "model", text: "close" }); // nothing to close: the assistant decides
  assert.deepEqual(action("stop"), { type: "stop_speaking" });
  assert.deepEqual(action("be quiet"), { type: "stop_speaking" });
  assert.deepEqual(action("say that again"), { type: "repeat" });
  assert.deepEqual(action("repeat"), { type: "repeat" });
  assert.deepEqual(action("what's on this page?"), { type: "read_page" });
  assert.deepEqual(action("read this page"), { type: "read_page" });
  assert.deepEqual(action("where am I"), { type: "read_page" });
  assert.deepEqual(action("refresh"), { type: "refresh" });
  assert.deepEqual(action("scroll down"), { type: "scroll", direction: "down" });
  assert.deepEqual(action("go to the top"), { type: "scroll", direction: "top" });
  assert.deepEqual(action("what can I say"), { kind: "help" });
});

test("look and sound", () => {
  assert.deepEqual(action("dark mode"), { type: "set_theme", theme: "dark" });
  assert.deepEqual(action("switch to light theme"), { type: "set_theme", theme: "light" });
  assert.deepEqual(action("go dark"), { type: "set_theme", theme: "dark" });
  assert.deepEqual(action("use the system theme"), { type: "set_theme", theme: "auto" });
  assert.deepEqual(action("follow the system theme"), { type: "set_theme", theme: "auto" });
  assert.deepEqual(action("auto"), { kind: "model", text: "auto" }); // a bare "auto" is not a theme
  assert.deepEqual(action("mute"), { type: "set_read_aloud", on: false });
  assert.deepEqual(action("turn on the voice"), { type: "set_read_aloud", on: true });
  assert.deepEqual(action("speak slower"), { type: "set_speech_rate", step: "slower" });
  assert.deepEqual(action("speed up"), { type: "set_speech_rate", step: "faster" });
  assert.deepEqual(action("speak at normal speed"), { type: "set_speech_rate", rate: 1.05 });
  assert.deepEqual(action("reduce motion"), { type: "set_motion", motion: "calm" });
  assert.deepEqual(action("turn on the animations"), { type: "set_motion", motion: "auto" });
  assert.deepEqual(action("turn off the 3d orb"), { type: "set_orb", on: false });
  assert.deepEqual(action("show the floor"), { type: "set_floor", on: true });
  assert.deepEqual(action("hide the background"), { type: "set_floor", on: false });
  // The plot's words: the lamp and the sweep.
  assert.deepEqual(action("turn off the lamp"), { type: "set_orb", on: false });
  assert.deepEqual(action("hide the sweep"), { type: "set_floor", on: false });
  assert.deepEqual(action("stop the sweep"), { type: "set_floor", on: false });
  assert.deepEqual(action("turn on the radar sweep"), { type: "set_floor", on: true });
  assert.deepEqual(action("start the sweep"), { type: "set_floor", on: true });
  assert.deepEqual(action("show echo show eight"), { type: "set_device", device: "standard" });
  assert.deepEqual(action("echo show 15"), { type: "set_device", device: "large" });
  assert.deepEqual(action("fit to window"), { type: "set_device", device: "fit" });
  assert.deepEqual(action("change the voice"), { type: "change_voice", step: "next" });
  assert.deepEqual(action("previous voice"), { type: "change_voice", step: "previous" });
  assert.deepEqual(action("call me Priya"), { type: "set_name", name: "Priya" });
  assert.deepEqual(action("My name is Marcus Lee."), { type: "set_name", name: "Marcus Lee" });
});

test("hands-free and the wake word", () => {
  assert.deepEqual(action("hands free mode"), { type: "hands_free", on: true });
  assert.deepEqual(action("turn on hands-free"), { type: "hands_free", on: true });
  assert.deepEqual(action("stop listening"), { type: "hands_free", on: false });
  assert.deepEqual(action("goodbye"), { type: "hands_free", on: false });
  assert.deepEqual(action("see you later"), { type: "hands_free", on: false });
  assert.deepEqual(action("bye bye"), { type: "hands_free", on: false });
  assert.deepEqual(action("that's all"), { kind: "rest" }); // the conversation ends; the microphone stays on
  assert.deepEqual(action("turn off the wake word"), { type: "wake_word", on: false });
  assert.deepEqual(action("always listen"), { type: "wake_word", on: false });
  assert.deepEqual(action("only listen when I say Watchfloor"), { type: "wake_word", on: true });
});

test("sessions and data are commands the page then confirms", () => {
  assert.deepEqual(action("sign me out"), { type: "sign_out" });
  assert.deepEqual(action("log out please"), { type: "sign_out" });
  assert.deepEqual(action("forget everything"), { type: "forget_data" });
  assert.deepEqual(action("clear my saved data"), { type: "forget_data" });
  assert.deepEqual(action("clear the conversation"), { type: "clear_conversation" });
  assert.deepEqual(action("start over"), { type: "clear_conversation" });
  assert.deepEqual(action("clear the activity log"), { type: "clear_activity" });
  assert.deepEqual(action("sign in", { signedIn: false }), { kind: "signin" });
  assert.deepEqual(action("sign in"), { kind: "model", text: "sign in" }); // already signed in
});

test("the Pipeline board by voice", () => {
  const view = (said, v, over) => assert.deepEqual(action(said, over), { type: "pipeline_view", ...v }, said);
  view("show only at risk deals", { band: "at_risk" });
  view("show me the at-risk deals", { band: "at_risk" });
  view("only healthy ones", { band: "healthy" });
  view("show watch deals", { band: "watch" });
  view("show all", { band: "all" });
  view("show everything", { band: "all" });
  view("clear the filter", { band: "all" });
  view("show enterprise deals", { segment: "enterprise" });
  view("filter to small business", { segment: "smb" });
  view("show the mid-market segment", { segment: "mid" });
  view("show all segments", { segment: "all" });
  view("sort by amount", { sort: "amount" });
  view("sort the board by health", { sort: "health" });
  view("order by name", { sort: "name" });
  view("largest first", { sort: "amount" });
  view("most urgent first", { sort: "urgency" });
  view("show the top three deals", { limit: 3 });
  view("show five deals", { limit: 5 });
  view("show top 2", { limit: 2 });
  view("show ten deals", { limit: 5 }); // the briefing's own ceiling
  assert.equal(action("three").kind, "model"); // a bare number is not a command
});

test("a deal is opened or pinned by name, even when recognition spelt it differently", () => {
  assert.deepEqual(action("open Kestrel"), { type: "open_deal", deal_id: "dl_kestrel", deal_name: "Kestrel Biotech" });
  assert.deepEqual(action("pull up kestral biotech"), { type: "open_deal", deal_id: "dl_kestrel", deal_name: "Kestrel Biotech" });
  assert.deepEqual(action("show me Harbor and Pine"), { type: "open_deal", deal_id: "dl_harbor", deal_name: "Harbor & Pine" });
  assert.deepEqual(action("pin harbor"), { type: "pin_deal", deal_id: "dl_harbor", deal_name: "Harbor & Pine", on: true });
  assert.deepEqual(action("unpin the Kestrel deal"), { type: "pin_deal", deal_id: "dl_kestrel", deal_name: "Kestrel Biotech", on: false });
  assert.deepEqual(action("pin this", { openDealId: "dl_kestrel", dialog: "deal" }), {
    type: "pin_deal",
    deal_id: "dl_kestrel",
    deal_name: "Kestrel Biotech",
    on: true,
  });
  assert.equal(action("pin this").kind, "model"); // no deal is open
  assert.equal(action("open Zebra Corp").kind, "model"); // not on the board: the assistant looks it up
  assert.equal(action("open Northwind").kind, "model"); // two fit equally well: not guessed
  assert.equal(action("open Kestrel", { deals: [] }).kind, "model");
});

test("a question is never taken for a command", () => {
  for (const said of [
    "Why is Kestrel at risk?",
    "What needs my attention today?",
    "Show me why Kestrel is at risk",
    "How is Harbor and Pine doing?",
    "Tell me about the pipeline",
    "Watch for deals under health thirty five",
    "Create a follow-up task for the owner of Kestrel",
    "Remind me about the pipeline review",
    "Open a new deal for Acme",
  ]) {
    assert.equal(interpret(said, ctx()).kind, "model", said);
  }
});

test("yes and no only count while a question is waiting", () => {
  assert.equal(interpret("yes", ctx()).kind, "model"); // the assistant owns a preview's yes
  assert.equal(interpret("Yes, go ahead.", ctx({ confirming: true })).kind, "yes");
  assert.equal(interpret("yeah", ctx({ dialog: "confirm" })).kind, "yes");
  assert.equal(interpret("no thanks", ctx({ confirming: true })).kind, "no");
  assert.equal(interpret("cancel", ctx({ confirming: true })).kind, "no");
  assert.equal(interpret("don't", ctx({ confirming: true })).kind, "no");
  assert.equal(interpret("sign me out", ctx({ confirming: true })).kind, "yes"); // a repeat of the request answers it
  assert.equal(interpret("what time is it", ctx({ confirming: true })).kind, "model");
});

test("standing orders are removed by describing them", () => {
  assert.deepEqual(interpret("remove the first rule", ctx({ page: "orders" })), { kind: "remove_rule", which: "first" });
  assert.deepEqual(interpret("delete my quiet deals alert", ctx()), { kind: "remove_rule", which: "quiet deals" });
  assert.deepEqual(interpret("stop the enterprise rule please", ctx()), { kind: "remove_rule", which: "enterprise" });
  assert.deepEqual(interpret("remove the standing order", ctx()), { kind: "remove_rule", which: "" });
});

test("resolveDeal picks one deal or says it can't", () => {
  assert.equal(resolveDeal("kestrel", deals).deal.id, "dl_kestrel");
  assert.equal(resolveDeal("kestral biotech", deals).deal.id, "dl_kestrel");
  assert.equal(resolveDeal("harbor and pine", deals).deal.id, "dl_harbor");
  assert.equal(resolveDeal("northwind traders", deals).deal.id, "dl_north");
  assert.deepEqual(resolveDeal("northwind", deals).ambiguous.map((d) => d.id).sort(), ["dl_north", "dl_north2"]);
  assert.deepEqual(resolveDeal("zebra", deals), { deal: null, ambiguous: [] });
  assert.deepEqual(resolveDeal("", deals), { deal: null, ambiguous: [] });
});

test("resolveRule finds an order by position or by its words", () => {
  assert.equal(resolveRule("first", rules).rule.id, "rule_a");
  assert.equal(resolveRule("the second one", rules).rule.id, "rule_b");
  assert.equal(resolveRule("last", rules).rule.id, "rule_c");
  assert.equal(resolveRule("fifth", rules).rule, null);
  assert.equal(resolveRule("enterprise", rules).rule.id, "rule_b");
  assert.equal(resolveRule("buyer reply", rules).rule.id, "rule_a");
  assert.equal(resolveRule("negotiation", rules).rule.id, "rule_c");
  assert.equal(resolveRule("quiet", rules).rule.id, "rule_c");
  assert.equal(resolveRule("", rules).rule, null);
  assert.equal(resolveRule("", rules).ambiguous.length, 3);
  assert.equal(resolveRule("", [rules[0]]).rule.id, "rule_a");
  assert.deepEqual(resolveRule("nothing like it", rules), { rule: null, ambiguous: [] });
  assert.deepEqual(resolveRule("first", []), { rule: null, ambiguous: [] });
});

test("'that's all' ends a conversation, but only as the whole utterance", () => {
  for (const said of [
    "that's all",
    "That's it for now",
    "Thanks, that's all",
    "that's all thanks",
    "I'm done",
    "I am done",
    "we're done",
    "nothing else",
    "OK Watchfloor, that's all",
  ]) {
    assert.deepEqual(interpret(said, ctx()), { kind: "rest" }, said);
  }
  for (const said of [
    "that's all the deals over fifty thousand",
    "bye bye Kestrel",
    "I'm done with Kestrel, what's next",
    "thanks, what about Kestrel",
    "what's the final one",
    "nothing else is at risk, right",
  ]) {
    assert.notEqual(interpret(said, ctx()).kind, "rest", said);
  }
  // a question on the screen is answered first
  assert.deepEqual(interpret("no", ctx({ dialog: "confirm" })), { kind: "no" });
});

test("asksSomething: a reply that ends on a question waits for the answer", () => {
  assert.equal(asksSomething("Kestrel is at risk. Should I set up a follow up?"), true);
  assert.equal(asksSomething("Remove this standing order? Flag big deals. Say yes or no."), true);
  assert.equal(asksSomething('Did you mean "Harbor and Pine"? '), true);
  assert.equal(asksSomething("Kestrel is at risk. Health thirty eight."), false);
  assert.equal(asksSomething("Which deal do you mean? I only found one."), false);
  assert.equal(asksSomething(""), false);
});
