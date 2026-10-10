// structuredContent shapes, mirroring packages/core/watchfloor_core/models/tools.py (the *Out models).

export type Band = "at_risk" | "watch" | "healthy";
export type Factor = "R" | "I" | "G" | "S";

export interface BriefingDeal {
  deal_id: string;
  name: string;
  account: string;
  amount_cents?: number | null;
  amount_display?: string | null;
  stage: string;
  owner: string;
  /** RIGS risk (higher is worse); people see `health`. */
  score: number;
  health?: number;
  band: Band;
  top_reason: string;
  new_since_last_briefing: boolean;
}

export interface Briefing {
  summary: string;
  all_clear: boolean;
  generated_at: string;
  new_count: number;
  attention_count: number;
  triggered_rules: { rule_id: string; description: string; deal_ids: string[] }[];
  deals: BriefingDeal[];
}

export interface DealCard {
  deal_id: string;
  name: string;
  account: string;
  amount_display?: string | null;
  stage: string;
  owner: string;
  close_date_display: string;
  next_step?: string | null;
}

export interface Explain {
  summary: string;
  deal: DealCard;
  risk: {
    score: number;
    health?: number;
    band: Band;
    band_since: string;
    components: Record<string, number>;
    computed_at: string;
  };
  reasons: { code: string; factor: Factor; text: string; evidence_ids: string[] }[];
  other_findings: { code: string; factor: Factor; text: string }[];
  trend_14d: number[];
}

export interface RecommendedAction {
  action_type: "create_task" | "reassign_owner" | "notify_channel" | "draft_followup";
  params: Record<string, string>;
  rationale: string;
  play_id: string;
  confidence: number;
}

export interface TriageResult {
  summary: string;
  job_id: string;
  deal_id: string;
  status: "queued" | "running" | "done" | "failed" | "timed_out";
  voice_summary?: string | null;
  drivers: { factor: Factor; claim: string; evidence_ids: string[] }[];
  recommended_actions: RecommendedAction[];
  finished_at?: string | null;
}

export interface Proposal {
  summary: string;
  requires_confirmation: boolean;
  proposal_id: string;
  confirmation_token: string;
  expires_at: string;
  preview: Record<string, unknown>;
}

export interface Confirmed {
  summary: string;
  status: "executed" | "already_executed";
  action_type: string;
  result?: Record<string, unknown>;
  audit_id?: string;
  side_effects?: string[];
  /** Until when undo_action can reverse it (ADR-021); absent when it can't be undone. */
  undo_until?: string;
}

export interface Extended {
  summary: string;
  expires_at: string;
}

export interface Undone {
  summary: string;
  status: "undone";
  action_type: string;
  audit_id: string;
  reverted: string[];
}

export type Data = Record<string, unknown>;

export type View =
  | { kind: "board"; data: Briefing }
  | { kind: "detail"; data: Explain; plan?: TriageResult }
  | { kind: "plan"; data: TriageResult }
  | { kind: "confirm"; data: Proposal }
  /** ``token`` is known when the approval was tapped here, which is what makes Undo possible. */
  | { kind: "done"; data: Confirmed; token?: string }
  | { kind: "undone"; data: Undone }
  | { kind: "message"; text: string };

/** Which screen a tool result belongs on, from its shape (the UI renders only structuredContent). */
export function viewFor(d: Data): View | null {
  if (Array.isArray(d.deals) && "all_clear" in d) return { kind: "board", data: d as unknown as Briefing };
  if (d.risk && d.deal) return { kind: "detail", data: d as unknown as Explain };
  if (typeof d.job_id === "string" && Array.isArray(d.recommended_actions)) {
    return { kind: "plan", data: d as unknown as TriageResult };
  }
  if (typeof d.confirmation_token === "string") return { kind: "confirm", data: d as unknown as Proposal };
  const pending = d.pending_proposal as Data | null | undefined;
  if (pending && typeof pending.confirmation_token === "string") {
    return { kind: "confirm", data: pending as unknown as Proposal };
  }
  if ((d.status === "executed" || d.status === "already_executed") && typeof d.action_type === "string") {
    return { kind: "done", data: d as unknown as Confirmed };
  }
  if (d.status === "undone" && Array.isArray(d.reverted)) return { kind: "undone", data: d as unknown as Undone };
  if (typeof d.summary === "string") return { kind: "message", text: d.summary };
  return null;
}
