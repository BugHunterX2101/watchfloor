// Pure helpers behind the pages (no DOM, no storage): greetings, call statistics, deal sorting and
// filtering, the health-ring arithmetic. Kept apart so `npm test` can check the numbers directly.

export type Band = "at_risk" | "watch" | "healthy";

export const BAND_LABEL: Record<Band, string> = { at_risk: "At risk", watch: "Watch", healthy: "Healthy" };

/** Most urgent first: the order bands are listed in, filtered by and sorted by. */
export const BAND_ORDER: readonly Band[] = ["at_risk", "watch", "healthy"];

export interface BriefingDeal {
  deal_id: string;
  name: string;
  account: string;
  amount_cents: number | null;
  amount_display: string | null;
  stage: string;
  owner: string;
  score: number;
  health: number;
  band: Band;
  top_reason: string;
  new_since_last_briefing: boolean;
}

export interface TriggeredRule {
  rule_id: string;
  description: string;
  deal_ids: string[];
}

export interface Briefing {
  summary: string;
  all_clear: boolean;
  generated_at: string;
  new_count: number;
  attention_count: number;
  triggered_rules: TriggeredRule[];
  deals: BriefingDeal[];
}

/** "Good morning" by the viewer's local hour (0-23). 05:00-11:59 morning, 12:00-17:59 afternoon,
 *  18:00-21:59 evening, otherwise a late-night line rather than a wrong "morning". */
export function greeting(hour: number, name = ""): string {
  const h = Number.isFinite(hour) ? ((Math.floor(hour) % 24) + 24) % 24 : 12;
  const part =
    h >= 5 && h < 12
      ? "Good morning"
      : h >= 12 && h < 18
        ? "Good afternoon"
        : h >= 18 && h < 22
          ? "Good evening"
          : "Burning the midnight oil";
  const who = name.trim();
  return who ? `${part}, ${who}.` : `${part}.`;
}

/** Nearest-rank percentile of an ascending list; 0 for an empty list. */
export function percentile(sorted: readonly number[], p: number): number {
  if (!sorted.length) return 0;
  const rank = Math.ceil((Math.min(100, Math.max(0, p)) / 100) * sorted.length);
  return sorted[Math.max(0, rank - 1)];
}

export interface CallLike {
  tool: string;
  ms: number;
  ok: boolean;
}

export interface CallStats {
  count: number;
  errors: number;
  writes: number;
  errorRate: number; // 0..1
  avg: number;
  p50: number;
  p95: number;
  max: number;
}

export function callStats(calls: readonly CallLike[], writes: ReadonlySet<string>): CallStats {
  const times = calls.map((c) => c.ms).sort((a, b) => a - b);
  const count = calls.length;
  const errors = calls.filter((c) => !c.ok).length;
  return {
    count,
    errors,
    writes: calls.filter((c) => writes.has(c.tool)).length,
    errorRate: count ? errors / count : 0,
    avg: count ? Math.round(times.reduce((a, b) => a + b, 0) / count) : 0,
    p50: percentile(times, 50),
    p95: percentile(times, 95),
    max: count ? times[count - 1] : 0,
  };
}

/** "just now", "3 min ago", "2 h ago", "yesterday", "5 days ago". `then` and `now` are epoch ms. */
export function relativeTime(then: number, now: number): string {
  const s = Math.max(0, Math.round((now - then) / 1000));
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(s / 3600);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(s / 86400);
  return d === 1 ? "yesterday" : `${d} days ago`;
}

/** Newest first, no repeats (case-insensitive), at most `max` entries. */
export function pushRecent(list: readonly string[], text: string, max = 6): string[] {
  const t = text.trim().slice(0, 120);
  if (!t) return [...list].slice(0, max);
  return [t, ...list.filter((x) => x.toLowerCase() !== t.toLowerCase())].slice(0, max);
}

export function togglePin(pins: readonly string[], id: string, max = 5): { pins: string[]; full: boolean } {
  if (pins.includes(id)) return { pins: pins.filter((p) => p !== id), full: false };
  if (pins.length >= max) return { pins: [...pins], full: true };
  return { pins: [...pins, id], full: false };
}

export type SortKey = "urgency" | "health" | "amount" | "name";

export interface DealFilter {
  band: Band | "all";
  query: string;
  sort: SortKey;
  pins: readonly string[];
}

/** Pinned deals lead; then the chosen order. Filtering never mutates the input. */
export function arrangeDeals(deals: readonly BriefingDeal[], f: DealFilter): BriefingDeal[] {
  const q = f.query.trim().toLowerCase();
  const rank = (d: BriefingDeal): number => BAND_ORDER.indexOf(d.band);
  const cmp: Record<SortKey, (a: BriefingDeal, b: BriefingDeal) => number> = {
    urgency: (a, b) => rank(a) - rank(b) || a.health - b.health,
    health: (a, b) => a.health - b.health,
    amount: (a, b) => (b.amount_cents ?? -1) - (a.amount_cents ?? -1),
    name: (a, b) => a.name.localeCompare(b.name),
  };
  return deals
    .filter(
      (d) =>
        (f.band === "all" || d.band === f.band) &&
        (!q || `${d.name} ${d.account} ${d.owner} ${d.stage}`.toLowerCase().includes(q)),
    )
    .sort(
      (a, b) =>
        Number(f.pins.includes(b.deal_id)) - Number(f.pins.includes(a.deal_id)) ||
        cmp[f.sort](a, b) ||
        a.name.localeCompare(b.name),
    );
}

