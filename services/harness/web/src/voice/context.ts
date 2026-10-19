// What the assistant is told about the person and the screen with each message, built from what the
// browser already holds (their name and reply length, the deals they pinned, the clock, the board as
// listed). It is context for the model to use, never an instruction: the server accepts only these
// fields, in these shapes, and puts them in the prompt as facts.
//
// Pure (no DOM, no storage), so `npm test` checks it directly.

import type { Awaiting, BoardEntry, Profile } from "../api.ts";

/** What the server says still waits, checked: only the three kinds, a short summary, a number. */
export function parseAwaiting(raw: unknown): Awaiting | null {
  if (!raw || typeof raw !== "object") return null;
  const a = raw as Record<string, unknown>;
  if (a.kind !== "preview" && a.kind !== "ask" && a.kind !== "undo") return null;
  if (typeof a.summary !== "string" || typeof a.until !== "number" || !Number.isFinite(a.until)) return null;
  return { kind: a.kind, summary: a.summary.slice(0, 200), until: a.until, fresh: a.fresh === true };
}

const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"] as const;

/** The same boundaries the greeting uses (logic.ts), in the words the assistant is given. */
export function partOfDay(hour: number): NonNullable<Profile["part"]> {
  const h = Number.isFinite(hour) ? ((Math.floor(hour) % 24) + 24) % 24 : 12;
  if (h >= 5 && h < 12) return "morning";
  if (h >= 12 && h < 18) return "afternoon";
  if (h >= 18 && h < 22) return "evening";
  return "night";
}

export interface ProfileInput {
  name: string;
  style: "brief" | "normal" | "detailed";
}

export function profileOf(
  s: ProfileInput,
  pinned: readonly string[],
  now: Date,
  taught: readonly { heard: string; meant: string }[] = [],
): Profile {
  const profile: Profile = {
    day: DAYS[now.getDay()],
    part: partOfDay(now.getHours()),
    style: s.style,
  };
  const name = s.name.replace(/\s+/g, " ").trim().slice(0, 40);
  if (name) profile.name = name;
  const pins = pinned.map((p) => p.replace(/\s+/g, " ").trim().slice(0, 60)).filter(Boolean).slice(0, 5);
  if (pins.length) profile.pins = pins;
  const lessons = taught.slice(0, 8).map((l) => ({ heard: l.heard, meant: l.meant }));
  if (lessons.length) profile.lessons = lessons;
  return profile;
}

export interface BoardDeal {
  deal_id: string;
  account: string;
  name: string;
  band: BoardEntry["band"];
  health: number;
}

/** The board as listed, in order: at most five deals, with the id the tools need for each. */
export function boardOf(deals: readonly BoardDeal[]): BoardEntry[] {
  return deals
    .filter((d) => /^[A-Za-z0-9_-]{1,64}$/.test(d.deal_id) && Number.isFinite(d.health))
    .slice(0, 5)
    .map((d) => ({
      id: d.deal_id,
      name: (d.account || d.name).replace(/\s+/g, " ").trim().slice(0, 60),
      band: d.band,
      health: Math.min(100, Math.max(0, Math.round(d.health))),
    }));
}
