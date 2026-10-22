// What the page remembers about this viewer, in this browser only: a display name for greetings,
// voice and motion preferences, recent questions and pinned deals. Nothing here is sent anywhere
// and none of it is needed: every read is guarded, so blocked or full storage just means the page
// forgets on reload (private windows, thumbnail capture).

import { pushRecent, togglePin, type PlotDeal } from "./logic";
import { addLesson, dropLesson, parseLesson, type Lesson } from "./voice/lessons";

export type ThemeChoice = "auto" | "light" | "dark";
export type DeviceChoice = "fit" | "compact" | "standard" | "large";
export type Motion = "auto" | "calm";
export type ReplyStyle = "brief" | "normal" | "detailed";

export interface Settings {
  name: string;
  voiceURI: string; // "" = the browser's default voice
  rate: number; // 0.7 – 1.4
  motion: Motion;
  orb3d: boolean; // the voice lamp's animation (the key predates the plot; kept so saved choices carry over)
  scene: boolean; // the plot's turning sweep
  readAloud: boolean;
  wakeWord: boolean; // hands-free acts only on requests that start with "Watchfloor" (or follow a reply)
  style: ReplyStyle; // how long the assistant's replies are (told to the model each turn)
  alerts: boolean; // hands-free speaks up when the board changes (voice/watcher.ts)
  theme: ThemeChoice;
  device: DeviceChoice;
}

export const DEFAULTS: Settings = {
  name: "",
  voiceURI: "",
  rate: 1.05,
  motion: "auto",
  orb3d: true,
  scene: true,
  readAloud: true,
  wakeWord: true,
  style: "normal",
  alerts: true,
  theme: "auto",
  device: "fit",
};

const KEY = "wf.settings";
const LEGACY_THEME = "wf-theme";
const RECENT_KEY = "wf.recent";
const PINS_KEY = "wf.pins";
const PIN_NAMES_KEY = "wf.pinnames";
const PLOT_KEY = "wf.skyline"; // the name it has always had, so a returning viewer keeps their plot
const LESSONS_KEY = "wf.lessons";

function get(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function put(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Storage blocked or full: the choice lasts for this page only.
  }
}

function drop(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    // Nothing to clear.
  }
}

const oneOf = <T extends string>(v: unknown, allowed: readonly T[], fallback: T): T =>
  typeof v === "string" && (allowed as readonly string[]).includes(v) ? (v as T) : fallback;

const cleanName = (v: unknown): string =>
  typeof v === "string" ? v.replace(/\p{Cc}/gu, " ").replace(/\s+/g, " ").trim().slice(0, 40) : "";

/** Whatever is in storage, forced into a valid shape: a hand-edited or older value never breaks the page. */
export function sanitize(raw: unknown): Settings {
  const o = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const rate =
    typeof o.rate === "number" && Number.isFinite(o.rate) ? Math.min(1.4, Math.max(0.7, o.rate)) : DEFAULTS.rate;
  return {
    name: cleanName(o.name),
    voiceURI: typeof o.voiceURI === "string" ? o.voiceURI.slice(0, 200) : "",
    rate,
    motion: oneOf(o.motion, ["auto", "calm"], DEFAULTS.motion),
    orb3d: typeof o.orb3d === "boolean" ? o.orb3d : DEFAULTS.orb3d,
    scene: typeof o.scene === "boolean" ? o.scene : DEFAULTS.scene,
    readAloud: typeof o.readAloud === "boolean" ? o.readAloud : DEFAULTS.readAloud,
    wakeWord: typeof o.wakeWord === "boolean" ? o.wakeWord : DEFAULTS.wakeWord,
    style: oneOf(o.style, ["brief", "normal", "detailed"], DEFAULTS.style),
    alerts: typeof o.alerts === "boolean" ? o.alerts : DEFAULTS.alerts,
    theme: oneOf(o.theme, ["auto", "light", "dark"], DEFAULTS.theme),
    device: oneOf(o.device, ["fit", "compact", "standard", "large"], DEFAULTS.device),
  };
}

function load(): Settings {
  let parsed: unknown = null;
  const saved = get(KEY);
  if (saved) {
    try {
      parsed = JSON.parse(saved);
    } catch {
      parsed = null;
    }
  }
  const s = sanitize(parsed);
  // The theme used to live under its own key.
  if (!saved) s.theme = oneOf(get(LEGACY_THEME), ["auto", "light", "dark"], s.theme);
  return s;
}

type Listener = (s: Readonly<Settings>, changed: ReadonlyArray<keyof Settings>) => void;

let current: Settings = load();
const listeners = new Set<Listener>();

export const settings = (): Readonly<Settings> => current;

export function update(patch: Partial<Settings>): void {
  const next = sanitize({ ...current, ...patch });
  const changed = (Object.keys(next) as (keyof Settings)[]).filter((k) => next[k] !== current[k]);
  current = next;
  put(KEY, JSON.stringify(next));
  if (changed.length) for (const fn of listeners) fn(next, changed);
}

