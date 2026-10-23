// Checks the numbers behind the chart behind the pages (src/backdrop-logic.ts, ADR-035): `npm test`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  ACTIVE_MS,
  CAMERAS,
  CAPS,
  IDLE_AFTER_MS,
  IDLE_MS,
  MOODS,
  approach,
  buildGrid,
  buildMotes,
  contrast,
  createGovernor,
  frameInterval,
  gridFor,
  horizonY,
  isSoftwareRenderer,
  luminance,
  over,
  parseColor,
  poolAt,
  project,
  swellAt,
  tintOf,
  viewProjection,
} from "../src/backdrop-logic.ts";

const close = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) <= eps, `${a} ≉ ${b}`);

test("colours are read from the forms the stylesheet uses", () => {
  assert.deepEqual(parseColor(" #ffffff "), [1, 1, 1]);
  assert.deepEqual(parseColor("#000"), [0, 0, 0]);
  assert.deepEqual(parseColor("#ff000080"), [1, 0, 0]);
  assert.deepEqual(parseColor("rgb(255 0 0)"), [1, 0, 0]);
  assert.deepEqual(parseColor("rgb(255, 0, 255, 0.5)"), [1, 0, 1]);
  assert.deepEqual(parseColor("rgb(0 255 0 / 0.16)"), [0, 1, 0]);
  for (const bad of ["", "red", "#12", "#12345", "rgb(1 2)", "var(--x)"]) assert.equal(parseColor(bad), null, bad);
});

// The tokens, read from the stylesheet itself so a palette change re-runs this proof.
function tokens(block) {
  const out = {};
  for (const [, name, value] of block.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{3,8})\s*;/g)) out[name] = parseColor(value);
  return out;
}
const css = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
const darkTokens = tokens(css.slice(css.indexOf(":root {"), css.indexOf("@media (prefers-color-scheme: light)")));
const lightTokens = tokens(css.slice(css.indexOf(':root[data-theme="light"]'), css.indexOf("@supports")));

test("the faintest text keeps 4.5:1 over the brightest the chart can make the page", () => {
  // The brightest page colour in dark: --bg-2 under the CSS lamp glow (7% of --signal, see .atmosphere).
  const dark = { base: over(darkTokens["bg-2"], darkTokens.signal, 0.07), t: darkTokens, cap: CAPS.dark, toward: [0, 0, 0] };
  // In light the page only darkens: --bg is the darker of its two paper colours.
  const light = { base: lightTokens.bg, t: lightTokens, cap: CAPS.light, toward: [1, 1, 1] };
  for (const [name, { base, t, cap, toward }] of Object.entries({ dark, light })) {
    // Every area is the lamp's hue at the cap's luminance, the sea pushed to a clear blue and the
    // glints at the lamp's own saturation. A blend of two colours is never brighter than the brighter
    // (nor darker than the darker) in luminance, so proving the tints proves every pixel of wash.
    for (const saturation of [cap.sea, 1]) {
      const tint = tintOf(t.signal, cap.tint, toward, saturation);
      close(luminance(tint), cap.tint, 2e-3 * cap.tint + 1e-4);
      const washed = over(base, tint, cap.wash);
      const texts = { faint: t.faint, muted: t.muted, text: t.text, crit: t.crit, warn: t.warn, ok: t.ok };
      for (const [ink, colour] of Object.entries(texts)) {
        if (!colour) continue;
        assert.ok(contrast(colour, washed) >= 4.5, `${name} --${ink} over the ${saturation}x tint: ${contrast(colour, washed).toFixed(2)}`);
        // And over the page between the tint and the plain background, the worst case for the one
        // that lightens or darkens against it.
        const half = over(base, tint, 0.5);
        assert.ok(contrast(colour, half) >= 4.5, `${name} --${ink} at half: ${contrast(colour, half).toFixed(2)}`);
      }
    }
    // The hairlines stay well short of the text colour itself.
    assert.ok(cap.line <= 0.25, name);
  }
});

test("the water is the lamp's own hue, and is visibly not the plain page", () => {
  for (const [scheme, tokens, toward] of [["dark", darkTokens, [0, 0, 0]], ["light", lightTokens, [1, 1, 1]]]) {
    const sea = tintOf(tokens.signal, CAPS[scheme].tint, toward, CAPS[scheme].sea);
    const hue = (c) => Math.atan2(Math.sqrt(3) * (c[1] - c[2]), 2 * c[0] - c[1] - c[2]);
    assert.ok(Math.abs(hue(sea) - hue(tokens.signal)) < 0.45, `${scheme}: the sea keeps the lamp's blue`);
    // The light theme has little room: its faintest text sets how far the paper may darken.
    const step = scheme === "dark" ? 1.2 : 1.12;
    assert.ok(contrast(sea, tokens.bg) >= step, `${scheme}: the sea is a visible step from the page (${contrast(sea, tokens.bg).toFixed(2)})`);
  }
});

