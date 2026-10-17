// The Activity page: every MCP tool call made this session, measured. The numbers come from the
// calls the harness API already reports (tool, server latency, ok), so nothing here is estimated:
// median and 95th percentile are nearest-rank over what was actually observed.

import type { Call } from "./api";
import { h } from "./dom";
import { callStats, relativeTime } from "./logic";
import { LABELS, WRITES } from "./trace";

export interface Entry {
  id: number;
  at: number; // epoch ms the turn finished
  source: string; // Turn · View · Pipeline · Orders
  totalMs: number;
  calls: Call[];
}

const MAX_ENTRIES = 200;
let entries: Entry[] = [];
let nextId = 1;
const subscribers = new Set<() => void>();

export function recordTurn(source: string, calls: Call[], totalMs: number): void {
  entries = [{ id: nextId++, at: Date.now(), source, totalMs, calls }, ...entries].slice(0, MAX_ENTRIES);
  for (const fn of subscribers) fn();
}

export function clearActivity(): void {
  entries = [];
  for (const fn of subscribers) fn();
}

export type Filter = "all" | "writes" | "errors";

const label = (tool: string): string => LABELS[tool] ?? tool;
const ms = (n: number): string => `${Math.round(n)} ms`;

/** The page, as voice drives it. */
export interface ActivityHandle {
  refresh(): void;
  setFilter(next: Filter): void;
  /** The session's calls in words. */
  describe(): string;
}

