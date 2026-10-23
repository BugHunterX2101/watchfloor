// Checks the numbers behind the pages: `npm test` (Node's own runner; Node 24 runs the .ts directly).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  arrangeDeals,
  boardTotals,
  callStats,
  compactUsd,
  dealTitle,
  dragLevel,
  fmtMs,
  greeting,
  hash32,
  healthOf,
  percentile,
  promptSafe,
  pushRecent,
  relativeTime,
  ringDash,
  sinceLabel,
  bellWords,
  orderExamples,
  PLOT_CORE,
  plotContacts,
  plotPoint,
  plotRange,
  roundFigure,
  shipsWatch,
  splitSummary,
  spokenMs,
  startersFor,
  toBriefing,
  toExplain,
  togglePin,
} from "../src/logic.ts";

const deal = (id, over = {}) => ({
  deal_id: id,
  name: id,
  account: "Acme",
  amount_cents: 100_00,
  amount_display: "$100",
  stage: "negotiation",
  owner: "Priya",
  score: 50,
  health: 50,
  band: "watch",
  top_reason: "",
  new_since_last_briefing: false,
  ...over,
});

test("greeting follows the local hour and never says morning at night", () => {
  assert.equal(greeting(5), "Good morning.");
  assert.equal(greeting(11, " Priya "), "Good morning, Priya.");
  assert.equal(greeting(12), "Good afternoon.");
  assert.equal(greeting(17), "Good afternoon.");
  assert.equal(greeting(18), "Good evening.");
  assert.equal(greeting(21), "Good evening.");
  assert.equal(greeting(22), "Burning the midnight oil.");
  assert.equal(greeting(3), "Burning the midnight oil.");
  assert.equal(greeting(24), "Burning the midnight oil.");
  assert.equal(greeting(-1), "Burning the midnight oil.");
  assert.equal(greeting(Number.NaN), "Good afternoon.");
});

test("percentile is nearest-rank and safe on empty input", () => {
  assert.equal(percentile([], 95), 0);
  assert.equal(percentile([10], 95), 10);
  const ten = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
  assert.equal(percentile(ten, 50), 5);
  assert.equal(percentile(ten, 95), 10);
  assert.equal(percentile(ten, 90), 9);
  assert.equal(percentile(ten, 0), 1);
  assert.equal(percentile(ten, 400), 10);
});

test("callStats counts errors and writes and rounds the mean", () => {
  const writes = new Set(["confirm_action"]);
  const s = callStats(
    [
      { tool: "get_risk_briefing", ms: 40, ok: true },
      { tool: "confirm_action", ms: 120, ok: true },
      { tool: "explain_deal_risk", ms: 41, ok: false },
    ],
    writes,
  );
  assert.deepEqual(s, { count: 3, errors: 1, writes: 1, errorRate: 1 / 3, avg: 67, p50: 41, p95: 120, max: 120 });
  assert.deepEqual(callStats([], writes), { count: 0, errors: 0, writes: 0, errorRate: 0, avg: 0, p50: 0, p95: 0, max: 0 });
});

test("relativeTime steps through seconds, minutes, hours and days", () => {
  const now = 1_000_000_000_000;
  assert.equal(relativeTime(now - 10_000, now), "just now");
  assert.equal(relativeTime(now + 5_000, now), "just now"); // clock skew never shows a negative
  assert.equal(relativeTime(now - 3 * 60_000, now), "3 min ago");
  assert.equal(relativeTime(now - 2 * 3_600_000, now), "2 h ago");
  assert.equal(relativeTime(now - 24 * 3_600_000, now), "yesterday");
  assert.equal(relativeTime(now - 5 * 86_400_000, now), "5 days ago");
});

test("durations read the way a person says them", () => {
  assert.equal(fmtMs(0), "0 ms");
  assert.equal(fmtMs(344.4), "344 ms");
  assert.equal(fmtMs(999.4), "999 ms");
  assert.equal(fmtMs(999.6), "1 s", "never prints 1000 ms");
  assert.equal(fmtMs(1000), "1 s");
  assert.equal(fmtMs(1234), "1.2 s");
  assert.equal(fmtMs(10_202), "10 s");
  assert.equal(fmtMs(19_165), "19 s");
  assert.equal(fmtMs(-5), "0 ms");
  assert.equal(fmtMs(Number.NaN), "0 ms");
  assert.equal(spokenMs(347), "347 milliseconds");
  assert.equal(spokenMs(1000), "1 second");
  assert.equal(spokenMs(10_202), "10.2 seconds");
});

