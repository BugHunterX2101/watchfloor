// The things voice can do to the page. The words are the contract with the server's `control_app`
// tool (harness_api/control.py lists the same names, and a test keeps the two equal): the model asks
// for one in a turn's `actions`, the spoken-command grammar produces the same objects directly, and
// everything passes through parseAction before it runs, so what arrives from the network is checked
// here again and a malformed or unknown request is dropped, never half-run.
//
// Kept free of the DOM so `npm test` can run it directly.

import { parseLesson } from "./lessons.ts";

export const ACTION_NAMES = [
  "navigate",
  "open_settings",
  "close_dialog",
  "set_theme",
  "set_device",
  "set_read_aloud",
  "set_speech_rate",
  "set_motion",
  "set_orb",
  "set_floor",
  "set_name",
  "set_style",
  "set_alerts",
  "pipeline_view",
  "open_deal",
  "pin_deal",
  "activity_view",
  "clear_activity",
  "change_voice",
  "wake_word",
  "refresh",
  "read_page",
  "repeat",
  "stop_speaking",
  "hands_free",
  "scroll",
  "clear_conversation",
  "sign_out",
  "forget_data",
  "remember",
] as const;

export type ActionName = (typeof ACTION_NAMES)[number];

export type PageName = "watch" | "pipeline" | "orders" | "activity" | "guide";
export type ThemeName = "auto" | "light" | "dark";
export type DeviceName = "fit" | "compact" | "standard" | "large";
export type SegmentName = "default" | "all" | "smb" | "mid" | "enterprise";
export type BandName = "all" | "at_risk" | "watch" | "healthy";
export type SortName = "urgency" | "health" | "amount" | "name";
export type FilterName = "all" | "writes" | "errors";
export type ScrollName = "up" | "down" | "top" | "bottom";
export type StyleName = "brief" | "normal" | "detailed";

export const PAGES: readonly PageName[] = ["watch", "pipeline", "orders", "activity", "guide"];
export const PAGE_LABEL: Record<PageName, string> = {
  watch: "Watch",
  pipeline: "Pipeline",
  orders: "Standing orders",
  activity: "Activity",
  guide: "the guide",
};

/** These end a session, or erase something: the page asks the user, out loud, before they run. */
export const NEEDS_YES: ReadonlySet<ActionName> = new Set<ActionName>(["sign_out", "forget_data", "clear_activity"]);

export type AppAction =
  | { type: "navigate"; page: PageName }
  | { type: "open_settings" }
  | { type: "close_dialog" }
  | { type: "set_theme"; theme: ThemeName }
  | { type: "set_device"; device: DeviceName }
  | { type: "set_read_aloud"; on: boolean }
  | { type: "set_speech_rate"; rate?: number; step?: "faster" | "slower" }
  | { type: "set_motion"; motion: "auto" | "calm" }
  | { type: "set_orb"; on: boolean }
  | { type: "set_floor"; on: boolean }
  | { type: "set_name"; name: string }
  | { type: "set_style"; style: StyleName }
  | { type: "set_alerts"; on: boolean }
  | { type: "pipeline_view"; segment?: SegmentName; limit?: number; band?: BandName; sort?: SortName }
  | { type: "open_deal"; deal_id?: string; deal_name?: string }
  | { type: "pin_deal"; deal_id?: string; deal_name?: string; on: boolean }
  | { type: "activity_view"; filter: FilterName }
  | { type: "clear_activity" }
  | { type: "change_voice"; step: "next" | "previous" }
  | { type: "wake_word"; on: boolean }
  | { type: "refresh" }
  | { type: "read_page" }
  | { type: "repeat" }
  | { type: "stop_speaking" }
  | { type: "hands_free"; on: boolean }
  | { type: "scroll"; direction: ScrollName }
  | { type: "clear_conversation" }
  | { type: "sign_out" }
  | { type: "forget_data" }
  | { type: "remember"; heard: string; meant: string };

/** The most page changes one turn may make (the server stops at the same number). */
export const MAX_ACTIONS = 4;

const isRecord = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const oneOf = <T extends string>(v: unknown, allowed: readonly T[]): T | undefined =>
  typeof v === "string" && (allowed as readonly string[]).includes(v) ? (v as T) : undefined;
const flag = (v: unknown): boolean | undefined => (typeof v === "boolean" ? v : undefined);
const clean = (v: unknown, max: number): string =>
  typeof v === "string" ? v.replace(/\p{Cc}/gu, " ").replace(/\s+/g, " ").trim().slice(0, max) : "";
const DEAL_ID = /^[A-Za-z0-9_-]{1,64}$/;

