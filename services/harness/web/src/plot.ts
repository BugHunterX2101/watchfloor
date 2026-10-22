// The plot: the pipeline drawn as contacts on a watch-stander's scope (ADR-032). Range is health
// (the closer a deal sits to the centre, the more it needs you; rings at 35 and 60 are where the
// At risk and Watch bands begin), bearing is the stage, mark size is the amount. The centre is the
// assistant's lamp and follows the voice state; a sweep turns while motion is allowed and each
// contact answers as it passes. Every mark is a word, a shape and a colour together, and each one is
// a button with a spoken-style label, so the plot reads without colour, without motion and without
// sight (the same facts are in the board and in the summary line beside it).

import { BAND_EDGES, BAND_LABEL, plotContacts, plotPoint, plotRange, type Band, type PlotDeal } from "./logic";

const NS = "http://www.w3.org/2000/svg";
const SIZE = 400;
const C = SIZE / 2;
/** One turn of the sweep, in seconds (the CSS animation and the contacts' echoes share it). */
export const SWEEP_S = 6;

const SHAPE: Record<Band, (x: number, y: number, s: number) => string> = {
  at_risk: (x, y, s) => `<path d="M${x} ${(y - s * 1.15).toFixed(1)}L${(x + s).toFixed(1)} ${(y + s * 0.75).toFixed(1)}H${(x - s).toFixed(1)}Z"/>`,
  watch: (x, y, s) =>
    `<rect x="${(x - s * 0.85).toFixed(1)}" y="${(y - s * 0.85).toFixed(1)}" width="${(s * 1.7).toFixed(1)}" height="${(s * 1.7).toFixed(1)}" rx="${(s * 0.2).toFixed(1)}"/>`,
  healthy: (x, y, s) => `<circle cx="${x}" cy="${y}" r="${s.toFixed(1)}"/>`,
};

function el<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number> = {}): SVGElementTagNameMap[K] {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  return node;
}

interface Box {
  x1: number;
  x2: number;
  y1: number;
  y2: number;
}

export interface PlotHandle {
  /** Draw these deals (a briefing's, most urgent first). Names are optional: a remembered plot has none. */
  set(deals: readonly PlotDeal[]): void;
  /** How many contacts are showing. */
  count(): number;
  /** Re-align the echoes with the sweep (after the plot was hidden and shown again). */
  sync(): void;
}

export interface PlotOptions {
  /** A contact was chosen (click, Enter or Space). */
  pick?(id: string, account: string): void;
  /** The accessible name of the whole figure. */
  label: string;
  /** Leave the stage names off the rim. */
  compact?: boolean;
}