test("a summary splits into its headline and what follows", () => {
  const s = "2 deals are at risk and 5 more are on watch; 2 new since your last briefing. Top risk: Kestrel Biotech, $84K. No two-way contact for 16 days.";
  assert.deepEqual(splitSummary(s), {
    head: "2 deals are at risk and 5 more are on watch; 2 new since your last briefing.",
    rest: "Top risk: Kestrel Biotech, $84K. No two-way contact for 16 days.",
  });
  assert.deepEqual(splitSummary("All clear."), { head: "All clear.", rest: "" });
  assert.deepEqual(splitSummary("  No full stop  "), { head: "No full stop", rest: "" });
  assert.deepEqual(splitSummary("Worth $1.2M. Call now."), { head: "Worth $1.2M.", rest: "Call now." }, "a decimal point is not the end of a sentence");
  assert.deepEqual(splitSummary(""), { head: "", rest: "" });
});

test("recents keep the newest first, drop repeats and cap the list", () => {
  assert.deepEqual(pushRecent(["a", "b"], "c"), ["c", "a", "b"]);
  assert.deepEqual(pushRecent(["a", "b"], "A"), ["A", "b"]);
  assert.deepEqual(pushRecent(["a"], "   "), ["a"]);
  assert.equal(pushRecent(["1", "2", "3", "4", "5", "6"], "7").length, 6);
  assert.equal(pushRecent([], "x".repeat(300))[0].length, 120);
});

test("pins toggle and stop at the limit", () => {
  assert.deepEqual(togglePin([], "d1"), { pins: ["d1"], full: false });
  assert.deepEqual(togglePin(["d1"], "d1"), { pins: [], full: false });
  assert.deepEqual(togglePin(["a", "b"], "c", 2), { pins: ["a", "b"], full: true });
});

test("deals sort by urgency then health; pinned lead; filters combine", () => {
  const deals = [
    deal("ok", { band: "healthy", health: 90, amount_cents: 500 }),
    deal("w1", { band: "watch", health: 60, amount_cents: null }),
    deal("r1", { band: "at_risk", health: 30, amount_cents: 900, name: "Kestrel" }),
    deal("r2", { band: "at_risk", health: 20, amount_cents: 100, account: "Harbor & Pine" }),
  ];
  const f = { band: "all", query: "", sort: "urgency", pins: [] };
  assert.deepEqual(arrangeDeals(deals, f).map((d) => d.deal_id), ["r2", "r1", "w1", "ok"]);
  assert.deepEqual(arrangeDeals(deals, { ...f, pins: ["ok"] }).map((d) => d.deal_id)[0], "ok");
  assert.deepEqual(arrangeDeals(deals, { ...f, sort: "amount" }).map((d) => d.deal_id), ["r1", "ok", "r2", "w1"]);
  assert.deepEqual(arrangeDeals(deals, { ...f, band: "at_risk", query: "harbor" }).map((d) => d.deal_id), ["r2"]);
  assert.deepEqual(arrangeDeals(deals, { ...f, query: "zzz" }), []);
  assert.equal(deals[0].deal_id, "ok", "the input list is not reordered");
});

test("the health ring arc is health percent of the circumference", () => {
  const { arc, gap, circumference } = ringDash(75, 20);
  assert.ok(Math.abs(circumference - 2 * Math.PI * 20) < 1e-9);
  assert.ok(Math.abs(arc - circumference * 0.75) < 1e-9);
  assert.ok(Math.abs(arc + gap - circumference) < 1e-9);
  assert.equal(ringDash(150, 10).gap, 0);
  assert.equal(ringDash(-5, 10).arc, 0);
  assert.equal(ringDash(Number.NaN, 10).arc, 0);
});

test("board totals sum only known amounts", () => {
  const t = boardTotals([
    deal("a", { band: "at_risk", amount_cents: 1_200_000 }),
    deal("b", { band: "at_risk", amount_cents: null }),
    deal("c", { band: "healthy" }),
  ]);
  assert.deepEqual(t, { byBand: { at_risk: 2, watch: 0, healthy: 1 }, atRiskCents: 1_200_000, unknownAmounts: 1 });
});

test("compact money reads at a glance", () => {
  assert.equal(compactUsd(95_000), "$950");
  assert.equal(compactUsd(4_800_000), "$48K");
  assert.equal(compactUsd(120_000_000), "$1.2M");
  assert.equal(compactUsd(100_000_000), "$1M");
  assert.equal(compactUsd(35_000_000_000), "$350M");
  assert.equal(compactUsd(-5), "$0");
});

test("health is 100 minus risk, clamped, and prefers the server's own number", () => {
  assert.equal(healthOf(62), 38);
  assert.equal(healthOf(62, 40), 40);
  assert.equal(healthOf(120), 0);
  assert.equal(healthOf(-4), 100);
  assert.equal(healthOf(50, Number.NaN), 50);
  assert.equal(healthOf(33.4), 67);
});

