# Local load baseline

Measured 2026-10-17 at commit `81799b2` on Windows 11, 8 logical CPUs, Python 3.12.10. 15 s per level, closed loop, reads only, database pool 2 connections with a 2 s wait for one.

One uvicorn process on loopback, Postgres in Docker on the same machine, rate limits lifted. These are not AWS numbers and not a promise: they show how the curve bends here, and they move with whatever else the machine is doing. Regenerate with `make load-baseline`.

| Callers | OK | Failed | Calls/s | p50 ms | p95 ms | p99 ms | max ms |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 238 | 0 | 15.8 | 41.1 | 196.3 | 485.3 | 1169.8 |
| 4 | 1561 | 0 | 103.8 | 36.0 | 51.9 | 95.6 | 208.3 |
| 16 | 1717 | 0 | 113.7 | 136.9 | 191.4 | 221.4 | 321.5 |
| 64 | 1031 | 0 | 65.4 | 615.0 | 2965.3 | 4453.7 | 8046.5 |

Failures by kind: none.

Per tool at 64 callers:

| Tool | Calls | p50 ms | p95 ms |
|---|---:|---:|---:|
| `get_risk_briefing` | 207 | 634.2 | 2494.5 |
| `explain_deal_risk` | 206 | 525.4 | 3044.6 |
| `lookup_deal` | 205 | 630.1 | 2878.2 |
| `list_standing_orders` | 209 | 594.6 | 2759.9 |
| `get_triage_result` | 204 | 685.9 | 3237.9 |
