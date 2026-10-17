// Navigation: five pages in one document, addressed by the URL hash (#/pipeline) so every page can
// be linked to, reloaded and reached with the browser's Back and Forward. Pages are shown and
// hidden, not rebuilt, so the conversation, the open view and the trace survive a trip to another
// page. On a change the page's heading takes focus (screen readers announce where they landed)
// and the tab title follows.

import { setBaseTitle } from "./orb";

export type Route = "watch" | "pipeline" | "orders" | "activity" | "guide";

export const ROUTES: readonly { id: Route; label: string; title: string }[] = [
  { id: "watch", label: "Watch", title: "Pipeline watch" },
  { id: "pipeline", label: "Pipeline", title: "Pipeline" },
  { id: "orders", label: "Orders", title: "Standing orders" },
  { id: "activity", label: "Activity", title: "Activity" },
  { id: "guide", label: "Guide", title: "Guide" },
];

const IDS = new Set<string>(ROUTES.map((r) => r.id));

/** "#/pipeline" → "pipeline"; "", "#", "#/" → "watch"; anything unknown → null. */
export function parseHash(hash: string): Route | null {
  const path = hash.replace(/^#\/?/, "").split(/[?/]/)[0].toLowerCase();
  if (!path) return "watch";
  return IDS.has(path) ? (path as Route) : null;
}

const hashFor = (route: Route): string => (route === "watch" ? "#/" : `#/${route}`);

type Listener = (route: Route, first: boolean) => void;
const listeners = new Set<Listener>();
let active: Route | null = null;

export const current = (): Route => active ?? "watch";

export function onRoute(fn: Listener): void {
  listeners.add(fn);
}

export function navigate(route: Route): void {
  if (parseHash(location.hash) === route) return;
  location.hash = hashFor(route);
}

/** A badge on a tab (a count of things needing attention); null clears it. */
export function setBadge(route: Route, count: number | null, noun = "need attention"): void {
  const tab = document.querySelector<HTMLAnchorElement>(`.nav a[data-route="${route}"]`);
  const badge = tab?.querySelector<HTMLElement>(".nav-badge");
  if (!tab || !badge) return;
  const label = ROUTES.find((r) => r.id === route)?.label ?? route;
  if (count && count > 0) {
    badge.hidden = false;
    badge.textContent = count > 9 ? "9+" : String(count);
    tab.setAttribute("aria-label", `${label}, ${count} ${noun}`);
  } else {
    badge.hidden = true;
    badge.textContent = "";
    tab.removeAttribute("aria-label");
  }
}

function show(route: Route, first: boolean): void {
  const previous = active;
  active = route;
  for (const page of document.querySelectorAll<HTMLElement>("[data-page]")) {
    const on = page.dataset.page === route;
    page.hidden = !on;
    if (on && !first && previous !== route) {
      // Restart the entrance animation without relying on a class timer.
      page.classList.remove("enter");
      void page.offsetWidth;
      page.classList.add("enter");
    }
  }
  for (const tab of document.querySelectorAll<HTMLAnchorElement>(".nav a[data-route]")) {
    if (tab.dataset.route === route) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  }
  const meta = ROUTES.find((r) => r.id === route);
  setBaseTitle(route === "watch" ? "Watchfloor · Simulated Alexa+" : `${meta?.title ?? route} · Watchfloor`);

  if (!first) {
    window.scrollTo({ top: 0 });
    // The page's own heading: where a screen reader should start reading.
    const heading = document.querySelector<HTMLElement>(`[data-page="${route}"] [data-page-title]`);
    heading?.setAttribute("tabindex", "-1");
    heading?.focus({ preventScroll: true });
  }
  for (const fn of listeners) fn(route, first);
}

export function initRouter(): void {
  const resolve = (first: boolean): void => {
    const route = parseHash(location.hash);
    if (route === null) {
      // An unknown address lands on Watch, and the address bar says so.
      history.replaceState(null, "", `${location.pathname}${location.search}#/`);
      show("watch", first);
      return;
    }
    if (route !== active) show(route, first);
  };
  window.addEventListener("hashchange", () => resolve(false));
  resolve(true);
}
