// The trace panel: one card per turn, one span per MCP tool call. Bars are durations relative to
// the turn's slowest call (server latency), not a timeline, because the API reports durations only.
// While a turn is in flight a live card counts the elapsed time, so the wait is never a blank.

import type { Call } from "./api";
import { ICONS } from "./icons";

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** A running card at the top of the trace until the turn returns; the elapsed time ticks live. */
export function pendingTrace(list: HTMLOListElement, source = "Turn"): { remove(): void } {
  list.querySelector(".calls-empty")?.setAttribute("hidden", "");
  const turn = el("li", "turn running");
  turn.setAttribute("aria-busy", "true");
  const head = el("div", "turn-head");
  const title = el("span");
  title.append(el("b", undefined, source), " · calling tools");
  const clock = el("span", "turn-clock", "0 ms");
  head.append(title, clock);
  const span = el("div", "span pending");
  const glyph = el("span", "span-glyph");
  glyph.setAttribute("role", "img");
  glyph.setAttribute("aria-label", "running");
  const bar = el("div", "span-bar");
  bar.append(el("i"));
  span.append(glyph, el("span", "span-name", "waiting for the MCP server"), el("span", "span-ms", "…"), bar);
  turn.append(head, span);
  list.prepend(turn);

  const t0 = performance.now();
  const timer = window.setInterval(() => {
    clock.textContent = `${Math.round(performance.now() - t0)} ms`;
  }, 50);
  return {
    remove() {
      window.clearInterval(timer);
      turn.remove();
      const empty = list.querySelector(".calls-empty");
      if (empty && list.children.length === 1) empty.removeAttribute("hidden");
    },
  };
}

export function showTrace(list: HTMLOListElement, calls: Call[], totalMs: number, source = "Turn"): void {
  list.querySelector(".calls-empty")?.remove();
  const turn = el("li", "turn");
  const head = el("div", "turn-head");
  const title = el("span");
  title.append(el("b", undefined, source), ` · ${calls.length} tool call${calls.length === 1 ? "" : "s"}`);
  head.append(title, el("span", "turn-clock", `${Math.round(totalMs)} ms end to end`));
  turn.append(head);

  const slowest = Math.max(1, ...calls.map((c) => c.ms));
  calls.forEach((c, i) => {
    const span = el("div", `span ${c.ok ? "ok" : "error"}`);
    span.style.setProperty("--i", String(i));
    const glyph = el("span", "span-glyph");
    glyph.innerHTML = c.ok ? ICONS.check : ICONS.x;
    glyph.setAttribute("role", "img");
    glyph.setAttribute("aria-label", c.ok ? "succeeded" : "failed");
    const bar = el("div", "span-bar");
    const fill = el("i");
    fill.style.width = `${Math.max(2, (c.ms / slowest) * 100)}%`;
    bar.append(fill);
    span.append(glyph, el("span", "span-name", c.tool), el("span", "span-ms", `${c.ms} ms${c.ok ? "" : " · error"}`), bar);

    // Confirmation tokens are single-use and short-lived, but still not something to put on screen.
    const shown: Record<string, unknown> = { ...c.arguments };
    if ("confirmation_token" in shown) shown.confirmation_token = "•••";
    if (Object.keys(shown).length) {
      const details = el("details");
      details.append(el("summary", undefined, "arguments"), el("pre", undefined, JSON.stringify(shown, null, 2)));
      span.append(details);
    }
    turn.append(span);
  });
  if (!calls.length) turn.append(el("div", "span-none", "No tool calls."));
  list.prepend(turn);
}

export function clearTrace(list: HTMLOListElement): void {
  list.replaceChildren(el("li", "calls-empty", "Tool calls appear here once you ask something."));
}
