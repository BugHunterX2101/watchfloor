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
