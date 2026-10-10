// Deal detail (DD §15.4): health and band, RIGS factor bars, reasons, trend; plan and Propose.

import { BandChip } from "./Band";
import {
  ago,
  AT_RISK_HEALTH,
  BAND_SPOKEN,
  capitalize,
  clock,
  dealTitle,
  dragLevel,
  FACTOR_NAME,
  FACTORS,
  healthOf,
  isStale,
  percent,
  since,
  WATCH_HEALTH,
} from "./format";
import type { Explain, RecommendedAction, TriageResult } from "./types";

/** Health over 14 days (the server sends risk points; health is 100 minus each). Fixed 0–100 scale
 *  (an auto-scaled axis turns a 2-point wobble into a cliff), the at-risk zone shaded along the
 *  bottom and the watch line dashed, so the line reads against the bands, not just against itself. */
function Trend({ risk, stale }: { risk: number[]; stale: boolean }) {
  if (risk.length < 2) return null;
  const points = risk.map((v) => 100 - v);
  const w = 140;
  const h = 32;
  const step = w / (points.length - 1);
  const y = (v: number) => h - (Math.min(100, Math.max(0, v)) / 100) * h;
  const path = points.map((v, i) => `${i ? "L" : "M"}${(i * step).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const first = points[0];
  const last = points[points.length - 1];
  const delta = last - first;
  const change = delta === 0 ? "no change" : `${delta > 0 ? "up" : "down"} ${Math.abs(delta)}`;
  return (
    <figure class="trend">
      <svg
        viewBox={`-4 -4 ${w + 8} ${h + 8}`}
        role="img"
        aria-label={`14-day health trend from ${first} to ${last}, ${change}. Shaded area is the at-risk zone.`}
      >
        <rect class="zone" x="0" y={y(AT_RISK_HEALTH)} width={w} height={h - y(AT_RISK_HEALTH)} />
        <line class="threshold" x1="0" x2={w} y1={y(WATCH_HEALTH)} y2={y(WATCH_HEALTH)} />
        <path d={path} fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
        {/* Today, marked: where the line ends is the health shown above; hollow when it is stale. */}
        <circle class={stale ? "end stale" : "end"} cx={w} cy={y(last)} r="3.5" />
      </svg>
      <figcaption class="meta">
        14-day trend · {first} → {last} ({change})
      </figcaption>
    </figure>
  );
}

export function Detail(props: { data: Explain; busy: boolean; dig: () => void; plan: () => void; status?: string }) {
  const { data, busy } = props;
  const { deal, risk } = data;
  const reasonFor = (f: string) => data.reasons.find((r) => r.factor === f)?.text ?? "";
  const value = (f: string) => risk.components[f.toLowerCase()] ?? 0;
  // The single biggest driver is named in words and carries the only strong bar; the rest stay quiet.
  const worst = FACTORS.reduce((a, b) => (value(b) > value(a) ? b : a));
  const stale = isStale(risk.computed_at);
  const health = healthOf(risk.score, risk.health);
  return (
    <section class="screen detail" aria-label={`${deal.account} detail`}>
      <div class="detail-left">
        <p class="title">{deal.account}</p>
        <p>{dealTitle(deal.account, deal.name)}</p>
        <p class="meta">
          {[deal.amount_display, deal.stage, deal.owner].filter(Boolean).join(" · ")}
        </p>
        {/* Health, as CRM deal scores read: higher is healthier, and it says so beside the number. */}
        <p class="big" aria-label={`Health ${health} out of 100, ${BAND_SPOKEN[risk.band]}`}>
          <span class="score">{health}</span>
          <span class="of">
            /100
            <br />
            health
          </span>
          <BandChip band={risk.band} />
        </p>
        <p class={`meta fresh${stale ? " stale" : ""}`}>
          {capitalize(since(risk.band_since))} · scored{" "}
          {stale ? ago(risk.computed_at) : clock(risk.computed_at)}
        </p>
        <Trend risk={data.trend_14d} stale={stale} />
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
        <p class="eyebrow">WHAT PULLS HEALTH DOWN</p>
        <ul class="factors">
          {FACTORS.map((f) => {
            const v = value(f);
            const top = f === worst && v > 0;
            return (
              <li key={f} class={`factor${top ? " worst" : ""}`}>
                {/* Spelled out: an Echo Show has no hover, so a letter with a tooltip hides its meaning. */}
                <span class="factor-name">
                  {FACTOR_NAME[f]}
                  {top && <span class="driver"> · biggest driver</span>}
                </span>
                <span class="bar" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent(v)} aria-valuetext={dragLevel(v)}
                  aria-label={`${FACTOR_NAME[f]}: ${dragLevel(v).toLowerCase()} pull on health`}>
                  <span style={{ width: `${percent(v)}%` }} />
                </span>
                <span class="value">{dragLevel(v)}</span>
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
                <strong>{FACTOR_NAME[d.factor]}</strong> · {d.claim}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
