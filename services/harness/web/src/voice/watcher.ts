// What changed on the board between two looks at it, in words for the ear. Hands-free keeps looking
// at the pipeline while it is on (main.ts polls the same get_risk_briefing the Pipeline page uses) and
// speaks up when something needs attention: a deal that has slipped to at risk, one that dropped
// sharply, one that recovered, a new at-risk deal, or a standing order that just triggered. The first
// look only sets the baseline; nothing is said about what was already true when it was switched on.
//
// Pure (no DOM, no storage, no network), so `npm test` checks the wording and the thresholds directly.

import type { Band, Briefing } from "../logic.ts";

export interface SeenDeal {
  name: string;
  band: Band;
  health: number;
}

export interface Snapshot {
  deals: Record<string, SeenDeal>;
  /** Standing orders that are triggered right now. */
  rules: Record<string, string>;
}

export interface News {
  say: string;
  /** What this announcement covers, so the same thing is not announced again soon. */
  keys: string[];
}

export const DROP_POINTS = 10; // a fall this size is worth a sentence even within a band
export const COOLDOWN_MS = 10 * 60_000; // the same news about the same deal is said once in this long
export const MAX_ITEMS = 3; // one announcement carries at most this many items; the rest are counted

export function snapshot(b: Briefing): Snapshot {
  const deals: Record<string, SeenDeal> = {};
  for (const d of b.deals) deals[d.deal_id] = { name: d.account || d.name, band: d.band, health: d.health };
  const rules: Record<string, string> = {};
  for (const r of b.triggered_rules) if (r.rule_id) rules[r.rule_id] = r.description;
  return { deals, rules };
}

const RANK: Record<Band, number> = { healthy: 0, watch: 1, at_risk: 2 };

interface Item {
  key: string;
  weight: number; // lower is said first
  text: string;
}

const ruleText = (description: string): string => {
  const d = description.replace(/\s+/g, " ").trim();
  return d.length > 90 ? `${d.slice(0, 87)}...` : d;
};

/** What is new in `next` since `prev` and has not been said lately; null when there is nothing to say. */
export function compare(
  prev: Snapshot,
  next: Snapshot,
  told: ReadonlyMap<string, number>,
  now: number,
): News | null {
  const items: Item[] = [];
  for (const [id, d] of Object.entries(next.deals)) {
    const before = prev.deals[id];
    if (!before) {
      if (d.band === "at_risk") {
        items.push({ key: `${id}:new`, weight: 0, text: `A new at-risk deal: ${d.name}, health ${d.health}.` });
      }
      continue;
    }
    const fell = before.health - d.health;
    if (RANK[d.band] > RANK[before.band]) {
      items.push(
        d.band === "at_risk"
          ? { key: `${id}:risk`, weight: 0, text: `${d.name} slipped to at risk, health ${d.health}.` }
          : { key: `${id}:watch`, weight: 3, text: `${d.name} moved to watch, health ${d.health}.` },
      );
    } else if (fell >= DROP_POINTS && d.band !== "healthy") {
      items.push({ key: `${id}:drop`, weight: 2, text: `${d.name} dropped ${fell} points, to health ${d.health}.` });
    } else if (RANK[d.band] < RANK[before.band] && d.band === "healthy") {
      items.push({ key: `${id}:up`, weight: 4, text: `Good news: ${d.name} is healthy again, health ${d.health}.` });
    }
  }
  for (const [id, description] of Object.entries(next.rules)) {
    if (id in prev.rules) continue;
    items.push({ key: `rule:${id}`, weight: 1, text: `Your standing order “${ruleText(description)}” just triggered.` });
  }
  const fresh = items
    .filter((i) => now - (told.get(i.key) ?? Number.NEGATIVE_INFINITY) >= COOLDOWN_MS)
    .sort((a, b) => a.weight - b.weight);
  if (!fresh.length) return null;
  const said = fresh.slice(0, MAX_ITEMS);
  const more = fresh.length - said.length;
  const tail = more > 0 ? ` And ${more} more ${more === 1 ? "change" : "changes"} on the board.` : "";
  return { say: `Heads up. ${said.map((i) => i.text).join(" ")}${tail}`, keys: fresh.map((i) => i.key) };
}
