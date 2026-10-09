// The trace panel: one card per turn, one span per MCP tool call. Bars are durations relative to
// the turn's slowest call (server latency), not a timeline, because the API reports durations only.

import type { Call } from "./api";

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function showTrace(list: HTMLOListElement, calls: Call[], totalMs: number, source = "Turn"): void {
  list.querySelector(".calls-empty")?.remove();
  const turn = el("li", "turn");
  const head = el("div", "turn-head");
  const title = el("span");
  title.append(el("b", undefined, source), ` · ${calls.length} tool call${calls.length === 1 ? "" : "s"}`);
  head.append(title, el("span", undefined, `${Math.round(totalMs)} ms end to end`));
  turn.append(head);

  const slowest = Math.max(1, ...calls.map((c) => c.ms));
  for (const c of calls) {
    const span = el("div", `span ${c.ok ? "ok" : "error"}`);
    const dot = el("span", "span-dot");
    dot.setAttribute("role", "img");
    dot.setAttribute("aria-label", c.ok ? "succeeded" : "failed");
    const bar = el("div", "span-bar");
    const fill = el("i");
    fill.style.width = `${Math.max(2, (c.ms / slowest) * 100)}%`;
    bar.append(fill);
    span.append(dot, el("span", "span-name", c.tool), el("span", "span-ms", `${c.ms} ms${c.ok ? "" : " · error"}`), bar);

    // Confirmation tokens are single-use and short-lived, but still not something to put on screen.
    const shown: Record<string, unknown> = { ...c.arguments };
    if ("confirmation_token" in shown) shown.confirmation_token = "•••";
    if (Object.keys(shown).length) {
      const details = el("details");
      details.append(el("summary", undefined, "arguments"), el("pre", undefined, JSON.stringify(shown, null, 2)));
      span.append(details);
    }
    turn.append(span);
  }
  if (!calls.length) turn.append(el("div", "span-none", "No tool calls."));
  list.prepend(turn);
}

export function clearTrace(list: HTMLOListElement): void {
  list.replaceChildren(el("li", "calls-empty", "Tool calls appear here once you ask something."));
}
