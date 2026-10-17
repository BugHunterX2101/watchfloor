// The Pipeline page: the risk briefing as a board you can read at a glance and act from. It calls the
// same get_risk_briefing tool the voice path does (with the signed-in user's token, through the
// harness API), shows each deal as a health ring, and opens a deal's explanation (explain_deal_risk)
// in a sheet with the 14-day health trend and the four RIGS factors. "Ask Watchfloor" hands the
// question to the conversation, so anything that changes data still goes through the preview and
// "yes" flow on Watch.
//
// Loading this board counts as a briefing, exactly as asking by voice does: the server marks the
// deals as seen. The page says so rather than hiding it.

import { callTool, SignedOut, type Call } from "./api";
import { h, icon, toast } from "./dom";
import { ICONS } from "./icons";
import {
  arrangeDeals,
  BAND_LABEL,
  BAND_ORDER,
  boardTotals,
  compactUsd,
  dealTitle,
  dragLevel,
  FACTOR_NAME,
  FACTORS,
  promptSafe,
  relativeTime,
  ringDash,
  sinceLabel,
  toBriefing,
  toExplain,
  type Band,
  type Briefing,
  type BriefingDeal,
  type Explain,
  type SortKey,
} from "./logic";
import { pinDeal, pinnedDeals, pins as loadPins, settings } from "./store";
import { resolveDeal, type DealRef } from "./voice/grammar";

export interface PipelineHost {
  /** Hand a question to the conversation on the Watch page. */
  ask(text: string): void;
  report(call: Call, source: string): void;
  signedOut(): void;
  /** A briefing was loaded: the tab badge and the 3D floor follow it. */
  briefing(b: Briefing): void;
}

const SEGMENTS = [
  ["default", "Default"],
  ["all", "All"],
  ["smb", "SMB"],
  ["mid", "Mid-market"],
  ["enterprise", "Enterprise"],
] as const;
type Segment = (typeof SEGMENTS)[number][0];

const SORTS: [SortKey, string][] = [
  ["urgency", "Most urgent"],
  ["health", "Lowest health"],
  ["amount", "Largest amount"],
  ["name", "Name"],
];

const STALE_MS = 5 * 60_000;
const SHAPE: Record<Band, string> = {
  at_risk: '<path d="M6 1.2 11 10.5H1z"/>',
  watch: '<rect x="1.8" y="1.8" width="8.4" height="8.4" rx="1"/>',
  healthy: '<circle cx="6" cy="6" r="4.6"/>',
};

/** A band is a word, a colour and a shape together, so it reads in greyscale and under colour filters. */
export function bandChip(band: Band): HTMLElement {
  const chip = h("span", { class: `band ${band}` });
  const mark = h("span", { class: "band-mark", "aria-hidden": "true" });
  mark.innerHTML = `<svg viewBox="0 0 12 12">${SHAPE[band]}</svg>`;
  chip.append(mark, BAND_LABEL[band]);
  return chip;
}

/** The health ring. Every value in the markup is a number or a fixed word, never API text. */
function ringSvg(health: number, band: Band, size: number): string {
  const r = size / 2 - 6;
  const c = size / 2;
  const { arc, gap } = ringDash(health, r);
  return (
    `<svg class="ring ${band}" viewBox="0 0 ${size} ${size}" role="img" aria-label="Health ${health} out of 100, ${BAND_LABEL[band]}">` +
    `<circle class="ring-track" cx="${c}" cy="${c}" r="${r}"/>` +
    `<circle class="ring-arc" cx="${c}" cy="${c}" r="${r}" stroke-dasharray="${arc.toFixed(2)} ${gap.toFixed(2)}" style="--arc:${arc.toFixed(2)}" transform="rotate(-90 ${c} ${c})"/>` +
    `<text class="ring-n" x="${c}" y="${c}" text-anchor="middle" dominant-baseline="central">${health}</text></svg>`
  );
}

