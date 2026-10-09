// Deal detail (DD §15.4): score and band, RIGS factor bars, reasons, trend; plan and Propose.

import { BAND_LABEL, dealTitle, FACTOR_NAME, FACTORS, percent, since } from "./format";
import type { Explain, RecommendedAction, TriageResult } from "./types";

function Trend({ points }: { points: number[] }) {
  if (points.length < 2) return null;
  const w = 140;
  const h = 32;
  const step = w / (points.length - 1);
  const y = (v: number) => h - (Math.min(100, Math.max(0, v)) / 100) * h;
  const path = points.map((v, i) => `${i ? "L" : "M"}${(i * step).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  return (
    <figure class="trend">
      <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`14-day trend from ${points[0]} to ${points[points.length - 1]}`}>
        <path d={path} fill="none" stroke="currentColor" stroke-width="2" />
      </svg>
      <figcaption class="meta">14-day trend</figcaption>
    </figure>
  );
}

export function Detail(props: { data: Explain; busy: boolean; dig: () => void; plan: () => void; status?: string }) {
  const { data, busy } = props;
  const { deal, risk } = data;
  const reasonFor = (f: string) => data.reasons.find((r) => r.factor === f)?.text ?? "";
  return (
    <section class="screen detail" aria-label={`${deal.account} detail`}>
      <div class="detail-left">
        <p class="title">{deal.account}</p>
        <p>{dealTitle(deal.account, deal.name)}</p>
        <p class="meta">
          {[deal.amount_display, deal.stage, deal.owner].filter(Boolean).join(" · ")}
        </p>
        <p class="big">
          <span class="score">{risk.score}</span>
          <span class={`band ${risk.band}`}>{BAND_LABEL[risk.band]}</span>
        </p>
        <p class="meta">{since(risk.band_since)}</p>
        <Trend points={data.trend_14d} />
        <div class="actions">
          <button type="button" disabled={busy} onClick={props.dig}>
            Dig into it
          </button>
          <button type="button" class="secondary" disabled={busy} onClick={props.plan}>
            Show the plan
          </button>
        </div>
        {props.status && <p class="status" role="status">{props.status}</p>}
      </div>

      <div class="detail-right">
        <p class="eyebrow">WHY IT'S {BAND_LABEL[risk.band]}</p>
        <ul class="factors">
          {FACTORS.map((f) => {
            const value = risk.components[f.toLowerCase()] ?? 0;
            return (
              <li key={f} class="factor">
                <span class="factor-name" title={FACTOR_NAME[f]}>
                  {f}
                </span>
                <span class="bar" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent(value)}
                  aria-label={`${FACTOR_NAME[f]} ${value.toFixed(2)}`}>
                  <span style={{ width: `${percent(value)}%` }} />
                </span>
                <span class="value">{value.toFixed(2)}</span>
                <span class="factor-why meta">{reasonFor(f)}</span>
              </li>
            );
          })}
        </ul>
        {data.other_findings.length > 0 && (
          <>
            <p class="eyebrow">ALSO NOTICED</p>
            <ul class="findings">
              {data.other_findings.map((f) => (
                <li key={f.code}>{f.text}</li>
              ))}
            </ul>
          </>
        )}
      </div>
    </section>
  );
}

const ACTION_LABEL: Record<RecommendedAction["action_type"], string> = {
  create_task: "Create a task",
  reassign_owner: "Reassign the owner",
  notify_channel: "Tell the team",
  draft_followup: "Draft a follow-up",
};

function describe(a: RecommendedAction): string {
  const p = a.params;
  switch (a.action_type) {
    case "create_task":
      return `${p.assignee_name ? `${p.assignee_name}: ` : ""}${p.title ?? ""}`;
    case "reassign_owner":
      return `To ${p.new_owner_name ?? "a teammate"}`;
    case "notify_channel":
      return p.message ?? "";
    case "draft_followup":
      return p.intent ?? "";
  }
}

export function Plan(props: { data: TriageResult; busy: boolean; propose: (a: RecommendedAction) => void }) {
  const { data, busy } = props;
  if (data.status !== "done") {
    return (
      <section class="screen">
        <div class="card notice" role="status">
          <p class="title">Deep review: {data.status.replace("_", " ")}</p>
          <p class="muted">{data.summary}</p>
        </div>
      </section>
    );
  }
  return (
    <section class="screen plan" aria-label="Recommended plan">
      <p class="eyebrow">PLAN</p>
      {data.voice_summary && <p>{data.voice_summary}</p>}
      <ol class="plan-actions">
        {data.recommended_actions.map((a) => (
          <li key={`${a.play_id}-${a.action_type}`} class="card plan-action">
            <div>
              <p class="plan-title">{ACTION_LABEL[a.action_type]}</p>
              <p>{describe(a)}</p>
              <p class="meta">{a.rationale}</p>
            </div>
            <button type="button" disabled={busy} onClick={() => props.propose(a)}>
              Propose
            </button>
          </li>
        ))}
      </ol>
      {data.recommended_actions.length === 0 && <p class="muted">No actions were recommended.</p>}
      {data.drivers.length > 0 && (
        <>
          <p class="eyebrow">DRIVERS</p>
          <ul class="findings">
            {data.drivers.map((d, i) => (
              <li key={i}>
                <strong>{d.factor}</strong> {d.claim}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
