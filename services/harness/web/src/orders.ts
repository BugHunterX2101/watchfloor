// The Orders page: the user's standing orders (rules that watch the pipeline between briefings),
// read with list_standing_orders and removed with remove_standing_order after an explicit
// confirmation. Creating or changing an order is a conversation (the model needs to understand
// "over fifty thousand, quiet for a week"), so those examples hand off to Watch.

import { callTool, SignedOut, type Call } from "./api";
import { busy, confirmDialog, h, icon, toast } from "./dom";
import { ICONS } from "./icons";
import { orderExamples, relativeTime, type BriefingDeal } from "./logic";

export interface OrdersHost {
  ask(text: string): void;
  report(call: Call, source: string): void;
  signedOut(): void;
  /** The deals in the latest briefing this visit, so the examples fit this board (empty before one). */
  deals(): readonly BriefingDeal[];
}

export interface Rule {
  rule_id: string;
  description: string;
  created_at: string;
  last_triggered_at: string | null;
}

/** A list_standing_orders result, checked; null if it isn't one. */
export function toRules(data: unknown): Rule[] | null {
  if (!data || typeof data !== "object" || !Array.isArray((data as { rules?: unknown }).rules)) return null;
  const out: Rule[] = [];
  for (const raw of (data as { rules: unknown[] }).rules) {
    if (!raw || typeof raw !== "object") return null;
    const r = raw as Record<string, unknown>;
    if (typeof r.rule_id !== "string" || typeof r.description !== "string") return null;
    out.push({
      rule_id: r.rule_id,
      description: r.description,
      created_at: typeof r.created_at === "string" ? r.created_at : "",
      last_triggered_at: typeof r.last_triggered_at === "string" ? r.last_triggered_at : null,
    });
  }
  return out;
}

const PREFERENCES = [
  "Show five deals in my briefing.",
  "Leave deal amounts out of my briefing.",
  "Focus my briefing on enterprise deals.",
];

const when = (iso: string): number => {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? 0 : t;
};

/** The page, as voice drives it. */
export interface OrdersHandle {
  setActive(on: boolean): void;
  setSignedIn(on: boolean): void;
  setEngine(engine: string | null): void;
  rules(): readonly Rule[];
  /** The list is loaded (loading it if needed); false when signed out or it failed. */
  ensure(): Promise<boolean>;
  reload(): void;
  /** Ask, with the same confirmation as the Remove button, and remove one standing order. */
  removeRule(rule: Rule): Promise<"removed" | "cancelled" | "failed">;
  /** The orders in words. */
  describe(): string;
}