/** Health over the last days as a line with the two band lines (60 and 35) for scale. Numbers only. */
function sparkline(values: number[]): HTMLElement {
  const W = 360;
  const H = 110;
  const pad = 10;
  const y = (v: number): number => pad + (1 - Math.min(100, Math.max(0, v)) / 100) * (H - pad * 2);
  const x = (i: number): number => (values.length === 1 ? W / 2 : pad + (i / (values.length - 1)) * (W - pad * 2));
  const pts = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
  const first = values[0];
  const last = values[values.length - 1];
  const delta = last - first;
  const trend = delta === 0 ? "unchanged" : delta > 0 ? `up ${delta}` : `down ${-delta}`;
  const guides = [60, 35]
    .map(
      (line) =>
        `<line class="spark-guide" x1="${pad}" x2="${W - pad}" y1="${y(line).toFixed(1)}" y2="${y(line).toFixed(1)}"/>` +
        `<text class="spark-tick" x="${W - pad}" y="${(y(line) - 3).toFixed(1)}" text-anchor="end">${line}</text>`,
    )
    .join("");
  const path =
    values.length > 1
      ? `<polygon class="spark-area" points="${pad},${H - pad} ${pts.join(" ")} ${W - pad},${H - pad}"/><polyline class="spark-line" points="${pts.join(" ")}"/>`
      : "";
  const holder = h("div");
  holder.innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Health over the last ${values.length} days, from ${first} to ${last}: ${trend}">` +
    `${guides}${path}<circle class="spark-dot" cx="${x(values.length - 1).toFixed(1)}" cy="${y(last).toFixed(1)}" r="4"/></svg>`;
  return h(
    "figure",
    { class: "spark" },
    holder,
    h(
      "figcaption",
      { class: "spark-cap" },
      `${values.length} ${values.length === 1 ? "day" : "days"} · ${first} → ${last} · `,
      h("b", null, delta === 0 ? "unchanged" : `${delta > 0 ? "▲" : "▼"} ${trend}`),
    ),
  );
}

/** A card leans toward the pointer: a touch of real depth. Off for touch, reduced motion and "Calm". */
function attachTilt(card: HTMLElement): void {
  const fine = matchMedia("(hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference)");
  card.addEventListener("pointermove", (e) => {
    if (!fine.matches || settings().motion === "calm") return;
    const box = card.getBoundingClientRect();
    const px = (e.clientX - box.left) / box.width - 0.5;
    const py = (e.clientY - box.top) / box.height - 0.5;
    card.style.setProperty("--ry", `${(px * 7).toFixed(2)}deg`);
    card.style.setProperty("--rx", `${(-py * 7).toFixed(2)}deg`);
    card.style.setProperty("--gx", `${((px + 0.5) * 100).toFixed(0)}%`);
    card.style.setProperty("--gy", `${((py + 0.5) * 100).toFixed(0)}%`);
    card.classList.add("tilting");
  });
  card.addEventListener("pointerleave", () => {
    card.classList.remove("tilting");
    card.style.removeProperty("--rx");
    card.style.removeProperty("--ry");
  });
}

/** What the board is showing: the four controls above it. */
export interface PipelineView {
  segment: Segment;
  limit: number;
  band: Band | "all";
  sort: SortKey;
}

/** A deal named by the assistant (an id from the tools) or by the user (a name, as heard). */
export interface DealRefIn {
  deal_id?: string;
  deal_name?: string;
}

/** The page, as voice drives it. Every method answers with a sentence fit to be spoken. */
export interface PipelineHandle {
  setActive(on: boolean): void;
  setSignedIn(on: boolean): void;
  reload(): void;
  deals(): readonly BriefingDeal[];
  view(): PipelineView;
  openDealId(): string | null;
  /** Change what the board shows (reloading when the segment or the count changed). */
  show(v: Partial<PipelineView>): Promise<string>;
  open(ref: DealRefIn): Promise<string>;
  pin(ref: DealRefIn, on: boolean): Promise<string>;
  closeSheet(): void;
  /** The board in words: the briefing's own summary, then what is showing. */
  describe(): string;
  /** A briefing is loaded (loading one if needed); false when signed out or it failed. */
  ensure(): Promise<boolean>;
}

const SORT_SPOKEN: Record<SortKey, string> = {
  urgency: "most urgent first",
  health: "lowest health first",
  amount: "largest first",
  name: "by name",
};

