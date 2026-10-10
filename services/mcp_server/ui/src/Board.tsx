// Triage board (DD §15.3): ranked risk rows; a tap asks the host for explain_deal_risk.
// The headline is the one number a manager wants from across the room (money at risk), rows are
// numbered in the order the answer speaks them, and a briefing older than an hour says so rather
// than passing for current.

import { BandChip } from "./Band";
import { ago, BAND_LABEL, clock, compactMoney, dealTitle, isStale } from "./format";
import type { Briefing } from "./types";

interface Props {
  data: Briefing;
  open: (dealId: string) => void;
  busy: boolean;
}

function headline(data: Briefing): string {
  const atRisk = data.deals.filter((d) => d.band === "at_risk");
  const cents = atRisk.reduce((sum, d) => sum + (d.amount_cents ?? 0), 0);
  const n = atRisk.length;
  if (!n) return `${data.attention_count} to watch`;
  const deals = `${n} ${n === 1 ? "deal" : "deals"}`;
  return cents > 0 ? `${compactMoney(cents)} at risk · ${deals}` : `${deals} at risk`;
}

export function Board({ data, open, busy }: Props) {
  const ruled = new Set(data.triggered_rules.flatMap((r) => r.deal_ids));
  const rules = data.triggered_rules.length;
  const stale = isStale(data.generated_at);
  const line = [
    `${data.attention_count} ${data.attention_count === 1 ? "deal needs" : "deals need"} attention`,
    data.new_count ? `${data.new_count} new since your last briefing` : null,
    rules ? `${rules} ${rules === 1 ? "rule" : "rules"} fired` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <section class="screen board" aria-label="Pipeline risk">
      <header class="board-head">
        <div>
          <p class="eyebrow">WATCHFLOOR · Pipeline risk</p>
          {!data.all_clear && data.deals.length > 0 && <p class="headline">{headline(data)}</p>}
          <p class="meta">{data.all_clear ? "All clear" : line}</p>
        </div>
        <p class={`meta fresh${stale ? " stale" : ""}`}>
          {stale ? `Last updated ${ago(data.generated_at)}` : `Updated ${clock(data.generated_at)}`}
        </p>
      </header>

      {data.all_clear || data.deals.length === 0 ? (
        <div class="card notice clear">
          <p class="title">Nothing needs you right now.</p>
          <p class="muted">{data.summary}</p>
        </div>
      ) : (
        <ol class="rows">
          {data.deals.map((d, i) => (
            <li key={d.deal_id}>
              <button
                type="button"
                class={`row card ${d.band}`}
                disabled={busy}
                onClick={() => open(d.deal_id)}
                aria-label={`${i + 1}. ${d.account}, ${BAND_LABEL[d.band]} at ${d.score}. Open details.`}
              >
                <span class="rank" aria-hidden="true">
                  {i + 1}
                </span>
                <span class="score">{d.score}</span>
                <BandChip band={d.band} />
                <span class="who">
                  <strong>{d.account}</strong> · {dealTitle(d.account, d.name)}
                </span>
                <span class="amount">{d.amount_display ?? ""}</span>
                <span class="chips">
                  {d.new_since_last_briefing && <span class="chip">NEW</span>}
                  {ruled.has(d.deal_id) && <span class="chip warn">RULE</span>}
                </span>
                <span class="why meta">
                  {d.top_reason} · {d.stage} · {d.owner}
                </span>
              </button>
            </li>
          ))}
        </ol>
      )}

      <p class="meta hint">Try: “Why is {data.deals[0]?.account ?? "Kestrel"} at risk?” · “Dig into it.”</p>
    </section>
  );
}
