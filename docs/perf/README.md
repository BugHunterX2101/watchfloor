# Hot-path query plans

`EXPLAIN (ANALYZE, BUFFERS)` of the SQL the code runs (TD §21.1), as `wf_app` under row-level security on
the local seed (136 deals · 1837 activities · 118 scores), commit `079be7d`. Each function ran once to warm the cache, then once explained.
Regenerate with `make perf-plans` after a query or index changes; `--check` fails when `ranked_risks`
takes over 5 ms.

| Query | Statements | Execution time | Plan |
|---|---|---|---|
| `ranked_risks` | 1 | 1.77 ms | [ranked_risks.txt](ranked_risks.txt) |
| `attention_counts` | 1 | 0.65 ms | [attention_counts.txt](attention_counts.txt) |
| `fired_since` | 1 | 0.46 ms | [fired_since.txt](fired_since.txt) |
| `rule_match` | 1 | 0.82 ms | [rule_match.txt](rule_match.txt) |
| `load_snapshots` | 4 | 3.58 ms | [load_snapshots.txt](load_snapshots.txt) |

`ranked_risks`, `fired_since` and `attention_counts` are the briefing (`get_risk_briefing`); `rule_match`
is one standing order in the watcher; `load_snapshots` builds the RIGS inputs for every open deal, the
watcher's 15-minute recompute. The seed is small, so these show plan shapes and index use, not scale.
