// Small DOM helpers shared by the pages. Text always goes in as text (never as HTML), so a deal name
// or a rule description from the API can't inject markup; only the static icon strings in icons.ts
// are ever set as HTML.

export type Child = Node | string | null | undefined | false;
type Attr = string | number | boolean | undefined;

export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs?: Record<string, Attr> | null,
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs ?? {})) {
    if (value === undefined || value === false) continue;
    if (key === "class") node.className = String(value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children) if (child) node.append(child);
  return node;
}

/** A static icon (from ICONS) in a span; `label` makes it a named image, otherwise it is decoration. */
export function icon(svg: string, className = "", label?: string): HTMLSpanElement {
  const span = h("span", { class: `ico ${className}`.trim() });
  span.innerHTML = svg;
  if (label) {
    span.setAttribute("role", "img");
    span.setAttribute("aria-label", label);
  } else {
    span.setAttribute("aria-hidden", "true");
  }
  return span;
}

let toasts: HTMLElement | null = null;

/** A short message that doesn't take focus (a polite live region), gone after a few seconds. */
export function toast(message: string, kind: "info" | "error" = "info"): void {
  if (!toasts) {
    toasts = h("div", { class: "toasts", role: "status", "aria-live": "polite" });
    document.body.append(toasts);
  }
  const item = h("p", { class: `toast ${kind}` }, message);
  toasts.append(item);
  window.setTimeout(
    () => {
      item.classList.add("leaving");
      window.setTimeout(() => item.remove(), 240);
    },
    kind === "error" ? 6000 : 3600,
  );
}

export interface ConfirmOptions {
  title: string;
  body: string;
  confirm: string;
  danger?: boolean;
}

/** A modal yes/no. Cancel has focus first (Enter never confirms by accident); Escape cancels. */
export function confirmDialog(opts: ConfirmOptions): Promise<boolean> {
  return new Promise((resolve) => {
    const cancel = h("button", { type: "submit", value: "cancel", class: "btn btn-quiet" }, "Cancel");
    const ok = h(
      "button",
      { type: "submit", value: "confirm", class: `btn ${opts.danger ? "btn-danger" : "btn-primary"}` },
      opts.confirm,
    );
    const form = h(
      "form",
      { method: "dialog", class: "sheet-form" },
      h("h2", { class: "sheet-title" }, opts.title),
      h("p", { class: "sheet-body" }, opts.body),
      h("div", { class: "sheet-actions" }, cancel, ok),
    );
    const dialog = h("dialog", { class: "sheet small", "aria-label": opts.title }, form);
    dialog.addEventListener("close", () => {
      resolve(dialog.returnValue === "confirm");
      dialog.remove();
    });
    document.body.append(dialog);
    dialog.showModal();
    cancel.focus();
  });
}

/** Wraps work on a button: disabled and aria-busy until it settles. */
export async function busy<T>(button: HTMLButtonElement, work: () => Promise<T>): Promise<T> {
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    return await work();
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}