export function onSettings(fn: Listener): void {
  listeners.add(fn);
}

function list(key: string): string[] {
  try {
    const v: unknown = JSON.parse(get(key) ?? "[]");
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string").slice(0, 20) : [];
  } catch {
    return [];
  }
}

export const recents = (): string[] => list(RECENT_KEY);

export function rememberAsk(text: string): void {
  put(RECENT_KEY, JSON.stringify(pushRecent(recents(), text)));
}

export const pins = (): string[] => list(PINS_KEY);

function pinNames(): Record<string, string> {
  try {
    const v: unknown = JSON.parse(get(PIN_NAMES_KEY) ?? "{}");
    if (!v || typeof v !== "object" || Array.isArray(v)) return {};
    return Object.fromEntries(Object.entries(v).filter(([, name]) => typeof name === "string")) as Record<string, string>;
  } catch {
    return {};
  }
}

/** The names of the pinned deals (for "How's Kestrel doing?" chips), in pin order. */
export function pinnedLabels(): string[] {
  const names = pinNames();
  return pins()
    .map((id) => names[id])
    .filter((n): n is string => !!n);
}

/** The pinned deals with their names (a deal pinned earlier may no longer be on the board). */
export function pinnedDeals(): { id: string; label: string }[] {
  const names = pinNames();
  return pins()
    .map((id) => ({ id, label: names[id] ?? "" }))
    .filter((p) => p.label);
}

/** Toggles a pinned deal, remembering its name for the chips; `full` is true when the limit stopped it. */
export function pinDeal(id: string, label = ""): { pins: string[]; full: boolean } {
  const result = togglePin(pins(), id);
  if (result.full) return result;
  put(PINS_KEY, JSON.stringify(result.pins));
  const names = pinNames();
  if (result.pins.includes(id) && label) names[id] = label.slice(0, 80);
  else delete names[id];
  // Names of deals that are no longer pinned don't linger.
  for (const key of Object.keys(names)) if (!result.pins.includes(key)) delete names[key];
  put(PIN_NAMES_KEY, JSON.stringify(names));
  return result;
}

/** The last briefing's deals for the plot: only the id, the band, the health and the stage (no
 *  names or amounts), so the scope shows your pipeline's shape as soon as you return. */
export function savePlot(deals: readonly PlotDeal[]): void {
  put(PLOT_KEY, JSON.stringify(deals.slice(0, 5).map(keep)));
}

const keep = (d: PlotDeal): PlotDeal => ({
  id: d.id,
  band: d.band,
  health: d.health,
  ...(typeof d.stage === "string" && d.stage ? { stage: d.stage.slice(0, 40) } : {}),
});

export function loadPlot(): PlotDeal[] {
  try {
    const v: unknown = JSON.parse(get(PLOT_KEY) ?? "[]");
    if (!Array.isArray(v)) return [];
    return v
      .filter(
        (d): d is PlotDeal =>
          !!d &&
          typeof d === "object" &&
          typeof (d as PlotDeal).id === "string" &&
          ["at_risk", "watch", "healthy"].includes((d as PlotDeal).band) &&
          typeof (d as PlotDeal).health === "number",
      )
      .slice(0, 5)
      .map(keep);
  } catch {
    return [];
  }
}

export function clearPlot(): void {
  drop(PLOT_KEY);
}

/** The corrections the user taught (voice/lessons.ts), newest first; each re-checked as it is read. */
export function lessons(): Lesson[] {
  try {
    const v: unknown = JSON.parse(get(LESSONS_KEY) ?? "[]");
    if (!Array.isArray(v)) return [];
    return v.map(parseLesson).filter((l): l is Lesson => l !== null).slice(0, 8);
  } catch {
    return [];
  }
}

/** Keeps a correction; false when it is not one the page will learn (a number, a yes or no). */
export function learn(raw: { heard: string; meant: string }): boolean {
  const lesson = parseLesson(raw);
  if (!lesson) return false;
  put(LESSONS_KEY, JSON.stringify(addLesson(lessons(), lesson)));
  return true;
}

export function unlearn(heard: string): void {
  put(LESSONS_KEY, JSON.stringify(dropLesson(lessons(), heard)));
}

/** Everything this page keeps in the browser, gone. The current settings fall back to the defaults. */
export function forgetEverything(): void {
  for (const k of [KEY, LEGACY_THEME, RECENT_KEY, PINS_KEY, PIN_NAMES_KEY, PLOT_KEY, LESSONS_KEY]) drop(k);
  const before = current;
  current = { ...DEFAULTS };
  const changed = (Object.keys(current) as (keyof Settings)[]).filter((k) => current[k] !== before[k]);
  if (changed.length) for (const fn of listeners) fn(current, changed);
}