export function mountActivity(root: HTMLElement, host: { toWatch(): void }): ActivityHandle {
  let filter: Filter = "all";
  root.replaceChildren();

  const clearButton = h("button", { type: "button", class: "btn btn-quiet" }, "Clear");
  clearButton.addEventListener("click", clearActivity);

  const head = h(
    "header",
    { class: "page-head" },
    h(
      "div",
      null,
      h("p", { class: "eyebrow" }, "This session"),
      h("h1", { class: "page-title", "data-page-title": true }, "Activity"),
      h("p", { class: "lede" }, "Every MCP tool call, with the server's own latency. Nothing is sampled or estimated."),
    ),
    h("div", { class: "page-actions" }, clearButton),
  );
  const tiles = h("div", { class: "tiles", role: "group", "aria-label": "Summary of tool calls" });
  const byTool = h("section", { class: "panel", "aria-labelledby": "by-tool-title" });
  const timeline = h("section", { class: "panel", "aria-labelledby": "timeline-title" });
  root.append(head, tiles, byTool, timeline);

  function tile(name: string, value: string, hint: string, tone?: "bad" | "write"): HTMLElement {
    return h(
      "div",
      { class: `tile${tone ? ` ${tone}` : ""}` },
      h("p", { class: "tile-label" }, name),
      h("p", { class: "tile-value" }, value),
      h("p", { class: "tile-hint" }, hint),
    );
  }

  function renderTiles(): void {
    const s = callStats(entries.flatMap((e) => e.calls), WRITES);
    const none = s.count === 0;
    tiles.replaceChildren(
      tile("Tool calls", String(s.count), `${entries.length} ${entries.length === 1 ? "turn" : "turns"}`),
      tile("Median", none ? "—" : ms(s.p50), "Half of the calls were faster"),
      tile("95th percentile", none ? "—" : ms(s.p95), "19 in 20 calls were faster"),
      tile(
        "Errors",
        none ? "—" : String(s.errors),
        none ? "No calls yet" : `${Math.round(s.errorRate * 100)}% of calls`,
        s.errors ? "bad" : undefined,
      ),
      tile("Writes", none ? "—" : String(s.writes), "Calls that change something", s.writes ? "write" : undefined),
    );
  }

  function renderByTool(): void {
    const agg = new Map<string, { n: number; sum: number; max: number; err: number }>();
    for (const c of entries.flatMap((e) => e.calls)) {
      const a = agg.get(c.tool) ?? { n: 0, sum: 0, max: 0, err: 0 };
      a.n += 1;
      a.sum += c.ms;
      a.max = Math.max(a.max, c.ms);
      a.err += c.ok ? 0 : 1;
      agg.set(c.tool, a);
    }
    const rows = [...agg.entries()].sort((a, b) => b[1].n - a[1].n || a[0].localeCompare(b[0]));
    const top = Math.max(1, ...rows.map(([, a]) => a.n));
    byTool.replaceChildren(h("h2", { class: "panel-title", id: "by-tool-title" }, "By tool"));
    if (!rows.length) {
      byTool.append(h("p", { class: "muted" }, "No calls yet."));
      return;
    }
    const table = h("table", { class: "grid-table" });
    table.append(
      h(
        "thead",
        null,
        h(
          "tr",
          null,
          ...["Tool", "Calls", "Average", "Slowest", "Errors"].map((t, i) =>
            h("th", { scope: "col", class: i ? "num" : "" }, t),
          ),
        ),
      ),
      h(
        "tbody",
        null,
        ...rows.map(([tool, a]) => {
          const share = h(
            "span",
            { class: "share", "aria-hidden": "true" },
            h("i", { style: `width:${Math.max(4, (a.n / top) * 100)}%` }),
          );
          return h(
            "tr",
            null,
            h(
              "th",
              { scope: "row" },
              h("span", { class: "tool-name" }, label(tool)),
              h("code", { class: "tool-id" }, tool),
              WRITES.has(tool) ? h("span", { class: "tag write" }, "writes") : null,
            ),
            h("td", { class: "num" }, String(a.n), share),
            h("td", { class: "num" }, ms(a.sum / a.n)),
            h("td", { class: "num" }, ms(a.max)),
            h("td", { class: `num${a.err ? " bad" : ""}` }, String(a.err)),
          );
        }),
      ),
    );
    byTool.append(h("div", { class: "table-wrap" }, table));
  }

  const FILTERS: [Filter, string][] = [
    ["all", "All"],
    ["writes", "Writes"],
    ["errors", "Errors"],
  ];

  function choose(next: Filter): void {
    filter = next;
    render();
    root.querySelector<HTMLElement>('.seg [aria-checked="true"]')?.focus();
  }

  function renderTimeline(): void {
    const seg = h("div", { class: "seg", role: "radiogroup", "aria-label": "Filter turns" });
    for (const [id, text] of FILTERS) {
      const b = h(
        "button",
        { type: "button", role: "radio", "aria-checked": String(filter === id), tabindex: filter === id ? "0" : "-1" },
        text,
      );
      b.addEventListener("click", () => choose(id));
      seg.append(b);
    }
    seg.addEventListener("keydown", (e) => {
      const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
      if (!step) return;
      e.preventDefault();
      const i = FILTERS.findIndex(([id]) => id === filter);
      choose(FILTERS[(i + step + FILTERS.length) % FILTERS.length][0]);
    });

    const shown = entries.filter((e) =>
      filter === "all" ? true : filter === "writes" ? e.calls.some((c) => WRITES.has(c.tool)) : e.calls.some((c) => !c.ok),
    );
    timeline.replaceChildren(h("div", { class: "panel-head" }, h("h2", { class: "panel-title", id: "timeline-title" }, "Timeline"), seg));

    if (!entries.length) {
      const go = h("button", { type: "button", class: "btn btn-primary" }, "Go ask something");
      go.addEventListener("click", host.toWatch);
      timeline.append(
        h(
          "div",
          { class: "empty" },
          h("p", { class: "empty-title" }, "Nothing has run yet."),
          h("p", { class: "muted" }, "Ask a question on Watch and every tool call lands here."),
          go,
        ),
      );
      return;
    }
    if (!shown.length) {
      timeline.append(
        h("p", { class: "muted" }, filter === "writes" ? "No calls that change something yet." : "No errors. That's the good outcome."),
      );
      return;
    }
    const now = Date.now();
    timeline.append(
      h(
        "ol",
        { class: "act-list" },
        ...shown.map((e) => {
          const time = new Date(e.at);
          return h(
            "li",
            { class: "act-item" },
            h(
              "div",
              { class: "act-meta" },
              h(
                "time",
                { datetime: time.toISOString(), title: relativeTime(e.at, now) },
                time.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" }),
              ),
              h("span", { class: "tag" }, e.source),
              h("span", { class: "muted" }, `${ms(e.totalMs)} end to end`),
            ),
            e.calls.length
              ? h(
                  "ul",
                  { class: "act-calls" },
                  ...e.calls.map((c) =>
                    h(
                      "li",
                      { class: `pill ${c.ok ? "ok" : "error"}${WRITES.has(c.tool) ? " write" : ""}`, title: c.tool },
                      h("span", { class: "pill-mark", "aria-hidden": "true" }, c.ok ? "✓" : "✕"),
                      h("span", { class: "sr-only" }, c.ok ? "Succeeded: " : "Failed: "),
                      label(c.tool),
                      h("b", null, ms(c.ms)),
                    ),
                  ),
                )
              : h("p", { class: "muted" }, "No tool calls: answered from the conversation."),
          );
        }),
      ),
    );
  }

  function render(): void {
    clearButton.disabled = entries.length === 0;
    renderTiles();
    renderByTool();
    renderTimeline();
  }

  subscribers.add(() => {
    if (!root.hidden) render();
  });
  render();
  return {
    refresh: render,
    setFilter(next) {
      filter = next;
      render();
    },
    describe() {
      if (!entries.length) return "Nothing has run yet this session. Ask a question and every tool call lands here.";
      const s = callStats(entries.flatMap((e) => e.calls), WRITES);
      const last = entries[0].calls.at(-1);
      const parts = [
        `${s.count} tool ${s.count === 1 ? "call" : "calls"} this session across ${entries.length} ${entries.length === 1 ? "turn" : "turns"}.`,
        s.count ? `Median ${Math.round(s.p50)} milliseconds, slowest ${Math.round(s.max)}.` : "",
        s.errors ? `${s.errors} ${s.errors === 1 ? "call" : "calls"} failed.` : "None failed.",
        s.writes ? `${s.writes} changed something.` : "",
        last ? `The latest was ${label(last.tool)}.` : "",
      ];
      return parts.filter(Boolean).join(" ");
    },
  };
}
