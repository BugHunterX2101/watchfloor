---
name: watchfloor-digest
description: End-of-day RevOps digest of at-risk deals from Watchfloor, read-only
always: false
triggers: watchfloor digest, pipeline digest, revops digest
---
# Watchfloor digest

Write a short end-of-day digest of the sales pipeline from the `watchfloor` MCP server.

1. Call `get_risk_briefing` with `limit: 3`. It returns the deals most at risk, with health scores
   and reasons.
2. For each of them, call `explain_deal_risk` with its `deal_id`.
3. Reply with:
   - one line per deal: name, owner, health, the main reason;
   - a suggested next step for each, worded as a suggestion;
   - for any deal whose owner should hear from someone today, a short follow-up note the reader can
     copy into an e-mail. Watchfloor cannot send e-mail.

Rules:

- Use only what the tools return. Never invent a deal, an amount, a date or a name.
- Do not call `propose_action`. This digest only reads; changes are made by a person in Alexa+ or
  the Watchfloor page, where they confirm them.
- If a tool fails or the server asks you to sign in, say so in one line and stop.
