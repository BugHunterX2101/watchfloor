// Triage board (DD §15.3): ranked risk rows; a tap asks the host for explain_deal_risk.

import { BAND_LABEL, clock, dealTitle } from "./format";
import type { Briefing } from "./types";

interface Props {
  data: Briefing;
  open: (dealId: string) => void;
  busy: boolean;
}

export function Board({ data, open, busy }: Props) {
  const ruled = new Set(data.triggered_rules.flatMap((r) => r.deal_ids));
  const rules = data.triggered_rules.length;
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
          <p class="meta">{data.all_clear ? "All clear" : line}</p>
        </div>
        <p class="meta">Updated {clock(data.generated_at)}</p>
      </header>

      {data.all_clear || data.deals.length === 0 ? (
        <div class="card notice">
          <p class="title">Nothing needs you right now.</p>
          <p class="muted">{data.summary}</p>
        </div>
      ) : (
        <ol class="rows">
          {data.deals.map((d) => (
            <li key={d.deal_id}>
              <button
                type="button"
                class={`row card ${d.band}`}
                disabled={busy}
                onClick={() => open(d.deal_id)}
                aria-label={`${d.account}, ${BAND_LABEL[d.band]} at ${d.score}. Open details.`}
              >
                <span class="score">{d.score}</span>
                <span class={`band ${d.band}`}>{BAND_LABEL[d.band]}</span>
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