/** Stroke dash for a health ring of radius `r`: the arc is health% of the circumference. */
export function ringDash(health: number, r: number): { arc: number; gap: number; circumference: number } {
  const circumference = 2 * Math.PI * r;
  const pct = Math.min(100, Math.max(0, Number.isFinite(health) ? health : 0)) / 100;
  const arc = circumference * pct;
  return { arc, gap: circumference - arc, circumference };
}

/** Totals for the strip above the board. Amounts are summed only where the API gave one. */
export function boardTotals(deals: readonly BriefingDeal[]): {
  byBand: Record<Band, number>;
  atRiskCents: number;
  unknownAmounts: number;
} {
  const byBand: Record<Band, number> = { at_risk: 0, watch: 0, healthy: 0 };
  let atRiskCents = 0;
  let unknownAmounts = 0;
  for (const d of deals) {
    byBand[d.band] += 1;
    if (d.band === "at_risk") {
      if (d.amount_cents == null) unknownAmounts += 1;
      else atRiskCents += d.amount_cents;
    }
  }
  return { byBand, atRiskCents, unknownAmounts };
}

const trimNumber = (n: number): string => (n >= 100 ? Math.round(n).toString() : n.toFixed(1).replace(/\.0$/, ""));

/** "$1.2M", "$48K", "$950" from cents (compact money for tiles; cards use the API's own display text). */
export function compactUsd(cents: number): string {
  const usd = Math.max(0, cents) / 100;
  if (usd >= 1_000_000) return `$${trimNumber(usd / 1_000_000)}M`;
  if (usd >= 1_000) return `$${trimNumber(usd / 1_000)}K`;
  return `$${Math.round(usd)}`;
}

/** Health is what people see (ADR-020): 100 minus the RIGS risk score, within 0-100. Servers
 *  before that change sent only the score, so a missing health falls back to it. */
export function healthOf(score: number, health?: number | null): number {
  const v = typeof health === "number" && Number.isFinite(health) ? health : 100 - score;
  return Math.round(Math.min(100, Math.max(0, v)));
}

/** "since Tue" inside the last six days, else "since Oct 5"; "" when the date can't be read. */
export function sinceLabel(iso: string, now = Date.now(), timeZone?: string): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const days = (now - t) / 86_400_000;
  const d = new Date(t);
  const label =
    days < 6
      ? d.toLocaleDateString("en-US", { weekday: "short", timeZone })
      : d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone });
  return `since ${label}`;
}

export type Factor = "R" | "I" | "G" | "S";
export const FACTORS: readonly Factor[] = ["R", "I", "G", "S"];
export const FACTOR_NAME: Record<Factor, string> = {
  R: "Responsiveness",
  I: "Intent",
  G: "Governance",
  S: "Schedule",
};

/** How hard a RIGS factor (0-1) pulls health down, in a word (the same words the views use). */
export function dragLevel(v: number): "High" | "Some" | "Low" | "None" {
  if (v >= 0.6) return "High";
  if (v >= 0.3) return "Some";
  return v > 0 ? "Low" : "None";
}

/** Deal names usually repeat the account ("Kestrel Biotech · Platform expansion"): keep the rest. */
export function dealTitle(account: string, name: string): string {
  if (!name.toLowerCase().startsWith(account.toLowerCase())) return name;
  const rest = name.slice(account.length).replace(/^[\s·:–—-]+/, "");
  return rest || name;
}

const str = (v: unknown, fallback = ""): string => (typeof v === "string" ? v : fallback);
const num = (v: unknown, fallback = 0): number => (typeof v === "number" && Number.isFinite(v) ? v : fallback);
const BAND_SET: ReadonlySet<string> = new Set(BAND_ORDER);
const FACTOR_SET: ReadonlySet<string> = new Set(FACTORS);
const isRecord = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);