const THEMES: readonly ThemeName[] = ["auto", "light", "dark"];
const DEVICES: readonly DeviceName[] = ["fit", "compact", "standard", "large"];
const SEGMENTS: readonly SegmentName[] = ["default", "all", "smb", "mid", "enterprise"];
const BANDS: readonly BandName[] = ["all", "at_risk", "watch", "healthy"];
const SORTS: readonly SortName[] = ["urgency", "health", "amount", "name"];
const FILTERS: readonly FilterName[] = ["all", "writes", "errors"];
const SCROLLS: readonly ScrollName[] = ["up", "down", "top", "bottom"];
export const STYLES: readonly StyleName[] = ["brief", "normal", "detailed"];
const NAMES: ReadonlySet<string> = new Set(ACTION_NAMES);

/** One action, checked and rebuilt from only the fields it is allowed to have; null if it isn't one. */
export function parseAction(raw: unknown): AppAction | null {
  if (!isRecord(raw) || typeof raw.type !== "string" || !NAMES.has(raw.type)) return null;
  switch (raw.type as ActionName) {
    case "navigate": {
      const page = oneOf(raw.page, PAGES);
      return page ? { type: "navigate", page } : null;
    }
    case "set_theme": {
      const theme = oneOf(raw.theme, THEMES);
      return theme ? { type: "set_theme", theme } : null;
    }
    case "set_device": {
      const device = oneOf(raw.device, DEVICES);
      return device ? { type: "set_device", device } : null;
    }
    case "set_motion": {
      const motion = oneOf(raw.motion, ["auto", "calm"] as const);
      return motion ? { type: "set_motion", motion } : null;
    }
    case "set_read_aloud":
    case "set_orb":
    case "set_floor":
    case "set_alerts":
    case "hands_free":
    case "wake_word": {
      const on = flag(raw.on);
      return on === undefined ? null : ({ type: raw.type, on } as AppAction);
    }
    case "set_speech_rate": {
      const rate = raw.rate;
      if (typeof rate === "number" && Number.isFinite(rate) && rate >= 0.7 && rate <= 1.4) {
        return { type: "set_speech_rate", rate: Math.round(rate * 100) / 100 };
      }
      const step = oneOf(raw.step, ["faster", "slower"] as const);
      return step ? { type: "set_speech_rate", step } : null;
    }
    case "set_name": {
      const name = clean(raw.name, 40);
      return name ? { type: "set_name", name } : null;
    }
    case "set_style": {
      const style = oneOf(raw.style, STYLES);
      return style ? { type: "set_style", style } : null;
    }
    case "pipeline_view": {
      const act: Extract<AppAction, { type: "pipeline_view" }> = { type: "pipeline_view" };
      const segment = oneOf(raw.segment, SEGMENTS);
      const band = oneOf(raw.band, BANDS);
      const sort = oneOf(raw.sort, SORTS);
      if (segment) act.segment = segment;
      if (band) act.band = band;
      if (sort) act.sort = sort;
      if (typeof raw.limit === "number" && Number.isInteger(raw.limit) && raw.limit >= 1 && raw.limit <= 5) {
        act.limit = raw.limit;
      }
      return Object.keys(act).length > 1 ? act : null;
    }
    case "open_deal":
    case "pin_deal": {
      const id = typeof raw.deal_id === "string" && DEAL_ID.test(raw.deal_id) ? raw.deal_id : undefined;
      const name = clean(raw.deal_name, 80) || undefined;
      if (!id && !name) return null;
      const base = { ...(id ? { deal_id: id } : {}), ...(name ? { deal_name: name } : {}) };
      return raw.type === "open_deal"
        ? { type: "open_deal", ...base }
        : { type: "pin_deal", ...base, on: flag(raw.on) !== false };
    }
    case "activity_view": {
      const filter = oneOf(raw.filter, FILTERS);
      return filter ? { type: "activity_view", filter } : null;
    }
    case "change_voice":
      return { type: "change_voice", step: oneOf(raw.step, ["next", "previous"] as const) ?? "next" };
    case "scroll": {
      const direction = oneOf(raw.direction, SCROLLS);
      return direction ? { type: "scroll", direction } : null;
    }
    case "remember": {
      const lesson = parseLesson(raw);
      return lesson ? { type: "remember", ...lesson } : null;
    }
    case "close_dialog":
    case "open_settings":
    case "clear_activity":
    case "refresh":
    case "read_page":
    case "repeat":
    case "stop_speaking":
    case "clear_conversation":
    case "sign_out":
    case "forget_data":
      return { type: raw.type } as AppAction;
  }
}

/** What a turn sent: each action checked, the bad ones dropped, at most MAX_ACTIONS kept. */
export function parseActions(raw: unknown): AppAction[] {
  if (!Array.isArray(raw)) return [];
  const out: AppAction[] = [];
  for (const item of raw) {
    const act = parseAction(item);
    if (act) out.push(act);
    if (out.length >= MAX_ACTIONS) break;
  }
  return out;
}