test("the stylesheet's --sea is the proven tint, so a card highlight never outshines the page", () => {
  for (const [scheme, t, toward] of [["dark", darkTokens, [0, 0, 0]], ["light", lightTokens, [1, 1, 1]]]) {
    assert.ok(t.sea, `${scheme}: --sea is defined`);
    const sea = tintOf(t.signal, CAPS[scheme].tint, toward, CAPS[scheme].sea);
    for (let i = 0; i < 3; i++) close(t.sea[i], sea[i], 1.5 / 255);
    // A highlight is the sea laid over the card's own surface: never brighter than the brighter of the two
    // (nor, in light, darker than the darker), so what proves the page proves the card.
    for (const ink of ["faint", "muted", "text"]) {
      for (const alpha of [0.5, 1]) {
        const lit = over(t.surface, t.sea, alpha);
        assert.ok(contrast(t[ink], lit) >= 4.5, `${scheme} --${ink} on a lit card at ${alpha}: ${contrast(t[ink], lit).toFixed(2)}`);
      }
    }
  }
});

test("tintOf finds the luminance it is asked for, from either side", () => {
  // The lamp's own luminance lies between the two ends, so each end reaches its own side of it.
  for (const [toward, targets] of [[[0, 0, 0], [0.004, 0.0165, 0.2]], [[1, 1, 1], [0.5, 0.735, 0.9]]]) {
    for (const target of targets) {
      const tint = tintOf([0.61, 0.79, 1], target, toward, 1.8);
      close(luminance(tint), target, 1e-4 + target * 1e-3);
    }
  }
});

test("each page has its own framing, and Pipeline looks down on the chart table", () => {
  assert.deepEqual(Object.keys(CAMERAS).sort(), ["activity", "guide", "orders", "pipeline", "watch"]);
  const watch = horizonY(CAMERAS.watch);
  assert.ok(watch > 0.2 && watch < 0.6, `Watch's horizon sits in the top third: ${watch}`);
  assert.ok(horizonY(CAMERAS.pipeline) > 1, "Pipeline shows no horizon");
  for (const cam of Object.values(CAMERAS)) assert.ok(cam.height > 1.2, "the camera stays clear of the floor");
});

test("the projection puts the far horizon where horizonY says, and the floor below it", () => {
  for (const cam of Object.values(CAMERAS)) {
    const m = viewProjection({ ...cam, yaw: 0 }, 16 / 9);
    const [x, y] = project(m, 0, 0, -10_000);
    close(x, 0, 1e-6);
    close(y, horizonY(cam), 2e-3);
    const [, nearY] = project(m, 0, 0, -8);
    assert.ok(nearY < y, "nearer ground is lower on the screen");
    const [, , depth] = project(m, 0, 0, -20);
    assert.ok(depth > -1 && depth < 1, "the floor is inside the clip range");
  }
});

test("the scene eases, never jumps", () => {
  close(approach(0, 10, 0, 500), 0);
  close(approach(0, 10, 500, 500), 10 * (1 - Math.exp(-1)));
  close(approach(0, 10, 1e9, 500), 10);
  assert.equal(approach(3, 7, 16, 0), 7);
  assert.equal(approach(3, 7, -5, 100), 3, "time never runs backwards");
});

test("the voice state is a mood: thinking hurries, listening hushes and brightens, an error dims", () => {
  assert.ok(MOODS.thinking.drift > MOODS.idle.drift);
  assert.ok(MOODS.listening.drift < MOODS.idle.drift && MOODS.listening.pool > MOODS.idle.pool);
  assert.ok(MOODS.error.pool < MOODS.idle.pool);
});

test("the lamp's pool wanders slowly and stays in front of the camera", () => {
  for (let t = 0; t < 4000; t += 7) {
    const p = poolAt(t);
    assert.ok(Math.abs(p.x) <= 6.1 && p.z >= 5.5 && p.z <= 11.5 && p.radius > 4.7, `t=${t}`);
    const q = poolAt(t + 1 / 30);
    assert.ok(Math.hypot(q.x - p.x, q.z - p.z) < 0.01, "less than a hundredth of a unit per frame");
  }
});

