// The harness API (services/harness/api): same origin, cookie session.

import { parseAwaiting } from "./voice/context";
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
  /** What still waits for the user's answer, change or undo (null when nothing does). */
  awaiting: Awaiting | null;
}

export interface Awaiting {
  /** preview: a change read back, waiting for yes. ask: a rule held for yes. undo: just done, can be undone. */
  kind: "preview" | "ask" | "undo";
  summary: string;
  /** Epoch seconds when it lapses. */
  until: number;
  /** This turn made it, so the question just asked is about it. */
  fresh: boolean;
}

/** The board as the assistant is shown it, so "the second one" means the second deal listed. */
export interface BoardEntry {
  id: string;
  name: string;
  band: "at_risk" | "watch" | "healthy";
  health: number;
}

/** What the browser knows about the user, sent with each message as context (never as orders). */
export interface Profile {
  name?: string;
  day?: "Monday" | "Tuesday" | "Wednesday" | "Thursday" | "Friday" | "Saturday" | "Sunday";
  part?: "morning" | "afternoon" | "evening" | "night";
  style?: "brief" | "normal" | "detailed";
  pins?: string[];
  /** Corrections the user taught (voice/lessons.ts): heard → meant. */
  lessons?: { heard: string; meant: string }[];
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
  /** The deal whose sheet is open: "this deal" and "it" mean this one. */
  open_deal?: string;
  /** The deals the board lists now, in order (at most five). */
  board?: BoardEntry[];
}

export interface SessionInfo {
  signed_in: boolean;
  engine: "strands" | "offline" | null;
  /** "polly": the server reads replies aloud in Amazon Polly's voice (`/api/speak`). */
  voice?: "polly" | null;
}

export class SignedOut extends Error {}

// No request waits forever: a hung one would leave the composer disabled for good. The limits sit
// above the server's own (a turn stops at 25 s, 40 s at most on Render; a tool call at 15 s) so its
// answer wins the race.
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

export interface ConverseOptions {
  context?: Screen;
  profile?: Profile;
  /** Said before the user heard the previous reply: it can change what they asked, never confirm it. */
  unheard?: boolean;
}

export async function converse(message: string, history: Line[], opts: ConverseOptions = {}): Promise<Turn> {
  const r = await timedFetch(
    "/converse",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // The API takes 24 lines of at most 2,000 characters and a message of at most 500.
      body: JSON.stringify({
        message: clip(message, 500),
        history: history.slice(-24).map((l) => ({ role: l.role, text: clip(l.text, 2000) })),
        ...(opts.context ? { context: opts.context } : {}),
        ...(opts.profile ? { profile: opts.profile } : {}),
        ...(opts.unheard ? { unheard: true } : {}),
      }),
    },
    LIMIT_MS.turn,
  );
  if (r.status === 401) throw new SignedOut();
  const body = (await r.json().catch(() => ({}))) as Partial<Omit<Turn, "actions" | "awaiting">> & {
    actions?: unknown;
    awaiting?: unknown;
    message?: string;
    error?: string;
  };
  if (!r.ok) throw new Error(body.message ?? body.error ?? `HTTP ${r.status}`);
  return {
    ...(body as Omit<Turn, "actions" | "awaiting">),
    actions: parseActions(body.actions),
    awaiting: parseAwaiting(body.awaiting),
  };
}

/** The user said no: forget the change that was waiting for an answer, so a later yes cannot confirm it. */
export async function dismiss(): Promise<void> {
  const r = await timedFetch("/api/dismiss", { method: "POST" }, LIMIT_MS.quick);
  if (r.status === 401) throw new SignedOut();
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
}

export interface ToolResult {
  structuredContent: Record<string, unknown> | null;
  content: { type: "text"; text: string }[];
  isError: boolean;
  call: Call;
  /** A preview made by a view's own tap can be answered by voice: the page learns it here. */
  awaiting?: unknown;
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