test("since-labels use the weekday inside a week and the date after", () => {
  const now = Date.parse("2026-10-17T12:00:00Z");
  assert.equal(sinceLabel("2026-10-14T09:00:00Z", now, "UTC"), "since Wed");
  assert.equal(sinceLabel("2026-10-05T09:00:00Z", now, "UTC"), "since Oct 5");
  assert.equal(sinceLabel("not a date", now, "UTC"), "");
});

test("a briefing is checked and health is filled in", () => {
  const ok = toBriefing({
    summary: "Two deals need you.",
    all_clear: false,
    generated_at: "2026-10-17T10:00:00-07:00",
    new_count: 1,
    attention_count: 2,
    triggered_rules: [{ rule_id: "rule_1", description: "Quiet 7 days", deal_ids: ["dl_1", 3] }],
    deals: [{ deal_id: "dl_1", name: "Kestrel Biotech · Expansion", band: "at_risk", score: 62, top_reason: "No reply" }],
  });
  assert.equal(ok.deals[0].health, 38);
  assert.equal(ok.deals[0].account, "Kestrel Biotech · Expansion");
  assert.equal(ok.deals[0].amount_cents, null);
  assert.deepEqual(ok.triggered_rules[0].deal_ids, ["dl_1"]);
  assert.equal(toBriefing(null), null);
  assert.equal(toBriefing({ summary: "x", deals: [{ deal_id: "d", name: "n", band: "weird", score: 1 }] }), null);
  assert.equal(toBriefing({ summary: "x", deals: "no" }), null);
});

test("an explain result becomes health over time, oldest first", () => {
  const ex = toExplain({
    summary: "s",
    deal: { deal_id: "dl_1", name: "Kestrel", account: "Kestrel Biotech", stage: "negotiation", owner: "Priya", close_date_display: "Oct 30" },
    risk: { score: 70, band: "at_risk", band_since: "2026-10-14T09:00:00Z", components: { r: 0.8, i: 0.2, g: 0, s: 0.5 } },
    reasons: [{ code: "quiet", factor: "R", text: "No reply in 9 days." }],
    other_findings: [{ code: "x", factor: "Z", text: "odd" }],
    trend_14d: [40, 55, 70],
  });
  assert.equal(ex.health, 30);
  assert.deepEqual(ex.trend, [60, 45, 30]);
  assert.deepEqual(ex.components, { R: 0.8, I: 0.2, G: 0, S: 0.5 });
  assert.equal(ex.other[0].factor, "R", "an unknown factor falls back instead of breaking the page");
  assert.equal(toExplain({ deal: {}, risk: {} }), null);
});

test("drag words, deal titles", () => {
  assert.deepEqual([0.9, 0.6, 0.59, 0.3, 0.1, 0].map(dragLevel), ["High", "High", "Some", "Some", "Low", "None"]);
  assert.equal(dealTitle("Kestrel Biotech", "Kestrel Biotech · Platform expansion"), "Platform expansion");
  assert.equal(dealTitle("Kestrel Biotech", "Kestrel Biotech"), "Kestrel Biotech");
  assert.equal(dealTitle("Acme", "Renewal"), "Renewal");
});

test("the plot puts health on range and stage on bearing, stably and without overlaps", () => {
  const deals = [
    { id: "dl_a", band: "at_risk", health: 29, stage: "Proposal", amount_cents: 84_000_00 },
    { id: "dl_b", band: "at_risk", health: 34, stage: "Negotiation", amount_cents: 52_000_00 },
    { id: "dl_c", band: "watch", health: 37, stage: "Proposal", amount_cents: 24_000_00 },
    { id: "dl_d", band: "watch", health: 42, stage: "Demo", amount_cents: null },
  ];
  const { contacts, sectors } = plotContacts(deals);
  assert.deepEqual(
    sectors.map((s) => [s.stage, s.start, s.end]),
    [["Proposal", 0, 120], ["Negotiation", 120, 240], ["Demo", 240, 360]],
    "one sector per stage, in board order",
  );
  assert.deepEqual(plotContacts(deals), { contacts, sectors }, "stable between calls");
  for (const c of contacts) {
    const s = sectors.find((x) => x.stage === deals.find((d) => d.id === c.id).stage);
    assert.ok(c.angle >= s.start && c.angle < s.end + 11, `${c.id} sits in its stage's sector`);
  }
  assert.equal(contacts[0].weight, 1, "the largest amount is the largest mark");
  assert.equal(contacts[3].weight, 0.35, "an unknown amount gets a middling mark");
  assert.ok(contacts[0].range < contacts[3].range, "lower health sits closer to the centre");
  assert.equal(plotRange(0), PLOT_CORE);
  assert.equal(plotRange(100), 1);
  assert.equal(plotRange(Number.NaN), PLOT_CORE);
  const twins = plotContacts([
    { id: "x1", band: "watch", health: 50, stage: "Demo" },
    { id: "x1", band: "watch", health: 50, stage: "Demo" },
  ]).contacts;
  assert.ok(Math.abs(twins[0].angle - twins[1].angle) >= 9, "marks on top of each other are nudged apart");
  assert.deepEqual(plotContacts([]), { contacts: [], sectors: [] });
  assert.deepEqual(plotPoint(0, 1, 200), { x: 100, y: 0 }, "bearing 0 is twelve o'clock");
  assert.deepEqual(plotPoint(90, 0.5, 200), { x: 150, y: 100 });
  assert.equal(hash32("dl_a"), hash32("dl_a"));
  assert.notEqual(hash32("dl_a"), hash32("dl_b"));
});

