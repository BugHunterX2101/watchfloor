// The harness API (services/harness/api): same origin, cookie session.

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
  html: string;
  tool: string;
  data: string; // the tool's structuredContent, as JSON
}

export interface Turn {
  reply: string;
  engine: "strands" | "offline";
  calls: Call[];
  ui: View | null;
}

export interface SessionInfo {
  signed_in: boolean;
  engine: "strands" | "offline" | null;
}

export class SignedOut extends Error {}

export async function session(): Promise<SessionInfo> {
  const r = await fetch("/api/session", { credentials: "same-origin" });
  if (!r.ok) throw new Error(`session check failed (HTTP ${r.status})`);
  return (await r.json()) as SessionInfo;
}

export async function converse(message: string, history: Line[]): Promise<Turn> {
  const r = await fetch("/converse", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, history: history.slice(-24) }),
  });
  if (r.status === 401) throw new SignedOut();
  const body = (await r.json().catch(() => ({}))) as Partial<Turn> & { message?: string; error?: string };
  if (!r.ok) throw new Error(body.message ?? body.error ?? `HTTP ${r.status}`);
  return body as Turn;
}

export interface ToolResult {
  structuredContent: Record<string, unknown> | null;
  content: { type: "text"; text: string }[];
  isError: boolean;
  call: Call;
}

/** A tool call from a ui:// view, made with the signed-in user's token (allowlisted server-side). */
export async function callTool(name: string, args: Record<string, unknown>): Promise<ToolResult> {
  const r = await fetch("/api/tool", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, arguments: args }),
  });
  if (r.status === 401) throw new SignedOut();
  const body = (await r.json().catch(() => ({}))) as Partial<ToolResult> & { message?: string; error?: string };
  if (!r.ok) throw new Error(body.message ?? body.error ?? `HTTP ${r.status}`);
  return body as ToolResult;
}

export async function logout(): Promise<void> {
  await fetch("/api/logout", { method: "POST", credentials: "same-origin" });
}
