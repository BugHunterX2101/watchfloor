// The harness API (services/harness/api): same origin, cookie session.

import {
  parseActions,
  type AppAction,
  type BandName,
  type PageName,
  type SegmentName,
  type SortName,
  type ThemeName,
} from "./voice/actions";

export type Role = "user" | "assistant";

export interface Line {
  role: Role;
  text: string;
}

export interface Call {
  tool: string;
  arguments: Record<string, unknown>;
  ms: number;
  ok: boolean;
}

export interface View {
  uri: string;
  tool: string;
  data: string; // the tool's structuredContent, as JSON
}

export interface Turn {
  reply: string;
  engine: "strands" | "offline";
  calls: Call[];
  ui: View | null;
  /** What the assistant asked the page to do (checked here: only known actions with valid fields). */
  actions: AppAction[];
}

/** What the page tells the assistant about itself: fixed words only (the server rejects anything else). */
export interface Screen {
  page: PageName;
  theme: ThemeName;
  read_aloud: boolean;
  hands_free: boolean;
  dialog: "none" | "settings" | "deal" | "confirm";
  segment?: SegmentName;
  band?: BandName;
  sort?: SortName;
}

export interface SessionInfo {
  signed_in: boolean;
  engine: "strands" | "offline" | null;
}

export class SignedOut extends Error {}

// No request waits forever: a hung one would leave the composer disabled for good. The limits sit
// above the server's own (a turn stops at 25 s, a tool call at 15 s) so its answer wins the race.
const LIMIT_MS = { turn: 45_000, tool: 30_000, quick: 15_000 } as const;

async function timedFetch(url: string, init: RequestInit, limit: number): Promise<Response> {
  try {
    return await fetch(url, { ...init, credentials: "same-origin", signal: AbortSignal.timeout(limit) });
  } catch (err) {
    if (err instanceof DOMException && err.name === "TimeoutError") {
      throw new Error("the server took too long to answer. Please try again");
    }
    throw err;
  }
}

/** At most `max` UTF-16 units, never cutting an emoji in half (a lone surrogate is not valid text). */
function clip(text: string, max: number): string {
  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  return /[\ud800-\udbff]$/.test(cut) ? cut.slice(0, -1) : cut;
}

export async function session(): Promise<SessionInfo> {
  const r = await timedFetch("/api/session", {}, LIMIT_MS.quick);
  if (!r.ok) throw new Error(`session check failed (HTTP ${r.status})`);
  return (await r.json()) as SessionInfo;
}

export async function converse(message: string, history: Line[], context?: Screen): Promise<Turn> {
  const r = await timedFetch(
    "/converse",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // The API takes 24 lines of at most 2,000 characters and a message of at most 500.
      body: JSON.stringify({
        message: clip(message, 500),
        history: history.slice(-24).map((l) => ({ role: l.role, text: clip(l.text, 2000) })),
        ...(context ? { context } : {}),
      }),
    },
    LIMIT_MS.turn,
  );
  if (r.status === 401) throw new SignedOut();
  const body = (await r.json().catch(() => ({}))) as Partial<Omit<Turn, "actions">> & {
    actions?: unknown;
    message?: string;
    error?: string;
  };
  if (!r.ok) throw new Error(body.message ?? body.error ?? `HTTP ${r.status}`);
  return { ...(body as Omit<Turn, "actions">), actions: parseActions(body.actions) };
}

export interface ToolResult {
  structuredContent: Record<string, unknown> | null;
  content: { type: "text"; text: string }[];
  isError: boolean;
  call: Call;
}

/** A tool call from a ui:// view, made with the signed-in user's token (allowlisted server-side). */
export async function callTool(name: string, args: Record<string, unknown>): Promise<ToolResult> {
  const r = await timedFetch(
    "/api/tool",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, arguments: args }),
    },
    LIMIT_MS.tool,
  );
  if (r.status === 401) throw new SignedOut();
  const body = (await r.json().catch(() => ({}))) as Partial<ToolResult> & { message?: string; error?: string };
  if (!r.ok) throw new Error(body.message ?? body.error ?? `HTTP ${r.status}`);
  return body as ToolResult;
}

export async function logout(): Promise<void> {
  const r = await timedFetch("/api/logout", { method: "POST" }, LIMIT_MS.quick);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
}