export function mountPipeline(root: HTMLElement, host: PipelineHost): PipelineHandle {
  let briefing: Briefing | null = null;
  let inflight: Promise<void> | null = null;
  let currentDeal: string | null = null;
  let loadedAt = 0;
  let status: "idle" | "loading" | "error" = "idle";
  let errorText = "";
  let signedIn = false;
  let active = false;
  let band: Band | "all" = "all";
  let sort: SortKey = "urgency";
  let segment: Segment = "default";
  let limit = 5;
  let requestId = 0;
  let ticker = 0;

  root.replaceChildren();

  // --- fixed chrome ---
  const refresh = h("button", { type: "button", class: "btn btn-quiet" });
  refresh.append(icon(ICONS.refresh), "Refresh");
  const stamp = h("p", { class: "stamp", role: "status", "aria-live": "polite" });
  const head = h(
    "header",
    { class: "page-head" },
    h(
      "div",
      null,
      h("p", { class: "eyebrow" }, "Risk briefing"),
      h("h1", { class: "page-title", "data-page-title": true }, "Pipeline"),
      h("p", { class: "lede" }, "The deals that need you, ranked by health. Tap one for the reasons."),
    ),
    h("div", { class: "page-actions" }, stamp, refresh),
  );
  const controls = h("div", { class: "toolbar" });
  const summary = h("p", { class: "summary-line" });
  const tiles = h("div", { class: "tiles", role: "group", "aria-label": "Board summary" });
  const rules = h("div", { class: "rules-strip" });
  const grid = h("ul", { class: "deal-grid" });
  const state = h("div", { class: "board-state" });
  const foot = h(
    "p",
    { class: "footnote" },
    "Loading this board counts as a briefing, the same as asking by voice: deals you've seen stop showing as new.",
  );
  // The cards are h3s; this keeps the outline h1 → h2 → h3 for a screen reader's heading list.
  root.append(head, controls, summary, tiles, rules, state, h("h2", { class: "sr-only" }, "Deals"), grid, foot);

  // --- toolbar ---
  function seg<T extends string>(
    label: string,
    options: readonly (readonly [T, string])[],
    value: T,
    pick: (v: T) => void,
  ): HTMLElement {
    const box = h("div", { class: "seg", role: "radiogroup", "aria-label": label });
    options.forEach(([id, text], i) => {
      const b = h(
        "button",
        { type: "button", role: "radio", "aria-checked": String(id === value), tabindex: id === value ? "0" : "-1" },
        text,
      );
      b.addEventListener("click", () => pick(id));
      b.addEventListener("keydown", (e) => {
        const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
        if (!step) return;
        e.preventDefault();
        pick(options[(i + step + options.length) % options.length][0]);
        requestAnimationFrame(() =>
          controls.querySelector<HTMLElement>(`[aria-label="${label}"] [aria-checked="true"]`)?.focus(),
        );
      });
      box.append(b);
    });
    return h("div", { class: "field" }, h("span", { class: "field-label" }, label), box);
  }

  function renderControls(): void {
    const counts = briefing ? boardTotals(briefing.deals).byBand : { at_risk: 0, watch: 0, healthy: 0 };
    const total = briefing?.deals.length ?? 0;
    const bands: [Band | "all", string][] = [
      ["all", `All ${total}`],
      ...BAND_ORDER.map((b): [Band, string] => [b, `${BAND_LABEL[b]} ${counts[b]}`]),
    ];
    const select = h("select", { class: "select" });
    for (const [id, text] of SORTS) select.append(h("option", { value: id, selected: id === sort }, text));
    select.addEventListener("change", () => {
      sort = select.value as SortKey;
      renderBoard();
    });
    controls.replaceChildren(
      seg("Segment", SEGMENTS, segment, (v) => {
        segment = v;
        void load();
      }),
      seg(
        "Deals shown",
        [1, 2, 3, 4, 5].map((n) => [String(n), String(n)] as const),
        String(limit),
        (v) => {
          limit = Number(v);
          void load();
        },
      ),
      seg("Show", bands, band, (v) => {
        band = v;
        renderBoard();
        renderControls();
      }),
      h("label", { class: "field" }, h("span", { class: "field-label" }, "Sort by"), select),
    );
  }

  // --- states ---
  function renderStamp(): void {
    if (status === "loading") stamp.textContent = "Updating…";
    else if (briefing && loadedAt) stamp.textContent = `Updated ${relativeTime(loadedAt, Date.now())}`;
    else stamp.textContent = "";
  }

  function skeletons(): HTMLElement[] {
    return Array.from({ length: Math.min(limit, 3) }, () =>
      h(
        "li",
        { class: "deal-card skeleton", "aria-hidden": "true" },
        h("div", { class: "sk sk-ring" }),
        h("div", { class: "sk sk-line" }),
        h("div", { class: "sk sk-line short" }),
      ),
    );
  }

  function statTile(name: string, value: string, hint: string, tone?: "bad" | "good"): HTMLElement {
    return h(
      "div",
      { class: `tile${tone ? ` ${tone}` : ""}` },
      h("p", { class: "tile-label" }, name),
      h("p", { class: "tile-value" }, value),
      h("p", { class: "tile-hint" }, hint),
    );
  }

  function renderBoard(): void {
    renderStamp();
    refresh.disabled = status === "loading" || !signedIn;
    refresh.classList.toggle("spinning", status === "loading");
    root.setAttribute("aria-busy", String(status === "loading"));
    state.replaceChildren();
    grid.replaceChildren();

    if (!signedIn) {
      summary.textContent = "";
      tiles.replaceChildren();
      rules.replaceChildren();
      state.append(
        h(
          "div",
          { class: "empty" },
          h("p", { class: "empty-title" }, "Sign in to see your pipeline."),
          h("p", { class: "muted" }, "The same OAuth sign-in Alexa+ uses, against the real sign-in server."),
          h("a", { class: "btn btn-primary", href: "/oauth/login" }, "Sign in"),
        ),
      );
      return;
    }

    if (!briefing) {
      summary.textContent = "";
      tiles.replaceChildren();
      rules.replaceChildren();
      if (status === "error") {
        const retry = h("button", { type: "button", class: "btn btn-primary" }, "Try again");
        retry.addEventListener("click", () => void load());
        state.append(
          h("div", { class: "empty" }, h("p", { class: "empty-title" }, "The briefing didn't load."), h("p", { class: "muted" }, errorText), retry),
        );
      } else {
        grid.append(...skeletons());
      }
      return;
    }

    // A failed refresh keeps the last good board and says so above it.
    if (status === "error") {
      const retry = h("button", { type: "button", class: "btn btn-quiet" }, "Try again");
      retry.addEventListener("click", () => void load());
      state.append(h("div", { class: "notice error", role: "alert" }, h("span", null, `Couldn't refresh: ${errorText}`), retry));
    }

    summary.textContent = briefing.summary;
    const totals = boardTotals(briefing.deals);
    const exposure = totals.byBand.at_risk === 0 ? "—" : `${compactUsd(totals.atRiskCents)}${totals.unknownAmounts ? "+" : ""}`;
    tiles.replaceChildren(
      statTile(
        "Need attention",
        String(briefing.attention_count),
        briefing.attention_count ? "At risk or on watch" : "All clear",
        briefing.attention_count ? "bad" : "good",
      ),
      statTile("New", String(briefing.new_count), "Since your last briefing"),
      statTile("At-risk exposure", exposure, totals.unknownAmounts ? "Some amounts not shown" : "Amount in at-risk deals shown"),
      statTile("Rules triggered", String(briefing.triggered_rules.length), "Standing orders that fired"),
    );

    const byId = new Map(briefing.deals.map((d) => [d.deal_id, d]));
    rules.replaceChildren(
      ...briefing.triggered_rules.map((r) => {
        const names = r.deal_ids.map((id) => byId.get(id)?.account).filter((n): n is string => !!n);
        return h("p", { class: "rule-chip" }, icon(ICONS.orders), h("span", null, r.description, names.length ? ` · ${names.join(", ")}` : ""));
      }),
    );

    const shown = arrangeDeals(briefing.deals, { band, query: "", sort, pins: loadPins() });
    if (!shown.length) {
      state.append(
        briefing.deals.length
          ? h(
              "div",
              { class: "empty" },
              h("p", { class: "empty-title" }, `No ${band === "all" ? "" : `${BAND_LABEL[band].toLowerCase()} `}deals in this view.`),
              h("p", { class: "muted" }, "Pick another filter above."),
            )
          : h(
              "div",
              { class: "empty good" },
              h("p", { class: "empty-title" }, briefing.all_clear ? "All clear." : "No deals to show."),
              h("p", { class: "muted" }, briefing.all_clear ? "Nothing in this segment needs you right now." : "Try a different segment."),
            ),
      );
      return;
    }
    grid.append(...shown.map(dealCard));
  }

  // --- a deal card ---
  function dealCard(d: BriefingDeal): HTMLElement {
    const pinned = loadPins().includes(d.deal_id);
    const ringBox = h("div", { class: "ring-box" });
    ringBox.innerHTML = ringSvg(d.health, d.band, 72);

    const open = h("button", { type: "button", class: "deal-open" }, d.account);
    open.addEventListener("click", () => void openDeal(d, open));

    const pin = h("button", {
      type: "button",
      class: "icon-btn pin",
      "aria-pressed": String(pinned),
      "aria-label": pinned ? `Unpin ${d.account}` : `Pin ${d.account}`,
      title: pinned ? "Unpin" : "Pin to the top",
    });
    pin.append(icon(ICONS.pin));
    pin.addEventListener("click", () => {
      const result = pinDeal(d.deal_id, d.account);
      if (result.full) toast("You can pin up to five deals. Unpin one first.", "error");
      renderBoard();
    });

    const ask = h("button", { type: "button", class: "btn btn-ghost" });
    ask.append("Ask Watchfloor", icon(ICONS.arrow));
    ask.addEventListener("click", () => host.ask(`Why is ${promptSafe(d.account)} at risk?`));

    const card = h(
      "li",
      { class: `deal-card ${d.band}${pinned ? " pinned" : ""}` },
      h(
        "div",
        { class: "deal-top" },
        ringBox,
        h(
          "div",
          { class: "deal-id" },
          h("h3", { class: "deal-name" }, open),
          h("p", { class: "deal-sub" }, `${dealTitle(d.account, d.name)}${d.stage ? ` · ${d.stage}` : ""}`),
          h("div", { class: "deal-tags" }, bandChip(d.band), d.new_since_last_briefing ? h("span", { class: "tag new" }, "New") : null),
        ),
        pin,
      ),
      d.top_reason ? h("p", { class: "deal-reason" }, d.top_reason) : null,
      h(
        "dl",
        { class: "deal-meta" },
        h("div", null, h("dt", null, "Amount"), h("dd", null, d.amount_display ?? "—")),
        h("div", null, h("dt", null, "Owner"), h("dd", null, d.owner || "—")),
      ),
      h("div", { class: "deal-actions" }, ask),
    );
    attachTilt(card);
    return card;
  }

  // --- the deal sheet ---
  let sheet: HTMLDialogElement | null = null;
  let sheetToken = 0;

  function ensureSheet(): HTMLDialogElement {
    if (sheet) return sheet;
    const dialog = h("dialog", { class: "sheet wide", "aria-labelledby": "sheet-title" });
    dialog.addEventListener("click", (e) => {
      if (e.target === dialog) dialog.close(); // a click on the backdrop
    });
    dialog.addEventListener("close", () => {
      sheetToken += 1; // a response that arrives after closing is dropped
      currentDeal = null;
    });
    document.body.append(dialog);
    sheet = dialog;
    return dialog;
  }

  async function openDeal(d: BriefingDeal, opener: HTMLElement | null): Promise<void> {
    const dialog = ensureSheet();
    const token = ++sheetToken;
    currentDeal = d.deal_id;
    const close = h("button", { type: "button", class: "icon-btn close", "aria-label": "Close" }, "✕");
    close.addEventListener("click", () => dialog.close());
    const title = h("h2", { class: "sheet-title", id: "sheet-title" }, d.account);
    const body = h("div", { class: "sheet-main", "aria-live": "polite" });
    dialog.replaceChildren(h("div", { class: "sheet-bar" }, title, close), body);
    if (!dialog.open) dialog.showModal();
    close.focus();
    dialog.addEventListener("close", () => opener?.isConnected && opener.focus(), { once: true });

    const run = async (): Promise<void> => {
      body.replaceChildren(h("div", { class: "sk sk-line" }), h("div", { class: "sk sk-line short" }), h("div", { class: "sk sk-block" }));
      try {
        const r = await callTool("explain_deal_risk", { deal_id: d.deal_id });
        if (token !== sheetToken) return;
        host.report(r.call, "Pipeline");
        const ex = r.isError ? null : toExplain(r.structuredContent);
        if (!ex) throw new Error(r.content[0]?.text || "That explanation came back in a shape this page doesn't understand.");
        body.replaceChildren(detail(ex, d, dialog));
      } catch (err) {
        if (token !== sheetToken) return;
        if (err instanceof SignedOut) {
          dialog.close();
          host.signedOut();
          return;
        }
        const retry = h("button", { type: "button", class: "btn btn-primary" }, "Try again");
        retry.addEventListener("click", () => void run());
        body.replaceChildren(
          h("div", { class: "empty" }, h("p", { class: "empty-title" }, "Couldn't load the explanation."), h("p", { class: "muted" }, (err as Error).message), retry),
        );
      }
    };
    await run();
  }

  function detail(ex: Explain, d: BriefingDeal, dialog: HTMLDialogElement): HTMLElement {
    const ringBox = h("div", { class: "ring-box big" });
    ringBox.innerHTML = ringSvg(ex.health, ex.band, 112);

    const factors = h(
      "ul",
      { class: "factors" },
      ...FACTORS.map((f) => {
        const v = ex.components[f];
        const word = dragLevel(v);
        return h(
          "li",
          { class: "factor" },
          h("span", { class: "factor-name" }, FACTOR_NAME[f]),
          h(
            "span",
            { class: `bar ${word.toLowerCase()}`, role: "img", "aria-label": `${FACTOR_NAME[f]}: ${word.toLowerCase()} pull on health` },
            h("i", { style: `width:${Math.round(Math.min(1, Math.max(0, v)) * 100)}%` }),
          ),
          h("span", { class: "factor-word" }, word),
        );
      }),
    );

    const ask = h("button", { type: "button", class: "btn btn-primary" });
    ask.append("Ask Watchfloor about this", icon(ICONS.arrow));
    ask.addEventListener("click", () => {
      dialog.close();
      host.ask(`Why is ${promptSafe(ex.deal.account)} at risk?`);
    });
    const dig = h("button", { type: "button", class: "btn btn-quiet", title: "Starts a background review of this deal; the result comes back on Watch." }, "Start a deep review");
    dig.addEventListener("click", () => {
      dialog.close();
      host.ask(`Dig into ${promptSafe(ex.deal.account)}`);
    });

    const reasonList = (items: Explain["reasons"]): HTMLElement =>
      h("ol", { class: "reasons" }, ...items.map((r) => h("li", null, h("span", { class: "tag" }, FACTOR_NAME[r.factor]), r.text)));

    return h(
      "div",
      { class: "detail" },
      h(
        "div",
        { class: "detail-top" },
        ringBox,
        h(
          "div",
          null,
          h("div", { class: "deal-tags" }, bandChip(ex.band), ex.band_since ? h("span", { class: "muted" }, sinceLabel(ex.band_since)) : null),
          h("p", { class: "detail-sub" }, `${dealTitle(ex.deal.account, ex.deal.name)}${ex.deal.stage ? ` · ${ex.deal.stage}` : ""}`),
          h(
            "dl",
            { class: "deal-meta" },
            h("div", null, h("dt", null, "Amount"), h("dd", null, ex.deal.amount_display ?? d.amount_display ?? "—")),
            h("div", null, h("dt", null, "Owner"), h("dd", null, ex.deal.owner || d.owner || "—")),
            h("div", null, h("dt", null, "Decision date"), h("dd", null, ex.deal.close_date_display || "—")),
            h("div", null, h("dt", null, "Next step"), h("dd", null, ex.deal.next_step ?? "None set")),
          ),
        ),
      ),
      h("p", { class: "detail-summary" }, ex.summary),
      h("h3", { class: "mini-title" }, "Why"),
      ex.reasons.length ? reasonList(ex.reasons) : h("p", { class: "muted" }, "No risk signals right now."),
      ex.other.length
        ? h("details", { class: "more" }, h("summary", null, `${ex.other.length} other ${ex.other.length === 1 ? "finding" : "findings"}`), reasonList(ex.other))
        : null,
      h("h3", { class: "mini-title" }, "What pulls health down"),
      factors,
      h("h3", { class: "mini-title" }, "Health over time"),
      ex.trend.length ? sparkline(ex.trend) : h("p", { class: "muted" }, "No history yet for this deal."),
      h("p", { class: "footnote" }, "Guide lines at 60 and 35 mark where the Watch and At risk bands begin."),
      h("div", { class: "sheet-actions" }, dig, ask),
    );
  }

  // --- loading ---
  /** One load at a time can be waited for (voice answers after the board has arrived). */
  function load(): Promise<void> {
    const p = fetchBoard();
    inflight = p;
    void p.finally(() => {
      if (inflight === p) inflight = null;
    });
    return p;
  }

  async function fetchBoard(): Promise<void> {
    if (!signedIn) return;
    const id = ++requestId;
    status = "loading";
    renderControls();
    renderBoard();
    try {
      const args: Record<string, unknown> = { limit };
      if (segment !== "default") args.segment = segment;
      const r = await callTool("get_risk_briefing", args);
      if (id !== requestId) return;
      host.report(r.call, "Pipeline");
      const parsed = r.isError ? null : toBriefing(r.structuredContent);
      if (!parsed) throw new Error(r.content[0]?.text || "The briefing came back in a shape this page doesn't understand.");
      briefing = parsed;
      host.briefing(parsed);
      loadedAt = Date.now();
      status = "idle";
      errorText = "";
    } catch (err) {
      if (id !== requestId) return;
      if (err instanceof SignedOut) {
        host.signedOut();
        return;
      }
      status = "error";
      errorText = (err as Error).message;
    }
    renderControls();
    renderBoard();
  }

  refresh.addEventListener("click", () => void load());

  function setActive(on: boolean): void {
    active = on;
    window.clearInterval(ticker);
    if (!on) return;
    ticker = window.setInterval(renderStamp, 20_000);
    if (signedIn && status !== "loading" && (!briefing || Date.now() - loadedAt > STALE_MS)) void load();
    else {
      renderControls();
      renderBoard();
    }
  }

  function setSignedIn(on: boolean): void {
    signedIn = on;
    if (!on) {
      briefing = null;
      status = "idle";
      requestId += 1;
      sheet?.close();
    }
    renderControls();
    renderBoard();
    if (on && active && !briefing) void load();
  }

  // --- voice: the same board, driven by what is said ---

  const toRef = (d: BriefingDeal): DealRef => ({ id: d.deal_id, account: d.account, name: d.name });

  /** The deal a reference means: on the board by id or by name, or a stand-in for an id the tools gave. */
  function find(ref: DealRefIn): { deal: BriefingDeal | null; ask?: string } {
    const deals = briefing?.deals ?? [];
    const byId = ref.deal_id ? deals.find((d) => d.deal_id === ref.deal_id) : undefined;
    if (byId) return { deal: byId };
    if (ref.deal_name) {
      const hit = resolveDeal(ref.deal_name, deals.map(toRef));
      const found = hit.deal ? deals.find((d) => d.deal_id === hit.deal?.id) : undefined;
      if (found) return { deal: found };
      if (hit.ambiguous.length) return { deal: null, ask: `Which one: ${hit.ambiguous.map((a) => a.account).join(" or ")}?` };
    }
    if (ref.deal_id) {
      const label = ref.deal_name ?? "this deal";
      const stand: BriefingDeal = {
        deal_id: ref.deal_id,
        name: label,
        account: label,
        amount_cents: null,
        amount_display: null,
        stage: "",
        owner: "",
        score: 0,
        health: 0,
        band: "watch",
        top_reason: "",
        new_since_last_briefing: false,
      };
      return { deal: stand };
    }
    return { deal: null };
  }

  function shown(): BriefingDeal[] {
    return briefing ? arrangeDeals(briefing.deals, { band, query: "", sort, pins: loadPins() }) : [];
  }

  /** "Showing 2 at-risk deals, most urgent first: Kestrel Biotech, health 38; …" */
  function spokenBoard(): string {
    if (!briefing) return "";
    const list = shown();
    const which = band === "all" ? "" : `${BAND_LABEL[band].toLowerCase()} `;
    if (!list.length) {
      if (briefing.deals.length) return `No ${which}deals in this view.`;
      return briefing.all_clear ? "All clear. Nothing in this segment needs you." : "No deals to show.";
    }
    const first = list
      .slice(0, 3)
      .map((d) => `${d.account}, health ${d.health}`)
      .join("; ");
    return `Showing ${list.length} ${which}${list.length === 1 ? "deal" : "deals"}, ${SORT_SPOKEN[sort]}: ${first}${list.length > 3 ? ", and more" : ""}.`;
  }

  async function ensure(): Promise<boolean> {
    if (!signedIn) return false;
    if (inflight) await inflight;
    else if (!briefing) await load();
    return briefing !== null;
  }

  const lost = (ref: DealRefIn): string => `${ref.deal_name ?? "That deal"} isn't on the board right now.`;

  renderControls();
  renderBoard();
  return {
    setActive,
    setSignedIn,
    reload: () => {
      if (active) void load();
    },
    deals: () => briefing?.deals ?? [],
    view: () => ({ segment, limit, band, sort }),
    openDealId: () => currentDeal,
    async show(v) {
      if (!signedIn) return "Sign in to see your pipeline.";
      let reload = !briefing;
      if (v.segment !== undefined && v.segment !== segment) {
        segment = v.segment;
        reload = true;
      }
      if (v.limit !== undefined && v.limit !== limit) {
        limit = Math.min(5, Math.max(1, Math.round(v.limit)));
        reload = true;
      }
      if (v.band !== undefined) band = v.band;
      if (v.sort !== undefined) sort = v.sort;
      if (reload) await load();
      else {
        renderControls();
        renderBoard();
      }
      if (!briefing) return `I couldn't load the pipeline. ${errorText}`.trim();
      return spokenBoard();
    },
    async open(ref) {
      if (!signedIn) return "Sign in to see your pipeline.";
      if (ref.deal_name && !ref.deal_id && !(await ensure())) return "I couldn't load the pipeline.";
      const { deal, ask } = find(ref);
      if (ask) return ask;
      if (!deal) return lost(ref);
      void openDeal(deal, null);
      return `Opening ${deal.account}.`;
    },
    async pin(ref, on) {
      if (!signedIn) return "Sign in first.";
      if (ref.deal_name && !ref.deal_id && !(await ensure())) return "I couldn't load the pipeline.";
      let { deal, ask } = find(ref);
      if (ask) return ask;
      if (!deal && !on && ref.deal_name) {
        // Unpinning a deal that has since left the board: it is still among the pins.
        const hit = resolveDeal(ref.deal_name, pinnedDeals().map((p) => ({ id: p.id, account: p.label, name: p.label })));
        if (hit.deal) deal = find({ deal_id: hit.deal.id, deal_name: hit.deal.account }).deal;
      }
      if (!deal) return lost(ref);
      const pinned = loadPins().includes(deal.deal_id);
      if (pinned === on) return on ? `${deal.account} is already pinned.` : `${deal.account} isn't pinned.`;
      if (pinDeal(deal.deal_id, deal.account).full) return "You can pin up to five deals. Unpin one first.";
      renderBoard();
      return on ? `Pinned ${deal.account}.` : `Unpinned ${deal.account}.`;
    },
    closeSheet: () => {
      if (sheet?.open) sheet.close();
    },
    describe() {
      if (!signedIn) return "Sign in to see your pipeline.";
      if (!briefing) return status === "error" ? `The pipeline didn't load. ${errorText}` : "The pipeline is still loading.";
      return `${briefing.summary} ${spokenBoard()}`.trim();
    },
    ensure,
  };
}