export function mountOrders(root: HTMLElement, host: OrdersHost): OrdersHandle {
  let rules: Rule[] | null = null;
  let inflight: Promise<void> | null = null;
  let status: "idle" | "loading" | "error" = "idle";
  let errorText = "";
  let signedIn = false;
  let engine: string | null = null;
  let requestId = 0;

  root.replaceChildren();
  const refresh = h("button", { type: "button", class: "btn btn-quiet" });
  refresh.append(icon(ICONS.refresh), "Refresh");
  const head = h(
    "header",
    { class: "page-head" },
    h(
      "div",
      null,
      h("p", { class: "eyebrow" }, "Between briefings"),
      h("h1", { class: "page-title", "data-page-title": true }, "Standing orders"),
      h("p", { class: "lede" }, "Rules that watch your pipeline for you. When one fires, it shows up in your next briefing."),
    ),
    h("div", { class: "page-actions" }, refresh),
  );
  const list = h("div", { class: "orders-body", "aria-live": "polite" });
  const voice = h("section", { class: "panel", "aria-labelledby": "orders-voice-title" });
  root.append(head, list, voice);

  function chips(texts: string[], disabled: boolean): HTMLElement {
    return h(
      "div",
      { class: "chip-row" },
      ...texts.map((t) => {
        const b = h("button", { type: "button", class: "chip", disabled }, t);
        b.addEventListener("click", () => host.ask(t));
        return b;
      }),
    );
  }

  function renderVoice(): void {
    const offline = engine === "offline";
    voice.replaceChildren(
      h("h2", { class: "panel-title", id: "orders-voice-title" }, "Create or change by voice"),
      h(
        "p",
        { class: "muted" },
        offline
          ? "Creating orders needs the model engine. This run uses the offline keyword router, which can list and read them but not write new ones."
          : "Say it the way you'd tell a colleague. Watchfloor reads the rule back and asks before it saves anything.",
      ),
      h("h3", { class: "mini-title" }, "New orders"),
      // Sized to the latest briefing: its typical amount, its top deal's stage, just above its lowest health.
      ...(host.deals().length
        ? [h("p", { class: "help" }, "These fit your latest briefing: its typical deal size, the stage your top deal is in, and its lowest health.")]
        : []),
      chips(orderExamples(host.deals()), offline || !signedIn),
      h("h3", { class: "mini-title" }, "Briefing preferences"),
      chips(PREFERENCES, offline || !signedIn),
    );
  }

  /** The Remove button's flow, and the one voice uses: a confirmation first, then the tool, then the list again. */
  async function removeRule(rule: Rule, button: HTMLButtonElement | null): Promise<"removed" | "cancelled" | "failed"> {
    const yes = await confirmDialog({
      title: "Remove this standing order?",
      body: rule.description,
      confirm: "Remove",
      danger: true,
    });
    if (!yes) return "cancelled";
    const work = async (): Promise<void> => {
      const r = await callTool("remove_standing_order", { rule_id: rule.rule_id });
      host.report(r.call, "Orders");
      const removed = (r.structuredContent as { removed?: unknown } | null)?.removed;
      if (r.isError || removed === false) throw new Error(r.content[0]?.text || "That order was already gone.");
    };
    let outcome: "removed" | "failed" = "removed";
    try {
      if (button) await busy(button, work);
      else await work();
      toast("Standing order removed.");
    } catch (err) {
      if (err instanceof SignedOut) {
        host.signedOut();
        return "failed";
      }
      toast(`Couldn't remove it: ${(err as Error).message}`, "error");
      outcome = "failed";
    }
    void load();
    return outcome;
  }

  function ruleCard(rule: Rule): HTMLElement {
    const remove = h("button", { type: "button", class: "btn btn-quiet danger-text" });
    remove.append(icon(ICONS.trash), "Remove");
    remove.setAttribute("aria-label", `Remove: ${rule.description}`);
    remove.addEventListener("click", () => void removeRule(rule, remove));

    const created = when(rule.created_at);
    const fired = rule.last_triggered_at ? when(rule.last_triggered_at) : 0;
    return h(
      "li",
      { class: "rule-card" },
      h(
        "div",
        null,
        h("p", { class: "rule-desc" }, rule.description),
        h(
          "p",
          { class: "muted" },
          created ? `Created ${relativeTime(created, Date.now())}` : "Created recently",
          " · ",
          fired ? `Last fired ${relativeTime(fired, Date.now())}` : "Hasn't fired yet",
        ),
      ),
      remove,
    );
  }

  function render(): void {
    refresh.disabled = status === "loading" || !signedIn;
    refresh.classList.toggle("spinning", status === "loading");
    list.replaceChildren();
    renderVoice();
    if (!signedIn) {
      list.append(
        h(
          "div",
          { class: "empty" },
          h("p", { class: "empty-title" }, "Sign in to see your standing orders."),
          h("a", { class: "btn btn-primary", href: "/oauth/login" }, "Sign in"),
        ),
      );
      return;
    }
    if (!rules) {
      if (status === "error") {
        const retry = h("button", { type: "button", class: "btn btn-primary" }, "Try again");
        retry.addEventListener("click", () => void load());
        list.append(
          h("div", { class: "empty" }, h("p", { class: "empty-title" }, "Couldn't load your orders."), h("p", { class: "muted" }, errorText), retry),
        );
      } else {
        list.append(
          h(
            "ul",
            { class: "rule-list", "aria-hidden": "true" },
            h("li", { class: "rule-card skeleton" }, h("div", { class: "sk sk-line" })),
            h("li", { class: "rule-card skeleton" }, h("div", { class: "sk sk-line short" })),
          ),
        );
      }
      return;
    }
    if (status === "error") list.append(h("div", { class: "notice error", role: "alert" }, `Couldn't refresh: ${errorText}`));
    list.append(
      rules.length
        ? h("ul", { class: "rule-list" }, ...rules.map(ruleCard))
        : h(
            "div",
            { class: "empty" },
            h("p", { class: "empty-title" }, "No standing orders yet."),
            h("p", { class: "muted" }, "Create one by voice below; it will appear here."),
          ),
    );
  }

  /** One load at a time can be waited for (voice answers after the list has arrived). */
  function load(): Promise<void> {
    const p = fetchRules();
    inflight = p;
    void p.finally(() => {
      if (inflight === p) inflight = null;
    });
    return p;
  }

  async function fetchRules(): Promise<void> {
    if (!signedIn) return;
    const id = ++requestId;
    status = "loading";
    render();
    try {
      const r = await callTool("list_standing_orders", {});
      if (id !== requestId) return;
      host.report(r.call, "Orders");
      const parsed = r.isError ? null : toRules(r.structuredContent);
      if (!parsed) throw new Error(r.content[0]?.text || "The list came back in a shape this page doesn't understand.");
      rules = parsed;
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
    render();
  }

  refresh.addEventListener("click", () => void load());
  render();

  return {
    setActive(on) {
      if (on && signedIn && status !== "loading") void load();
    },
    setSignedIn(on) {
      signedIn = on;
      if (!on) {
        rules = null;
        status = "idle";
        requestId += 1;
      }
      render();
    },
    setEngine(next) {
      engine = next;
      renderVoice();
    },
    rules: () => rules ?? [],
    async ensure() {
      if (!signedIn) return false;
      if (inflight) await inflight;
      else if (!rules) await load();
      return rules !== null;
    },
    reload: () => void load(),
    removeRule: (rule) => removeRule(rule, null),
    describe() {
      if (!signedIn) return "Sign in to see your standing orders.";
      if (!rules) return status === "error" ? `Your orders didn't load. ${errorText}` : "Your orders are still loading.";
      if (!rules.length) return "You have no standing orders. You can create one by asking, for example: flag deals over fifty thousand dollars with no buyer reply for seven days.";
      const words = ["One", "Two", "Three"];
      const list = rules
        .slice(0, 3)
        .map((r, i) => `${words[i]}: ${r.description}`)
        .join(" ");
      return `You have ${rules.length} standing ${rules.length === 1 ? "order" : "orders"}. ${list}${rules.length > 3 ? " And more." : ""}`;
    },
  };
}