/** Mounts a plot into `root` (an empty element). The static parts are drawn once; `set` redraws the contacts. */
export function mountPlot(root: HTMLElement, opts: PlotOptions): PlotHandle {
  const svg = el("svg", { viewBox: `-34 -34 ${SIZE + 68} ${SIZE + 68}`, class: "plot-svg", role: "group" });
  svg.setAttribute("aria-label", opts.label);

  // The sweep's fading wedge, in the sweep's own coordinates (the centre is 0,0 there). The stops take
  // `currentColor` from the svg, so the theme sets the svg's colour.
  const uid = Math.random().toString(36).slice(2, 8);
  const tail = plotPoint(-40, 1, SIZE);
  const defs = el("defs");
  defs.innerHTML =
    `<linearGradient id="sweep-${uid}" gradientUnits="userSpaceOnUse" x1="${tail.x - C}" y1="${tail.y - C}" x2="0" y2="${-C}">` +
    `<stop offset="0" stop-color="currentColor" stop-opacity="0"/>` +
    `<stop offset="1" stop-color="currentColor" stop-opacity=".3"/></linearGradient>`;
  svg.append(defs);

  // The bands as faint zones, inside out: at risk, then watch. Healthy is the open water beyond.
  const zones = el("g", { class: "plot-zones", "aria-hidden": "true" });
  const rRisk = C * plotRange(BAND_EDGES.at_risk);
  const rWatch = C * plotRange(BAND_EDGES.watch);
  zones.append(
    el("circle", { class: "zone watch", cx: C, cy: C, r: rWatch }),
    el("circle", { class: "zone at_risk", cx: C, cy: C, r: rRisk }),
  );
  svg.append(zones);

  // The graticule: rim, band rings, the core, and bearing ticks every 10° (long every 30°).
  const grid = el("g", { class: "plot-grid", "aria-hidden": "true" });
  grid.append(
    el("circle", { class: "rim", cx: C, cy: C, r: C }),
    el("circle", { class: "band-ring at_risk", cx: C, cy: C, r: rRisk }),
    el("circle", { class: "band-ring watch", cx: C, cy: C, r: rWatch }),
    el("circle", { class: "core", cx: C, cy: C, r: C * plotRange(0) }),
  );
  for (let a = 0; a < 360; a += 10) {
    const long = a % 30 === 0;
    const p1 = plotPoint(a, 1, SIZE);
    const p2 = plotPoint(a, long ? 0.95 : 0.975, SIZE);
    grid.append(el("line", { class: long ? "tick long" : "tick", x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y }));
  }
  for (const [edge, r] of [
    [BAND_EDGES.at_risk, rRisk],
    [BAND_EDGES.watch, rWatch],
  ] as const) {
    const t = el("text", { class: "ring-label", x: C + 5, y: C - r + 13 });
    t.textContent = String(edge);
    grid.append(t);
  }
  svg.append(grid);

  const sectors = el("g", { class: "plot-sectors", "aria-hidden": "true" });
  svg.append(sectors);

  // The sweep: a 40° wedge trailing a bright line, drawn around 0,0 inside a group moved to the
  // centre, so CSS turns the inner group about its own origin (no transform-box guesswork).
  const turn = el("g", { class: "sweep-turn" });
  turn.append(
    el("path", { class: "sweep-wedge", d: `M0 0L${tail.x - C} ${tail.y - C}A${C} ${C} 0 0 1 0 ${-C}Z`, fill: `url(#sweep-${uid})` }),
    el("line", { class: "sweep-line", x1: 0, y1: 0, x2: 0, y2: -C }),
  );
  const sweep = el("g", { class: "plot-sweep", "aria-hidden": "true", transform: `translate(${C} ${C})` });
  sweep.append(turn);
  svg.append(sweep);

  const marks = el("g", { class: "plot-marks" });
  svg.append(marks);

  // The lamp: the assistant at the centre of the scope (body[data-voice] styles it).
  const lamp = el("g", { class: "lamp", "aria-hidden": "true" });
  lamp.append(
    el("circle", { class: "lamp-ripple", cx: C, cy: C, r: C * 0.1 }),
    el("circle", { class: "lamp-ripple two", cx: C, cy: C, r: C * 0.1 }),
    el("circle", { class: "lamp-halo", cx: C, cy: C, r: C * 0.095 }),
    el("circle", { class: "lamp-core", cx: C, cy: C, r: C * 0.05 }),
  );
  svg.append(lamp);

  root.replaceChildren(svg);
  root.classList.add("plot");
  let shown = 0;

  function set(deals: readonly PlotDeal[]): void {
    const { contacts, sectors: list } = plotContacts(deals.slice(0, 12));
    shown = contacts.length;
    root.dataset.contacts = String(contacts.length);

    // Stage sectors: a divider on each boundary and the stage's name just outside the rim.
    sectors.replaceChildren();
    for (const s of list) {
      if (list.length > 1) {
        const edge = plotPoint(s.start, 1, SIZE);
        const inner = plotPoint(s.start, plotRange(0), SIZE);
        sectors.append(el("line", { class: "divider", x1: inner.x, y1: inner.y, x2: edge.x, y2: edge.y }));
      }
      if (!opts.compact && s.stage) {
        const at = plotPoint((s.start + s.end) / 2, 1.07, SIZE);
        const t = el("text", { class: "stage-label", x: at.x, y: at.y, "text-anchor": "middle", "dominant-baseline": "middle" });
        t.textContent = s.stage.toUpperCase();
        sectors.append(t);
      }
    }

    marks.replaceChildren();
    // Labels go where they collide with no mark and no earlier label: beside the mark on its own
    // side first, then the other side, then a line above or below. The most urgent are placed first.
    const taken: Box[] = contacts.map((c) => {
      const p = plotPoint(c.angle, c.range, SIZE);
      const s = 5 + c.weight * 5;
      return { x1: p.x - s, x2: p.x + s, y1: p.y - s, y2: p.y + s };
    });
    for (const r of [rRisk, rWatch]) taken.push({ x1: C + 2, x2: C + 26, y1: C - r + 2, y2: C - r + 19 }); // "35", "60"
    const place = (x: number, y: number, s: number, chars: number): { x: number; y: number; right: boolean } => {
      const w = chars * 7 + 26;
      const prefer = x >= C;
      let first: { x: number; y: number; right: boolean } | null = null;
      for (const dy of [0, -17, 17, -32, 32]) {
        for (const right of [prefer, !prefer]) {
          const lx = right ? x + s + 7 : x - s - 7;
          const box = { x1: right ? lx : lx - w, x2: right ? lx + w : lx, y1: y + dy - 9, y2: y + dy + 9 };
          const spot = { x: lx, y: y + dy, right };
          first ??= spot;
          if (!taken.some((b) => b.x1 < box.x2 && box.x1 < b.x2 && b.y1 < box.y2 && box.y1 < b.y2)) {
            taken.push(box);
            return spot;
          }
        }
      }
      return first ?? { x: x + s + 7, y, right: true };
    };

    contacts.forEach((c, i) => {
      const { x, y } = plotPoint(c.angle, c.range, SIZE);
      const s = 5 + c.weight * 5;
      const named = !!c.account;
      const g = el("g", {
        class: `contact ${c.band}`,
        style: `--delay:${((c.angle / 360) * SWEEP_S).toFixed(2)}s;--i:${i}`,
      });
      g.setAttribute("role", named && opts.pick ? "button" : "img");
      if (named && opts.pick) g.setAttribute("tabindex", "0");
      g.setAttribute(
        "aria-label",
        `${named ? `${c.account}: ` : ""}${BAND_LABEL[c.band]}, health ${c.health}${c.stage ? `, ${c.stage}` : ""}`,
      );
      // An invisible target big enough for a finger at phone size (and where the focus ring is drawn).
      const hit = el("circle", { class: "hit", cx: x, cy: y, r: 26 });
      // A thin bearing line from the core to the contact: the eye reads range as distance.
      const from = plotPoint(c.angle, plotRange(0), SIZE);
      const leader = el("line", { class: "leader", x1: from.x, y1: from.y, x2: x, y2: y });
      const echo = el("circle", { class: "echo", cx: x, cy: y, r: (s + 4).toFixed(1) });
      const mark = el("g", { class: "mark" });
      mark.innerHTML = SHAPE[c.band](x, y, s);
      g.append(hit, leader, echo, mark);
      if (named) {
        const account = c.account ?? "";
        const shown = account.length > 20 ? `${account.slice(0, 19)}…` : account;
        const at = place(x, y, s, shown.length);
        const label = el("text", {
          class: "contact-label",
          x: at.x.toFixed(1),
          y: (at.y + 1).toFixed(1),
          "text-anchor": at.right ? "start" : "end",
          "dominant-baseline": "middle",
        });
        label.textContent = shown;
        const value = el("tspan", { class: "contact-health", dx: 5 });
        value.textContent = String(c.health);
        label.append(value);
        g.append(label);
      }
      if (named && opts.pick) {
        const choose = (): void => opts.pick?.(c.id, c.account ?? "");
        g.addEventListener("click", choose);
        g.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            choose();
          }
        });
      }
      marks.append(g);
    });
    sync();
  }

  /** New contacts start their echo when they are drawn, the sweep when it was: put them on the
   *  sweep's clock, so each answers just as the line crosses it. */
  function sync(): void {
    const lead = turn.getAnimations()[0];
    if (!lead) return;
    // The same start on the document timeline (a pending animation's currentTime would drift).
    void lead.ready.then(() => {
      for (const a of marks.getAnimations({ subtree: true })) a.startTime = lead.startTime;
    });
  }

  set([]);
  return { set, count: () => shown, sync };
}