/** A get_risk_briefing result, checked and normalised (health filled in); null if it isn't one. */
export function toBriefing(data: unknown): Briefing | null {
  if (!isRecord(data) || !Array.isArray(data.deals) || typeof data.summary !== "string") return null;
  const deals: BriefingDeal[] = [];
  for (const raw of data.deals) {
    if (!isRecord(raw) || typeof raw.deal_id !== "string" || typeof raw.name !== "string") return null;
    if (!BAND_SET.has(str(raw.band)) || typeof raw.score !== "number") return null;
    deals.push({
      deal_id: raw.deal_id,
      name: raw.name,
      account: str(raw.account, raw.name),
      amount_cents: typeof raw.amount_cents === "number" ? raw.amount_cents : null,
      amount_display: typeof raw.amount_display === "string" ? raw.amount_display : null,
      stage: str(raw.stage),
      owner: str(raw.owner),
      score: raw.score,
      health: healthOf(raw.score, typeof raw.health === "number" ? raw.health : null),
      band: raw.band as Band,
      top_reason: str(raw.top_reason),
      new_since_last_briefing: raw.new_since_last_briefing === true,
    });
  }
  const rules = Array.isArray(data.triggered_rules) ? data.triggered_rules : [];
  return {
    summary: data.summary,
    all_clear: data.all_clear === true,
    generated_at: str(data.generated_at),
    new_count: num(data.new_count),
    attention_count: num(data.attention_count),
    triggered_rules: rules.filter(isRecord).map((r) => ({
      rule_id: str(r.rule_id),
      description: str(r.description),
      deal_ids: Array.isArray(r.deal_ids) ? r.deal_ids.filter((x): x is string => typeof x === "string") : [],
    })),
    deals,
  };
}

export interface Explain {
  summary: string;
  deal: {
    deal_id: string;
    name: string;
    account: string;
    amount_display: string | null;
    stage: string;
    owner: string;
    close_date_display: string;
    next_step: string | null;
  };
  health: number;
  band: Band;
  band_since: string;
  components: Record<Factor, number>; // 0-1 pull of each RIGS factor
  reasons: { code: string; factor: Factor; text: string }[];
  other: { code: string; factor: Factor; text: string }[];
  trend: number[]; // health per day, oldest first (the API sends the risk score; this is 100 minus it)
}

const findings = (v: unknown): { code: string; factor: Factor; text: string }[] =>
  (Array.isArray(v) ? v : []).filter(isRecord).map((r) => ({
    code: str(r.code),
    factor: FACTOR_SET.has(str(r.factor)) ? (r.factor as Factor) : "R",
    text: str(r.text),
  }));

/** An explain_deal_risk result, checked and normalised; null if it isn't one. */
export function toExplain(data: unknown): Explain | null {
  if (!isRecord(data) || !isRecord(data.deal) || !isRecord(data.risk) || typeof data.risk.score !== "number") return null;
  const { deal, risk } = data;
  if (!BAND_SET.has(str(risk.band)) || typeof deal.deal_id !== "string") return null;
  const parts = isRecord(risk.components) ? risk.components : {};
  return {
    summary: str(data.summary),
    deal: {
      deal_id: deal.deal_id,
      name: str(deal.name),
      account: str(deal.account, str(deal.name)),
      amount_display: typeof deal.amount_display === "string" ? deal.amount_display : null,
      stage: str(deal.stage),
      owner: str(deal.owner),
      close_date_display: str(deal.close_date_display),
      next_step: typeof deal.next_step === "string" ? deal.next_step : null,
    },
    health: healthOf(risk.score as number, typeof risk.health === "number" ? risk.health : null),
    band: risk.band as Band,
    band_since: str(risk.band_since),
    components: {
      R: num(parts.r),
      I: num(parts.i),
      G: num(parts.g),
      S: num(parts.s),
    },
    reasons: findings(data.reasons),
    other: findings(data.other_findings),
    trend: (Array.isArray(data.trend_14d) ? data.trend_14d : []).filter((n): n is number => typeof n === "number").map((s) => healthOf(s)),
  };
}

/** FNV-1a: a small, stable string hash (the same deal always lands in the same place). */
export function hash32(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** Where a column can stand on the floor (world units, left and right of the clear middle). */
export const SKYLINE_LANES = [-9, -7.4, -5.8, -4.2, 4.2, 5.8, 7.4, 9] as const;

export interface SkylineDeal {
  id: string;
  band: Band;
  health: number;
}

export interface Pillar extends SkylineDeal {
  x: number; // lane
  height: number; // world units: taller means lower health
}

/** The background's skyline: one column per deal (at most five, as in a briefing), each in its own
 *  lane chosen by the deal's id, with a height that grows as health falls (1.2 at health 100, 5.5 at 0). */
export function skyline(deals: readonly SkylineDeal[]): Pillar[] {
  const taken = new Set<number>();
  return deals.slice(0, 5).map((d) => {
    let lane = hash32(d.id) % SKYLINE_LANES.length;
    while (taken.has(lane)) lane = (lane + 1) % SKYLINE_LANES.length;
    taken.add(lane);
    const risk = 100 - Math.min(100, Math.max(0, Number.isFinite(d.health) ? d.health : 0));
    return { ...d, x: SKYLINE_LANES[lane], height: Math.round((1.2 + (risk / 100) * 4.3) * 100) / 100 };
  });
}

/** A deal or account name becomes part of a chat prompt: control characters out, whitespace
 *  collapsed, length capped (the API takes 500 characters; a name needs far fewer). */
export function promptSafe(text: string, max = 80): string {
  return text
    .replace(/\p{Cc}/gu, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, max);
}
