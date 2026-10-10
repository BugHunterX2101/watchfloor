// Labels and timers. Bands are always spelled out in text, never color alone (DD §15.2).

import type { Band, Factor } from "./types";

export const BAND_LABEL: Record<Band, string> = { at_risk: "AT RISK", watch: "WATCH", healthy: "HEALTHY" };

export const FACTOR_NAME: Record<Factor, string> = {
  R: "Responsiveness",
  I: "Intent",
  G: "Governance",
  S: "Schedule",
};

export const FACTORS: Factor[] = ["R", "I", "G", "S"];

export function clock(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

/** "since Tue" within the last week, else "since Oct 5". */
export function since(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const days = (now.getTime() - d.getTime()) / 86_400_000;
  const label =
    days < 6 ? d.toLocaleDateString([], { weekday: "short" }) : d.toLocaleDateString([], { month: "short", day: "numeric" });
  return `since ${label}`;
}

/** "4:51" until ``expiresAt``; "0:00" once it has passed. */
export function countdown(expiresAt: string, now: number = Date.now()): { text: string; expired: boolean } {
  const left = Math.max(0, Math.floor((new Date(expiresAt).getTime() - now) / 1000));
  const text = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
  return { text, expired: left === 0 };
}

export function channelOf(sideEffects: unknown): string | null {
  if (!Array.isArray(sideEffects)) return null;
  const post = sideEffects.find((s) => typeof s === "string" && s.startsWith("slack_post:"));
  return typeof post === "string" ? post.slice("slack_post:".length) : null;
}

/** Deal names usually repeat the account ("Kestrel Biotech · Platform expansion"): keep the rest. */
export function dealTitle(account: string, name: string): string {
  if (!name.toLowerCase().startsWith(account.toLowerCase())) return name;
  const rest = name.slice(account.length).replace(/^[\s·:–—-]+/, "");
  return rest || name;
}

export function percent(x: number): number {
  return Math.round(Math.min(1, Math.max(0, x)) * 100);
}

/** Health is what people see (ADR-020): 0–100, higher is healthier, 100 minus the RIGS risk score.
 *  The band lines from rigs.yaml (risk enters at 65 and 40) are health 35 and 60. */
export const AT_RISK_HEALTH = 35;
export const WATCH_HEALTH = 60;

/** The deal's health; servers before ADR-020 sent only the risk score. */
export function healthOf(score: number, health?: number | null): number {
  return typeof health === "number" ? health : 100 - score;
}

/** "$1.2M", "$840K", "$9,500": compact for a glance; exact figures stay on the detail screen. */
export function compactMoney(cents: number): string {
  const dollars = cents / 100;
  if (dollars >= 1_000_000) return `$${(dollars / 1_000_000).toFixed(dollars >= 10_000_000 ? 0 : 1).replace(/\.0$/, "")}M`;
  if (dollars >= 10_000) return `$${Math.round(dollars / 1000)}K`;
  return `$${Math.round(dollars).toLocaleString("en-US")}`;
}

/** "just now", "12 min ago", "3 h ago", "2 days ago". */
export function ago(iso: string, now: number = Date.now()): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const min = Math.max(0, Math.round((now - t) / 60_000));
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const h = Math.round(min / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
}

/** Older than this, a briefing or score says so instead of passing for current. */
export const STALE_MINUTES = 60;

export function isStale(iso: string, now: number = Date.now()): boolean {
  const t = new Date(iso).getTime();
  return !Number.isNaN(t) && now - t > STALE_MINUTES * 60_000;
}

/** Side effects (DD §8 preview.side_effects) as plain sentences, before and after they happen. */
export function effectText(effect: string, done: boolean): string {
  const channel = effect.startsWith("slack_post:") ? effect.slice("slack_post:".length) : null;
  if (channel) return done ? `Posting to ${channel}` : `Posts to ${channel}`;
  const plan: Record<string, [string, string]> = {
    task_created: ["Creates a task on the deal", "Task created"],
    owner_changed: ["Changes the deal owner", "Owner changed"],
    draft_dm: ["Sends the draft to you in Slack, never to the customer", "Draft on its way to your Slack"],
    deal_updated: ["Updates the deal record", "Deal updated"],
    risk_recomputed: ["Re-scores the deal's health", "Health re-scored"],
  };
  return plan[effect]?.[done ? 1 : 0] ?? effect.replace(/_/g, " ");
}