test("one swell when listening starts, gone in under four seconds", () => {
  assert.equal(swellAt(-1), null);
  assert.equal(swellAt(Number.NaN), null);
  assert.equal(swellAt(-Infinity), null);
  const start = swellAt(0);
  const late = swellAt(3000);
  assert.ok(start && late && late.radius > start.radius && late.strength < start.strength);
  assert.equal(swellAt(3600), null);
});

test("pacing: thirty frames a second, ten when nothing happens, none hidden or still", () => {
  const pace = { hidden: false, still: false, sinceActivity: 0, voice: "idle" };
  assert.equal(frameInterval(pace), ACTIVE_MS);
  assert.equal(frameInterval({ ...pace, sinceActivity: IDLE_AFTER_MS }), IDLE_MS);
  for (const voice of ["listening", "thinking", "speaking"]) {
    assert.equal(frameInterval({ ...pace, sinceActivity: IDLE_AFTER_MS, voice }), ACTIVE_MS, voice);
  }
  assert.equal(frameInterval({ ...pace, sinceActivity: IDLE_AFTER_MS, voice: "error" }), IDLE_MS, "an error is a resting state");
  assert.equal(frameInterval({ ...pace, hidden: true }), null);
  assert.equal(frameInterval({ ...pace, still: true, voice: "thinking" }), null);
});

test("the governor steps the resolution down on a slow machine, then rests", () => {
  const g = createGovernor();
  // Twenty-four frames 60 ms late make 1440 ms: not yet.
  for (let i = 0; i < 24; i++) assert.equal(g.frame(60, ACTIVE_MS), null);
  assert.equal(g.frame(60, ACTIVE_MS), "lower");
  assert.equal(g.scale(), 0.75);
  // A very slow machine (three frames a second) gets there in seconds, not minutes.
  const slow = createGovernor();
  const verdicts = [];
  for (let i = 0; i < 30; i++) verdicts.push(slow.frame(330, ACTIVE_MS));
  assert.deepEqual(verdicts.filter(Boolean), ["lower", "lower", "lower", "rest", "rest", "rest"]);
  assert.equal(slow.scale(), 0.4, "never below the last step");
  g.reset();
  assert.equal(g.scale(), 1);
  for (let i = 0; i < 200; i++) assert.equal(g.frame(5000, ACTIVE_MS), null, "a throttled tab is not a slow machine");
  for (let i = 0; i < 500; i++) assert.equal(g.frame(i % 5 ? 33 : 60, ACTIVE_MS), null, "an occasional late frame is not a slow machine");
  assert.equal(g.scale(), 1);
});

test("software renderers are known by name", () => {
  for (const name of [
    "ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)",
    "llvmpipe (LLVM 15.0.7, 256 bits)",
    "ANGLE (Microsoft, Microsoft Basic Render Driver Direct3D11 vs_5_0 ps_5_0, D3D11)",
  ]) {
    assert.ok(isSoftwareRenderer(name), name);
  }
  for (const name of ["ANGLE (Intel, Intel(R) Iris(R) Xe Graphics (0x00009A49) Direct3D11 vs_5_0 ps_5_0, D3D11)", "Apple M2", "Mali-G78", "unknown"]) {
    assert.ok(!isSoftwareRenderer(name), name);
  }
});

test("the mesh covers the fan with in-range indices, for both sizes", () => {
  for (const coarse of [false, true]) {
    const { cols, rows, motes } = gridFor(coarse);
    const { vertices, indices } = buildGrid(cols, rows);
    assert.equal(vertices.length, cols * rows * 2);
    assert.equal(indices.length, (cols - 1) * (rows - 1) * 6);
    assert.ok(Math.max(...indices) < cols * rows);
    assert.deepEqual([vertices[0], vertices[1]], [-1, 0]);
    assert.deepEqual([vertices.at(-2), vertices.at(-1)], [1, 1]);
    assert.equal(buildMotes(motes).length, motes * 4);
  }
  assert.ok(gridFor(true).cols * gridFor(true).rows < gridFor(false).cols * gridFor(false).rows);
  assert.throws(() => buildGrid(300, 300), RangeError);
});

test("the motes are the same on every visit", () => {
  assert.deepEqual(buildMotes(8), buildMotes(8));
  for (const v of buildMotes(64)) assert.ok(v >= -1 && v <= 1);
});