test("ship's time names the watch and counts its bells", () => {
  assert.deepEqual(shipsWatch(0, 0), { watch: "Middle watch", bells: 8 });
  assert.deepEqual(shipsWatch(0, 30), { watch: "Middle watch", bells: 1 });
  assert.deepEqual(shipsWatch(3, 59), { watch: "Middle watch", bells: 7 });
  assert.deepEqual(shipsWatch(4, 0), { watch: "Morning watch", bells: 8 });
  assert.deepEqual(shipsWatch(9, 45), { watch: "Forenoon watch", bells: 3 });
  assert.deepEqual(shipsWatch(14, 0), { watch: "Afternoon watch", bells: 4 });
  assert.deepEqual(shipsWatch(16, 30), { watch: "First dog watch", bells: 1 });
  assert.deepEqual(shipsWatch(18, 30), { watch: "Last dog watch", bells: 5 });
  assert.deepEqual(shipsWatch(22, 0), { watch: "First watch", bells: 4 });
  assert.deepEqual(shipsWatch(23, 59), { watch: "First watch", bells: 7 });
  assert.deepEqual(shipsWatch(Number.NaN, Number.NaN), { watch: "Middle watch", bells: 8 });
  assert.equal(bellWords(1), "1 bell");
  assert.equal(bellWords(6), "6 bells");
});

test("suggestions come from the board as it is", () => {
  assert.deepEqual(startersFor(null), ["What needs my attention today?", "What am I watching?"]);
  const b = {
    new_count: 1,
    deals: [
      deal("a", { account: "Kestrel Biotech", band: "at_risk" }),
      deal("b", { account: "Harbor & Pine", band: "at_risk" }),
    ],
  };
  assert.deepEqual(startersFor(b), [
    "What needs my attention today?",
    "Why is Kestrel Biotech at risk?",
    "How's Harbor & Pine doing?",
    "What changed since my last briefing?",
  ]);
  const calm = startersFor({ new_count: 0, deals: [deal("c", { account: "Northgate", band: "healthy" })] });
  assert.deepEqual(calm, ["What needs my attention today?", "How's Northgate doing?", "What should I do first?", "What am I watching?"]);
  assert.equal(startersFor({ new_count: 0, deals: [deal("d", { account: "Bad\nName", band: "at_risk" })] })[1], "Why is Bad Name at risk?");
  // A deal on watch is not at risk, so it is not asked about as if it were.
  assert.equal(startersFor({ new_count: 0, deals: [deal("e", { account: "Orchard" })] })[1], "How's Orchard doing?");
});

test("standing-order examples are sized to the board", () => {
  assert.equal(roundFigure(84_000), 50_000);
  assert.equal(roundFigure(31_000), 25_000);
  assert.equal(roundFigure(12_000), 10_000);
  assert.equal(roundFigure(0), 0);
  const deals = [
    deal("a", { amount_cents: 84_000_00, health: 29, stage: "Proposal", band: "at_risk" }),
    deal("b", { amount_cents: 52_000_00, health: 34 }),
    deal("c", { amount_cents: 31_000_00, health: 42 }),
  ];
  assert.deepEqual(orderExamples(deals), [
    "Flag deals over $50,000 with no buyer reply for 7 days.",
    "Alert me about proposal-stage deals that go quiet for 5 days.",
    "Watch deals whose health drops to 30 or lower.",
  ]);
  assert.deepEqual(orderExamples([]), [
    "Flag deals over $50,000 with no buyer reply for 7 days.",
    "Alert me about negotiation-stage deals that go quiet for 5 days.",
    "Watch deals whose health drops to 30 or lower.",
  ]);
});

test("prompt text drops control characters and is capped", () => {
  assert.equal(promptSafe("Kestrel\n\t Renewal\u0000"), "Kestrel Renewal");
  assert.equal(promptSafe("x".repeat(200)).length, 80);
});
