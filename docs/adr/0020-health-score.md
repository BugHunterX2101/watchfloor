# ADR-020: People see deal health, not risk

- **Status:** Accepted (2026-10-10)
- **Supersedes in part:** ADR-019 (the "/100 risk" label)
- **Refines:** DESIGN.md §7 (RIGS), §15 (views), PRD voice responses

## Context

Every revenue tool surveyed scores deals so that higher is healthier: Gong, HubSpot, Salesforce
Pipeline Inspection and Clari. Watchfloor spoke and showed its RIGS risk score, where higher is
worse. As a result, "71" meant bad news to Watchfloor and good news to every CRM a manager already
used. ADR-019 labelled the number "risk" everywhere. The product owner chose to switch to a health
score instead.

## Decision

**Health = 100 − RIGS risk.** Health runs from 0 to 100, and higher is healthier. It is the only
number people see or hear.

**What stays on the risk scale.** The RIGS model, bands and band hysteresis, scores in storage,
standing-order conditions (`min_score`) and `LogUpdateOut.score_before/after` are unchanged. The
bands keep their names and thresholds. In health terms, At Risk is health 35 or lower when it enters
(risk 65), and Watch is health 60 or lower (risk 40).

**Tool outputs.**

- **New field.** `BriefingDeal` and `RiskBlock` gain a required `health`.
- **`score` stays.** It is the internal risk, and its description says so.
- **Descriptions.** The `get_risk_briefing` and `explain_deal_risk` descriptions tell the model to
  say health, not score.
- **Standing orders.** The `min_score` field description gives the conversion: health 30 or lower
  is 70.
- **Snapshot.** `schemas/tools.snapshot.json` is regenerated.

**Spoken and written text.** Every place a number reaches a person now uses health:

- **Explain lead:** "Kestrel Biotech is At Risk, health 29."
- **Log update:** "Health moved from 41 to 29, At Risk", or "Health is unchanged at 29".
- **Field update receipt:** "Health is now 29".
- **Slack:** alert, team note and deep-review headers read "(health 29)".
- **Standing orders** read as "health of 30 or lower".
- **Agents.** The triage agent's gate fallback and offline summary use health.
- **Harness prompt.** The model is told to quote `health`, never `score`.

**Views.**

- **Board.** The number on each row is health. The eyebrow states the scale in words ("Deal
  health, 0–100, higher is healthier"), because an Echo Show has no hover.
- **Detail.** Reads "18 /100 health". The 14-day trend plots health (100 minus each risk point),
  with the at-risk zone shaded along the bottom and the watch line at 60.
- **Older servers.** The views fall back to 100 − `score` when talking to a server that predates
  this change.

## Consequences

- **Add-on.** The tool schemas changed, so the Alexa+ add-on must be redeployed (`alexa-ai deploy`,
  DD §22.2) wherever it is registered.
- **Existing standing orders** keep the description they were saved with ("a risk score of 70 or
  higher"). New ones read in health terms.
- **Factor bars stay drag values.** They show how much each RIGS factor pulls health down, so the
  "biggest driver" is still the longest bar.
