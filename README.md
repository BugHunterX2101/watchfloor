<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/brand/watchfloor-lockup-dark.png">
  <img src="docs/brand/watchfloor-lockup-light.png" alt="Watchfloor: pipeline risk watch" width="560">
</picture>

### Ambient pipeline-risk triage for Alexa+, and a voice assistant you can run a sales pipeline with, hands-free.

Ask *"what needs my attention today?"* and hear the deals at risk, why (with evidence from the CRM),
and the next step. Nothing changes until you say yes. Then change your mind, and it keeps up.

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
![MCP](https://img.shields.io/badge/MCP-2025--11--25-4FE0C8)
![Python](https://img.shields.io/badge/python-3.12-3776AB)
![TypeScript](https://img.shields.io/badge/web-TypeScript%20%2B%20Vite-3178C6)
![Postgres](https://img.shields.io/badge/postgres-16%20%2B%20RLS-336791)
![IaC](https://img.shields.io/badge/infra-AWS%20CDK%20(Python)-FF9900)
![Tests](https://img.shields.io/badge/tests-952%20passing-2EA44F)

[Try it](#try-it) · [Architecture](#architecture) · [Repository map](#repository-map) · [Run it locally](#run-it-locally) ·
[Decisions](#decisions-that-shape-it) · [Docs](#documentation)

</div>

---

## Contents

1. [What it is](#what-it-is)
2. [Try it](#try-it)
3. [What you can say](#what-you-can-say)
4. [Architecture](#architecture)
5. [Repository map](#repository-map)
6. [Decisions that shape it](#decisions-that-shape-it)
7. [Run it locally](#run-it-locally)
8. [Testing and quality](#testing-and-quality)
9. [Deploy](#deploy)
10. [Status and honest limits](#status-and-honest-limits)
11. [Documentation](#documentation) · [License](#license)

---

## What it is

A founder or sales leader does not open a CRM to find out which deals are dying. By the time the
numbers say so, the quarter is gone. Watchfloor watches the pipeline for them and tells them out
loud, in a few seconds, which deals need attention, why, and what to do about it.

It is three things that fit together:

| | What | Where |
|---|---|---|
| **1. A remote MCP server** | Protocol **2025-11-25**, stateless Streamable HTTP, OAuth 2.1. Fourteen tools for an assistant to call (briefing, explain, look up, deep review, propose / confirm / undo, standing orders, log an update) and six read-only tools for the triage agents (`explain_deal_risk` is in both sets: 19 distinct tools). | [`services/mcp_server`](services/mcp_server) |
| **2. A triage backend** | Deterministic, explainable risk scoring (RIGS) kept fresh in Postgres, a transactional outbox, standing orders that run in the background, and a team of Strands agents (a Lead and four specialists) for a deep review, behind a deterministic output gate. | [`packages/core`](packages/core) · [`services/workers`](services/workers) · [`services/agents`](services/agents) |
| **3. A simulated Alexa+ and voice companion** | A web harness that signs in with real OAuth and calls the same server over the real protocol. Hands-free, wake word, a conversation that stays open, spoken confirmations, 31 page actions, and a companion layer that lets you change your mind mid-sentence and learns the names it mishears. | [`services/harness`](services/harness) |

**Path B.** The Alexa+ add-on toolkit was closed to our AWS account
([friction log entry 4](docs/friction-log.md)), so this entry is a *simulated* Alexa+ experience, as
the hackathon rules allow. The harness is a real MCP client: real OAuth (PKCE), the deployed server,
the real protocol, the real tools. Only the Alexa speech layer is simulated. The add-on side
(service token, listing, account linking) is built and ready if access opens
([ADR-024](docs/adr/0024-alexa-service-token.md)).

### Design principles

- **The voice path never waits on a model.** Risk is scored ahead of time by RIGS, so every read tool
  answers from Postgres in well under the 500 ms Alexa+ budget (p95 ≤ 150 ms target) — [ADR-002](docs/adr/0002-deterministic-hot-path.md), [ADR-010](docs/adr/0010-rigs-heuristic.md).
- **Every number can be explained.** A score is four named factors and a list of reasons, each pointing
  at the activity rows that caused it. Nothing is a black box.
- **Writes take two steps.** `propose_action` returns a preview and a single-use token; `confirm_action`
  executes it. A spoken "yes" is never inferred from the same turn that asked — [ADR-003](docs/adr/0003-two-tool-confirmation.md).
- **Models propose, code decides.** Agent output passes a deterministic gate (evidence, people, actions,
  wording) before it can leave the container — [ADR-012](docs/adr/0012-lead-draft-and-output-gate.md).
- **The model words the answer; it never supplies the facts.** Totals and the week-over-week comparison
  are computed on the server. Every number above one, and every month, weekday and relative date, in
  a spoken reply is checked in code against that turn's tool results and the user's own words. A
  guess is sent back once, then cut, and the assistant says it couldn't check rather than make one
  up. What a "yes" saves is
  read back from the held arguments, not the model's paraphrase — [ADR-031](docs/adr/0031-learning-from-corrections.md).
- **Failure is visible and recoverable.** Side effects go through a transactional outbox with backoff
  and a dead state; deep reviews time out cleanly; every change can be undone for 30 seconds — [ADR-008](docs/adr/0008-transactional-outbox.md), [ADR-021](docs/adr/0021-undo-and-more-time.md).
- **Trust the data, not the text.** CRM free text is stored as data and shown to models inside
  `<crm_data>` tags that the prompt says are never instructions; the repo ships hostile notes in its
  seed to test it.

---

## Try it

**Hosted:** <https://watchfloor.onrender.com/>. While AWS blocks all compute on the account (Lambda
from 2026-10-16, then Fargate; EC2, App Runner, AgentCore and Lightsail are refused too,
[friction log entry 11](docs/friction-log.md)), the whole stack runs as one service on Render's free plan
([deploy/render](deploy/render/README.md)): the same auth server, MCP server, harness and workers, on one
origin. A free instance sleeps after 15 minutes idle, so the first request can take about a minute.

1. Open <https://watchfloor.onrender.com/> and select **Sign in**. That runs the real OAuth flow (PKCE)
   against Watchfloor's auth server.
2. Sign in as `priya@lumenridge.example`. The password is in the Devpost testing instructions; it is
   never in this repository.

**Or run it on your machine** (no AWS and no model needed). The commands are under
[Run it locally](#run-it-locally); then open <http://localhost:8082>, select **Sign in**, and sign in
as `priya@lumenridge.example` with the `WF_DEMO_PASSWORD` you chose.

The AWS `dev` environment (<https://rgroug1dvd.execute-api.us-east-1.amazonaws.com/>) answers with errors
until AWS Support lifts the block; that is the account, not a fault in the app.

Either way, ask in your own words ([What you can say](#what-you-can-say)), or turn on
**Hands-free** and just talk. Every tool call, with its latency, is listed beside the conversation.
The data is a synthetic sales team (Lumenridge) and its deals, so nothing you do reaches a real
customer.
[Privacy](https://watchfloor.onrender.com/privacy) ·
[Terms](https://watchfloor.onrender.com/terms)

> Demo video: _the link is added here once the recording is published._

---

## What you can say

| You say | What happens |
|---|---|
| "What needs my attention today?" | `get_risk_briefing`: the ranked at-risk deals, and any standing orders that fired since last time |
| "Why is Kestrel at risk?" | `lookup_deal` → `explain_deal_risk`: health, the four factors, the top reasons with evidence, a 14-day trend |
| "How much is at risk?" / "Better or worse than last week?" | Totals over every open deal and a comparison with the snapshot seven days back, both computed on the server and said as given |
| "How is castle bio doing?" … "No, I said Kestrel" | The correction is learned (names only, never numbers), so the same mishearing is fixed next time; listed and removable in Settings |
| "Dig into Kestrel" … "Is the review finished?" | `start_deep_triage` → a Lead and four specialists work for ~20 s; the page reads the result out when it lands |
| "Post a heads-up about Kestrel to the team" … "yes" | `propose_action` (preview) → `confirm_action`; "undo" takes it back for 30 s |
| "Make that Friday" / "no, Harbor and Pine" / "never mind" | The pending change is replaced, redirected or dropped: **what you said last wins** |
| "Tell me when a deal over fifty thousand goes quiet for five days" | A standing order, read back in your own numbers and saved only after your yes |
| "Open the pipeline, show only at-risk, sort by amount" | Three page actions in one breath, run as separate commands |
| "Open the second one", "pin it", "dark mode", "speak slower" | Resolved against what is on screen; most run in the browser with **no model call** |
| "Keep it short" / "call me Sam" / "don't interrupt me with updates" | Reply length, your name, spoken alerts: applied and confirmed |
| "That's all" / "Watchfloor, stop listening" | Ends the conversation / switches hands-free off |

In hands-free mode it also **watches the board for you** and speaks up when a deal slips, a standing
order fires, or a new deal turns at-risk. It never says the same news about the same deal twice
within ten minutes, so it does not nag.

If the language model is unavailable, a keyword router still answers the briefing, explain, review
and standing-order questions, says so, and the board's buttons still act. Free-form requests to act
need a model.

---

## Architecture

Every diagram below is rendered by GitHub, and each was checked against the code it describes.

### 1. System context

Who talks to what. The harness is the Path B stand-in for Alexa+; swapping it for the real thing
changes the box on the left, not the server.

```mermaid
flowchart LR
  subgraph People
    PR["Priya, founder<br/>speaks to Alexa+"]
    TM["Team<br/>reads Slack"]
    JU["Judges<br/>repo · video · harness"]
  end
  subgraph Client["Experience"]
    AX["Alexa+<br/>(add-on, if toolkit access opens)"]
    HS["Simulated Alexa+<br/>web harness (Path B)"]
  end
  WF["Watchfloor<br/>MCP server · auth server · workers · triage agents"]
  subgraph Models["Language models"]
    BR["Amazon Bedrock<br/>Nova Lite / Pro"]
    GM["Gemini<br/>via Strands"]
  end
  SL["Slack"]

  PR -- "voice" --> AX
  PR -- "voice or text" --> HS
  JU --> HS
  AX -. "MCP 2025-11-25 · OAuth 2.1" .-> WF
  HS -- "MCP 2025-11-25 · OAuth 2.1 + PKCE" --> WF
  HS -- "reasoning" --> BR
  HS -. "when Bedrock cannot run" .-> GM
  WF -- "deep-triage agents" --> BR
  WF -- "Block Kit alerts, plans, drafts" --> SL
  SL --> TM
```

### 2. Logical layers

Five layers, each only calling downward. The agents are the one thing that re-enters from the side,
and they do it through the same protocol layer as everyone else, with a job-scoped token.

```mermaid
flowchart TB
  subgraph L1["Experience"]
    V["Alexa+ voice"]
    HW["Harness web app<br/>5 pages · night scope · voice"]
    UIV["MCP Apps views<br/>triage board · deal detail · confirm sheet"]
    SK["Slack messages"]
  end
  subgraph L2["Protocol and edge"]
    EG["EdgeGuard<br/>Host · Origin · Accept · body ≤ 64 KiB"]
    AU["Auth · ES256 JWT · audience = resource URI"]
    VR["Version check"]
    RL["Rate limit · per user and workspace"]
    SM["Stateless Streamable HTTP · JSON"]
  end
  subgraph L3["Application"]
    REG["Tool registry · scope-filtered"]
    TL["14 user tools · 6 agent tools"]
    RS["3 ui:// resources"]
  end
  subgraph L4["Domain · watchfloor_core"]
    RIGS["RIGS engine · pure functions"]
    SVC["Services<br/>read · actions · rules · triage · updates"]
    TOK["Confirmation tokens · JCS hash"]
    SPK["Speech formatting"]
  end
  subgraph L5["Infrastructure"]
    DB[("Postgres 16 · RLS FORCE")]
    Q["SQS · transactional outbox"]
    WK["Workers<br/>dispatcher · watcher · sweeper · canary"]
    AG["Triage agents"]
  end
  L1 --> L2 --> L3 --> L4 --> L5
  WK --> AG
  AG -- "MCP · agent scope" --> L2
```

### 3. One request through the edge

The order of checks is deliberate: cheap and anonymous first, identity next, rate limiting only for
callers we have already authenticated.

```mermaid
flowchart TD
  S(["POST /mcp"]) --> H{"Exactly one Host,<br/>well-formed and allowed?"}
  H -- no --> R421["421 Misdirected Request"]
  H -- yes --> O{"Origin present and not allowed,<br/>or sent twice?"}
  O -- yes --> R403["403 Forbidden"]
  O -- no --> AC{"Accept lists application/json<br/>and text/event-stream?"}
  AC -- no --> R406["406 Not Acceptable"]
  AC -- yes --> SZ{"Body ≤ 64 KiB and valid JSON?"}
  SZ -- "too big" --> R413["413"]
  SZ -- "bad JSON" --> R400a["400 parse error"]
  SZ -- ok --> T{"Valid Bearer JWT?<br/>iss · aud · exp · signature"}
  T -- no --> R401["401"]
  T -- yes --> V{"initialize, or<br/>MCP-Protocol-Version = 2025-11-25?"}
  V -- no --> R400b["400 Bad Request"]
  V -- yes --> RL{"Within rate limit?"}
  RL -- no --> R429["429 + Retry-After"]
  RL -- yes --> N{"JSON-RPC notification<br/>or response?"}
  N -- yes --> R202["202 Accepted"]
  N -- no --> OK["200 application/json<br/>JSON-RPC result or error"]
```

### 4. The hot path: a morning briefing

No model, no queue, no network hop beyond Postgres. This is why the answer fits in the voice budget.

```mermaid
sequenceDiagram
  autonumber
  actor P as Priya
  participant AX as Alexa+ / harness
  participant M as MCP server
  participant DB as Postgres
  P->>AX: "What needs my attention today?"
  AX->>M: tools/call get_risk_briefing
  M->>DB: set workspace · read prefs and last_briefing_at
  M->>DB: ranked risks (risk_scores ⋈ deals ⋈ accounts ⋈ users)
  M->>DB: standing orders that fired since last briefing
  M->>DB: touch last_briefing_at · write audit_log
  M-->>AX: structuredContent (summary, deals, triggered rules) + ui://triage-board
  AX-->>P: spoken briefing, board on screen
```

### 5. Acting safely: propose, confirm, undo

Two tools, a single-use token bound to the exact parameters, and a window to take it back. The Slack
post is held a few seconds longer than the undo window so an undo can still withdraw it.

```mermaid
sequenceDiagram
  autonumber
  actor P as Priya
  participant AX as Alexa+ / harness
  participant M as MCP server
  participant DB as Postgres
  participant D as dispatcher
  participant S as Slack
  P->>AX: "Have Maya book their CFO by Thursday"
  AX->>M: propose_action (create_task, assignee, due date)
  M->>DB: resolve assignee · insert action_proposals (pending, token_hash, params_hash)
  M-->>AX: preview + confirmation_token (5 min)
  AX-->>P: "Create a task for Maya… Should I go ahead?"
  P->>AX: "Yes"
  AX->>M: confirm_action (token)
  M->>DB: claim token · insert task · insert outbox · insert audit (one transaction)
  M-->>AX: executed + audit id + undo window
  opt within 30 seconds
    P->>AX: "Undo that"
    AX->>M: undo_action (token)
    M->>DB: reverse the write · cancel the unsent outbox row
  end
  D->>S: after the hold, "Task for Maya: book CFO call"
```

### 6. A deep review, end to end

The warm path. The agents never touch the database: they read through the same MCP server with a
five-minute token scoped to one job.

```mermaid
sequenceDiagram
  autonumber
  actor P as Priya
  participant AX as Alexa+ / harness
  participant M as MCP server
  participant DB as Postgres
  participant Q as SQS jobs
  participant D as dispatcher
  participant AS as Auth server
  participant R as Triage agents
  participant S as Slack
  P->>AX: "Dig into Kestrel"
  AX->>M: start_deep_triage (deal_id)
  M->>DB: lock deal · dedupe · insert triage_jobs (queued)
  M->>Q: send job
  M-->>AX: job id, ETA about 20 s
  AX-->>P: "Review started. I'll tell you when it's ready."
  Q->>D: deliver
  D->>AS: client_credentials, job token (5 min)
  D->>R: run (deal_id, mcp token)
  R->>M: read timeline, history, account, playbook (agent scope)
  R-->>D: TriageOutput JSON
  D->>D: schema gate
  D->>DB: complete job · insert outbox (Slack plan)
  D->>S: Block Kit plan
  AX->>M: get_triage_result (page polls every 4 s)
  M-->>AX: voice summary, drivers, actions
  AX-->>P: reads the result out by itself
```

### 7. Standing orders and the watcher

The cold path. A rule is data in Postgres; the watcher recomputes risk and evaluates rules every
fifteen minutes; alerts reach Slack through the outbox and reach you in the next briefing.

```mermaid
sequenceDiagram
  autonumber
  actor P as Priya
  participant AX as Alexa+ / harness
  participant M as MCP server
  participant DB as Postgres
  participant W as watcher
  participant Q as SQS outbox
  participant D as dispatcher
  participant S as Slack
  P->>AX: "Flag deals over fifty thousand that go quiet for five days"
  AX->>M: set_standing_order (min amount, quiet days)
  M-->>AX: held: read back in your numbers, ask
  P->>AX: "Yes"
  AX->>M: set_standing_order (same arguments)
  M->>DB: insert standing_orders (deterministic description)
  loop every 15 minutes
    W->>DB: recompute RIGS with hysteresis · upsert risk_scores
    W->>DB: evaluate rules · insert alerts · insert outbox (dedupe key)
    W->>Q: publish per outbox row
  end
  Q->>D: deliver
  D->>S: "At Risk: Harbor and Pine (66). Standing order fired."
  Note over P,AX: Next briefing leads with the fired rules
```

### 8. Sign-in: OAuth 2.1 with PKCE

Self-hosted, minimal, standards-shaped ([ADR-006](docs/adr/0006-self-hosted-oauth.md)). The
harness is a public client; its tokens live in an encrypted, HTTP-only cookie, never in page script.

```mermaid
sequenceDiagram
  autonumber
  participant B as Browser
  participant H as Harness API
  participant AS as Auth server
  participant K as Signing key (ES256)
  participant M as MCP server
  B->>H: GET /oauth/login
  H-->>B: 302 to /authorize (code_challenge S256, resource = MCP URI)
  B->>AS: login + consent
  AS-->>B: 302 to /oauth/callback?code=…&state=…
  B->>H: GET /oauth/callback
  H->>AS: POST /token (code, code_verifier, resource)
  AS->>K: sign access token (60 min)
  AS-->>H: access token + rotating refresh token
  H-->>B: sealed session cookie (SameSite=Lax, HttpOnly)
  B->>H: POST /converse
  H->>M: tools/call with the user's Bearer token
  M->>M: verify with cached JWKS
```

### 9. Risk scoring: RIGS

Four factors, each 0 to 1, from a snapshot of a deal. The weights, stage norms and band thresholds
live in [`rigs.yaml`](packages/core/watchfloor_core/config/rigs.yaml); a change bumps the model version
and the watcher re-stamps every score.

```mermaid
flowchart LR
  SNAP["Deal snapshot<br/>stage · amount · buyer · next step<br/>14-day engagement · 90-day history"] --> R
  SNAP --> I
  SNAP --> G
  SNAP --> S
  R["R · Recency (shown as Responsiveness)<br/>days since a two-way touch vs the stage norm"] --> SUM
  I["I · Intent<br/>14-day engagement points vs the stage baseline"] --> SUM
  G["G · Gaps (shown as Governance)<br/>0.25 per missing qualification item"] --> SUM
  S["S · Slippage (shown as Schedule)<br/>pushes, stage regression, amount cuts"] --> SUM
  SUM["Weighted sum<br/>0.30 R + 0.25 I + 0.25 G + 0.20 S<br/>score 0 to 100"] --> BAND
  BAND{"Band with hysteresis<br/>at risk: enter 65, leave below 60<br/>watch: enter 40, leave below 36"}
  BAND --> OUT["Health = 100 − score<br/>reasons with evidence ids<br/>model version rigs-1.1"]
```

### 10. The triage agent team

Agents are *tools of the Lead*, not a free-running swarm ([ADR-004](docs/adr/0004-agents-as-tools.md)),
and they read only through MCP ([ADR-007](docs/adr/0007-agents-read-via-mcp.md)). The gate is plain
code and cannot be talked round.

```mermaid
flowchart TB
  D["dispatcher"] -->|"job token"| APP["Agent app"]
  APP --> LEAD["Triage Lead<br/>≤ 8 steps · temperature 0"]
  LEAD -->|tool| AN["Signal Analyst"]
  LEAD -->|tool| CX["Account Context"]
  LEAD -->|tool| ST["Playbook Strategist"]
  LEAD -->|tool| CR["Critic<br/>one revision"]
  AN -->|"MCP · agent scope"| T1["get_deal_timeline<br/>get_deal_history<br/>explain_deal_risk"]
  CX -->|"MCP · agent scope"| T2["get_account_context<br/>get_prior_triage"]
  ST -->|"MCP · agent scope"| T3["get_playbook"]
  LEAD --> DRAFT["LeadDraft"]
  DRAFT --> GATE{"Deterministic output gate"}
  GATE --> RULE1["Keep only evidence ids this job read<br/>drop drivers with none"]
  GATE --> RULE2["Drop actions with an unknown play,<br/>a link, discount wording, an unseen person,<br/>or params that fail propose_action"]
  GATE --> RULE3["Strip links, ids, e-mails, phone numbers<br/>trim to 45 words / 320 characters"]
  RULE1 --> OUT["TriageOutput"]
  RULE2 --> OUT
  RULE3 --> OUT
  OUT --> D2["dispatcher validates the schema again"]
```

### 11. One voice turn in the harness

The browser keeps the fast, safe things local (exact commands) and sends the rest to the harness API
with a small, checked description of the page and the person. The model's page requests come back as
data and are validated again before they run.

```mermaid
flowchart TB
  MIC["Microphone<br/>browser speech recognition"] --> HF["Hands-free state machine<br/>wake word · conversation window"]
  HF --> SPLIT["Split compound requests"]
  SPLIT --> GR{"Exactly a spoken command?<br/>grammar.ts"}
  GR -- yes --> ACT["parseAction → run on the page<br/>no network, no model"]
  GR -- "no, or a yes/no/amend" --> POST["POST /converse<br/>message · history · page context<br/>profile · unheard flag"]
  POST --> API["Harness API<br/>rate limit · sealed cookie"]
  API --> PEND["Decode the pending change<br/>preview / confirmed / ask"]
  PEND --> CHAIN{"Model chain"}
  CHAIN -- "Bedrock" --> TURN
  CHAIN -- "else Gemini" --> TURN
  CHAIN -- "else keyword router" --> OFF["Offline router<br/>says it is the quick version"]
  TURN["Strands turn<br/>MCP tools + control_app"] --> MCP["MCP server<br/>user's own token"]
  TURN --> SINK["control_app sink<br/>≤ 4 actions · validated"]
  MCP --> RES["reply · calls · ui · awaiting"]
  SINK --> RES
  OFF --> RES
  RES --> BACK["Browser<br/>parseAction again · speakable() · speak"]
  BACK --> HF
```

### 12. Hands-free is a conversation

States of the microphone ([ADR-027](docs/adr/0027-voice-only-control.md),
[ADR-028](docs/adr/0028-hands-free-conversation.md)). Nothing is acted on until the wake word;
after a reply the window stays open, longer when the reply ended on a question.

```mermaid
stateDiagram-v2
  [*] --> off
  off --> waiting: hands-free on
  waiting --> awake: wake word "Watchfloor"
  awake --> busy: request sent
  busy --> busy: speech while working is queued, not dropped
  busy --> speaking: reply arrives
  speaking --> awake: reply ends, window opens (45 s, or 90 s after a question)
  speaking --> awake: talked over by the wake word or a clear request
  awake --> waiting: window lapses, or "that's all"
  awake --> off: "stop listening", 5 min idle, or goodbye
  waiting --> off: microphone blocked or not allowed
  note right of busy
    "Still working on that" at 6 s
    "Taking a little longer" at 14 s
  end note
```

### 13. A pending change

How a change moves from "asked" to "done", and every way out of it. The state rides in the sealed
cookie, so the server stays stateless between turns ([ADR-029](docs/adr/0029-voice-companion.md)).

```mermaid
stateDiagram-v2
  [*] --> none
  none --> preview: propose_action
  none --> ask: set_standing_order / remove_standing_order / update_briefing_preferences held for a yes
  preview --> preview: amended with the same tool and corrected details, new preview
  ask --> ask: amended, held again with the new arguments
  preview --> confirmed: "yes" in a LATER turn
  ask --> saved: "yes" in a later turn, same arguments only
  preview --> none: "no", "never mind", dismissed, or expired
  ask --> none: "no", "never mind", or dismissed
  preview --> preview: "more time" extends, never past 15 min
  confirmed --> none: undo window (30 s) ends
  confirmed --> undone: "undo that"
  saved --> none
  undone --> none
  note right of preview
    Speech said before the previous reply
    was heard (the unheard flag) can never
    answer it
  end note
```

### 14. What the assistant is told each turn

The companion layer. All of it is *context, never commands*: fixed words and flattened text, so a
deal note cannot instruct the model.

```mermaid
flowchart LR
  subgraph Browser
    SCR["Screen<br/>page · theme · hands-free · open dialog"]
    BRD["Board<br/>deal order, the open deal"]
    PRO["Profile<br/>name · time of day · reply length · pinned deals"]
    UNH["Unheard flag"]
  end
  subgraph Server
    PND["Pending change note<br/>(summary flattened into one quoted line)"]
    SYS["System prompt<br/>alexa_style.md"]
  end
  SCR --> CTX["Per-turn context"]
  BRD --> CTX
  PRO --> CTX
  UNH --> CTX
  PND --> CTX
  SYS --> LLM["Model"]
  CTX --> LLM
  LLM --> TOOLS["Tools<br/>MCP + control_app"]
  TOOLS --> GUARD{"Code-level guards"}
  GUARD --> G1["Writes held until a later-turn yes"]
  GUARD --> G2["Sign-out, forget data, clear activity<br/>ask in the page first"]
  GUARD --> G3["Only allow-listed view tools<br/>from the page"]
  GUARD --> G4["Every number and date spoken<br/>checked against this turn's tools"]
  GUARD --> G5["A held write only with<br/>numbers the user said"]
```

### 15. Watching the board

In hands-free the page polls the same allow-listed briefing call and compares it with the last look,
so you hear about a change, not about the board.

```mermaid
flowchart TD
  T["Every 60 s while hands-free is on"] --> OK{"Tab visible, hands-free on,<br/>and spoken alerts on?"}
  OK -- no --> T
  OK -- yes --> G["get_risk_briefing via /api/tool"]
  G --> CMP["watcher.ts compares with the last look"]
  CMP --> E1["A deal slipped to at risk or watch,<br/>or fell 10 points or more"]
  CMP --> E2["A standing order just triggered"]
  CMP --> E3["A new deal arrived at risk"]
  CMP --> E4["A deal is healthy again"]
  E1 --> COOL{"Same news about the same deal<br/>said in the last 10 minutes?"}
  E2 --> COOL
  E3 --> COOL
  E4 --> COOL
  COOL -- yes --> T
  COOL -- no --> SAY["Say it once the assistant is free:<br/>at most 3 items, the rest counted"]
  SAY --> T
```

### 16. Data model

Eighteen tables in `public` (plus the migrator's own ledger) and five in `auth`. Sixteen hold tenant
data, each scoped by workspace with row-level security forced on, under separate `wf_app`,
`wf_auth` and `wf_readonly` roles that cannot bypass it ([TECHNICAL.md §9.2](docs/TECHNICAL.md); rules
and preferences live here too, [ADR-005](docs/adr/0005-postgres-rules-and-prefs.md)). The app role is also cut back to what it needs:
it cannot rewrite `workspaces` or `schema_migrations`, and the audit log is append-only, with deletes
allowed only past the 7-day retention policy (migration `0006_least_privilege.sql`). The compact view:

```mermaid
erDiagram
  WORKSPACES ||--o{ USERS : has
  USERS ||--|| USER_PREFS : has
  WORKSPACES ||--o{ ACCOUNTS : has
  ACCOUNTS ||--o{ CONTACTS : has
  ACCOUNTS ||--o{ DEALS : has
  DEALS ||--o{ ACTIVITIES : logs
  DEALS ||--o{ DEAL_HISTORY : changes
  DEALS ||--|| RISK_SCORES : "current score"
  DEALS ||--o{ RISK_SCORE_DAILY : trend
  DEALS ||--o{ TASKS : has
  USERS ||--o{ STANDING_ORDERS : sets
  STANDING_ORDERS ||--o{ ALERTS : fires
  USERS ||--o{ ACTION_PROPOSALS : proposes
  DEALS ||--o{ TRIAGE_JOBS : "reviewed by"
  WORKSPACES ||--o{ OUTBOX : queues
  WORKSPACES ||--o{ AUDIT_LOG : records
  AUTH_CLIENTS ||--o{ AUTH_CODES : issues
  AUTH_CREDENTIALS ||--o{ AUTH_REFRESH_TOKENS : rotates
```

### 17. Reliability: the outbox and the job lifecycle

Nothing external happens inside a request. The row commits with the business write; delivery is
at-least-once and idempotent; the sweeper republishes what a lost message left behind.

```mermaid
stateDiagram-v2
  direction LR
  state "Outbox row" as OB {
    [*] --> pending: committed with the write
    pending --> sent: delivered
    pending --> pending: failed, backoff (5 attempts)
    pending --> dead: attempts used up, or the worker died each time
    pending --> cancelled: undone before delivery
    dead --> pending: operator re-arms it
  }
  state "Triage job" as JB {
    [*] --> queued
    queued --> running: dispatcher claims it
    running --> done: result passes the gate
    running --> failed: agent or schema failure
    queued --> timed_out: sweeper, 180 s with no result
    running --> timed_out: sweeper, 180 s with no result
  }
```

### 18. Security zones and trust boundaries

Untrusted text (CRM notes, anything a person types) is data at every step. Model output is untrusted
until the gate has passed it. The agent zone has no database access at all.

```mermaid
flowchart LR
  subgraph Z0["Zone 0 · untrusted"]
    A1["Assistant requests"]
    A2["Browsers"]
    A3["CRM free text"]
  end
  subgraph Z1["Zone 1 · validated edge"]
    E1["EdgeGuard 421 / 403 / 406 / 413"]
    E2["Auth 401"]
    E3["Version 400 · Rate 429"]
  end
  subgraph Z2["Zone 2 · trusted core"]
    C1["Tool handlers and services"]
    C2[("Postgres · RLS forced")]
    C3["KMS · Secrets Manager"]
  end
  subgraph Z3["Zone 3 · model zone (output untrusted)"]
    M1["Triage agents<br/>read-only agent scope"]
    M2["Language models"]
  end
  A1 --> E1 --> E2 --> E3 --> C1 --> C2
  A2 --> E1
  A3 -. "stored as data, shown inside crm_data tags" .-> C2
  C1 -- "job-scoped token" --> M1
  M1 --> M2
  M1 -- "TriageOutput · schema + gate" --> C1
  C1 --> C3
```

### 19. Where it runs on AWS

The shipped configs use `compute: ecs` (Fargate), because the account refused Lambda
([ADR-025](docs/adr/0025-ecs-when-lambda-is-refused.md)). The same code also packages as Lambda
functions (`compute: lambda`, the original layout, still tested). No load balancer, no NAT gateway,
no VPC interface endpoints: the credits are the budget ([ADR-009](docs/adr/0009-nat-instance.md), [ADR-014](docs/adr/0014-free-plan-instance-types.md), [ADR-022](docs/adr/0022-nova-on-the-free-plan.md)).

```mermaid
flowchart TB
  subgraph NET["Internet"]
    BRW["Browser"]
    ALX["Alexa+ bridge"]
    SLK["Slack webhook"]
  end
  subgraph AWS["AWS account · us-east-1"]
    subgraph EDGE["Public HTTP APIs"]
      APIH["Harness API"]
      APIM["MCP API"]
      APIA["Auth API"]
    end
    subgraph VPC["VPC · 2 AZs"]
      VL["VPC link + Cloud Map"]
      subgraph SVC["ECS Fargate · arm64 · 0.25 vCPU each"]
        HS["harness-api<br/>serves the web app"]
        MS["mcp-server"]
        AU["auth-server"]
        TR["triage<br/>agents"]
        WK["workers<br/>dispatcher · watcher · sweeper · canary"]
      end
      RDS[("RDS PostgreSQL 16<br/>db.t4g.micro · encrypted · TLS only")]
      NAT["fck-nat · t4g.micro"]
    end
    subgraph MANAGED["Managed services"]
      SQ["SQS jobs + outbox<br/>each with a DLQ"]
      KMS["KMS · ES256 signing key"]
      SEC["Secrets Manager"]
      BED["Bedrock · Nova"]
      CW["CloudWatch · alarms · dashboard"]
      BUD["Budgets → SNS e-mail"]
    end
  end
  BRW --> APIH --> VL
  ALX --> APIM --> VL
  BRW --> APIA --> VL
  VL --> HS
  VL --> MS
  VL --> AU
  HS -- "MCP + user token" --> MS
  MS --> RDS
  AU --> RDS
  AU --> KMS
  MS --> SQ
  SQ --> WK
  WK --> RDS
  WK --> TR
  TR -- "MCP · agent scope" --> MS
  TR --> BED
  HS --> BED
  WK --> NAT --> SLK
  SVC -.-> CW
  SVC -.-> SEC
```

### 20. Infrastructure as code

Eight CDK stacks per environment, of which seven deploy today: `Agents` (AgentCore) is created only
when AgentCore is in use, and the account refuses it
([ADR-023](docs/adr/0023-gemini-when-bedrock-cannot-run.md)), so deep reviews run beside the workers
instead. When it is on, `Agents` is built before `Async` because the dispatcher needs the runtime's
address; `Mcp` reaches `Auth` and the queues through fixed URLs, so a send before the queues exist
only logs and the sweeper republishes the outbox row.

```mermaid
flowchart LR
  NET["net<br/>VPC · NAT · S3 endpoint"] --> DATA["data<br/>RDS · secrets · KMS"]
  NET --> AUTH["auth<br/>auth server · signing key"]
  DATA --> AUTH
  NET --> MCP["mcp<br/>MCP server · HTTP API"]
  DATA --> MCP
  AGENTS["agents<br/>(only when enabled)"] --> ASYNC
  NET --> ASYNC["async<br/>queues · workers · schedules"]
  DATA --> ASYNC
  AUTH --> ASYNC
  NET --> HARNESS["harness<br/>harness API · web app"]
  AUTH --> OPS["ops<br/>alarms · dashboard · budget"]
  MCP --> OPS
  ASYNC --> OPS
```

### 21. From a commit to an environment

Pushes to `main` deploy `dev`; tags deploy `demo`. While AWS has compute blocked, pushes are marked
`[skip ci]` and verified, so no failed deploy runs against the account.

```mermaid
flowchart LR
  DEV["Branch"] --> PR["Pull request"]
  PR --> CI{"ci.yml<br/>ruff · lockfile · tool-schema snapshot<br/>unit · conformance · integration (Postgres)<br/>agents · harness (api + web) · views build · cdk synth"}
  CI -- fail --> DEV
  CI -- pass --> MAIN["main"]
  MAIN --> DD{"deploy-dev.yml<br/>checks · package · cdk deploy dev<br/>migrate · seed · remote conformance"}
  DD -- pass --> TAG["git tag vX.Y.Z"]
  TAG --> RD{"release-demo.yml<br/>cdk deploy demo · migrate<br/>evals · conformance · latency probe"}
  RD --> FREEZE["Demo frozen"]
```

> The decision that put us on Path B, and what would flip it, is diagrammed in
> [TECHNICAL.md §4.17](docs/TECHNICAL.md); the spec-level set of 17 views is in §4.

---

## Repository map

```text
watchfloor/
├── README.md                    # you are here
├── LICENSE                      # Apache-2.0
├── Makefile                     # bootstrap · dev · test · deploy · demo operations
├── Procfile.dev                 # the five local processes (honcho)
├── docker-compose.yml           # Postgres 16 on 127.0.0.1:55432
├── pyproject.toml               # uv workspace root, ruff, pytest
├── uv.lock
├── .env.example                 # local placeholders only
├── .github/
│   ├── dependabot.yml           # weekly update PRs (uv ×4, npm ×2, actions)
│   └── workflows/
│       ├── ci.yml               # lint · tests · builds · synth
│       ├── deploy-dev.yml       # main → dev
│       ├── release-demo.yml     # v*.*.* → demo
│       └── security.yml         # advisories + Trivy: dependency PRs, weekly, on demand; never on a push
├── packages/
│   └── core/                    # the shared domain (no web framework in here)
│       ├── pyproject.toml
│       └── watchfloor_core/
│           ├── rigs.py · rigs_params.py       # RIGS: pure scoring functions and parameters
│           ├── tokens.py · principal.py       # confirmation tokens · who is calling, with what scopes
│           ├── speech.py · envelope.py        # speakable text · result envelopes
│           ├── db.py · queue.py · clock.py    # pooled Postgres with RLS · SQS · injectable time
│           ├── bedrock.py · gemini.py         # model clients
│           ├── settings.py · errors.py · ids.py · usage.py
│           ├── config/
│           │   ├── rigs.yaml                  # weights · bands · stage norms
│           │   └── playbook.yaml              # the plays the Strategist may suggest
│           ├── models/                        # domain · schema · tools · triage (Pydantic)
│           ├── repos/                         # SQL: deals · scores · rules · proposals · outbox · jobs · prefs · users …
│           └── services/                      # read · actions · rules · triage · updates · scoring · agent_read · slack_blocks
├── services/
│   ├── mcp_server/              # the product: a stateless MCP server
│   │   ├── mcp_server/
│   │   │   ├── app.py · server.py · session.py · context.py · resources.py · wellknown.py
│   │   │   ├── middleware/                    # edge_guard · auth · version · ratelimit · telemetry
│   │   │   └── tools/                         # briefing · explain · lookup · triage · actions · updates · rules · prefs · agent_read · descriptions · snapshot
│   │   ├── schemas/tools.snapshot.json        # the pinned tool schemas (CI fails if they drift)
│   │   └── ui/                                # MCP Apps views: Board · Detail · Confirm (Preact, single-file HTML)
│   ├── auth_server/             # OAuth 2.1 authorization server (PKCE S256, RFC 8414 / 9728)
│   │   └── auth_server/         # app · authorize · token · revoke · jwks · signer · clients · storage · seeder · tickets · legal · metadata · templates · static (add-on assets)
│   ├── workers/                 # everything that runs without a request
│   │   └── workers/             # dispatcher · watcher · sweeper · canary · outbox_publisher · job_tokens · drafts · slack · migrator · service · runtime · agentcore_client · memory_events
│   ├── agents/                  # deep triage; its own uv project (ADR-011)
│   │   ├── Dockerfile
│   │   ├── agents/              # lead · runner · gate · data_tools · memory · offline · models · main · handler
│   │   │   ├── specialists/     # analyst · context · strategist · critic
│   │   │   └── prompts/         # one markdown prompt per agent
│   │   └── tests/
│   └── harness/                 # simulated Alexa+ (Path B)
│       ├── api/                 # Starlette API; its own uv project
│       │   ├── harness_api/
│       │   │   ├── app.py                     # routes: /oauth/* · /converse · /api/tool · /api/dismiss · /api/view · /api/session · /api/logout · /healthz
│       │   │   ├── agent.py                   # a turn: pending change · model chain · tools · one second look at an unsupported number
│       │   │   ├── control.py                 # control_app: the 31 page actions, validated · exact read-backs · grounding of numbers and dates
│       │   │   ├── offline.py                 # the keyword router when no model can answer
│       │   │   ├── mcp_http.py · session.py · refresh.py · pkce.py
│       │   │   ├── limits.py · headers.py · config.py
│       │   │   └── prompts/alexa_style.md     # the assistant's voice, rules and manners
│       │   └── tests/
│       └── web/                 # TypeScript + Vite; no framework, no CDN
│           ├── index.html · vite.config.ts · tsconfig.json
│           ├── public/                        # logo SVGs, icons
│           ├── src/
│           │   ├── main.ts · router.ts · store.ts · api.ts · dom.ts
│           │   ├── pipeline.ts · orders.ts · activity.ts · guide.ts · settings.ts
│           │   ├── orb.ts · plot.ts                 # the voice state and the pipeline plot (SVG, no library)
│           │   ├── backdrop.ts · backdrop-logic.ts · tilt.ts  # the WebGL2 chart behind the pages (lazy chunk) · its colour caps · the card lean
│           │   ├── speech.ts · sound.ts · captions.ts · banner.ts · apphost.ts · trace.ts · logic.ts · icons.ts
│           │   ├── styles.css
│           │   └── voice/
│           │       ├── handsfree.ts               # the microphone state machine
│           │       ├── grammar.ts · commander.ts  # exact spoken commands → actions
│           │       ├── actions.ts                 # the shared action vocabulary
│           │       ├── context.ts · watcher.ts    # what the page tells the model · the board watcher
│           │       ├── lessons.ts                 # corrections learned from the user (names only)
│           │       └── dock.ts · text.ts
│           └── test/                              # logic · voice · handsfree · companion · precision · lessons · backdrop (node --test)
├── db/
│   ├── migrate.py
│   ├── migrations/              # 0001_init · 0002_rls · 0003_auth_schema · 0004_roles_grants · 0005_undo · 0006_least_privilege
│   └── seed/                    # the deterministic Lumenridge workspace, scenarios and hostile notes
├── infra/                       # AWS CDK (Python); its own uv project
│   ├── app.py · cdk.json · pyproject.toml
│   ├── config/                  # dev.yaml · demo.yaml
│   ├── bootstrap/               # github-deploy-role.yaml (the OIDC deploy role)
│   ├── wf_infra/
│   │   ├── config.py · common.py
│   │   └── stacks/              # net · data · auth · mcp · agents · async_ · harness · ops
│   └── tests/                   # config validation and synth assertions
├── deploy/                      # render/: the whole stack as one Docker service on Render's free plan (Caddy in front)
├── integrations/                # kiro/: Kiro and Kiro Crew client config (ADR-034)
├── scripts/                     # dev_keys · dev_token · demo · run_task · conformance_remote · latency_probe · load_baseline · explain_plans · package_lambdas · remote_mcp · default_hosts · restore_drill · addon_assets (the Alexa+ listing page)
├── tests/
│   ├── unit/                    # RIGS golden and property tests, tokens, envelopes, rate limits, docs sync …
│   ├── conformance/             # protocol: transport · edge · tools/list · output schemas · PRM · service token
│   ├── integration/             # against real Postgres: tools, auth server, confirm guard, undo, workers, migrator, seeder, DB and delivery faults, restore drill, offline answers checked against SQL
│   ├── security/                # agent scope · token replay · tenant isolation · races · DB privileges · edge guard · log hygiene · committed secrets · CI security config
│   ├── evals/                   # golden triage set, utterance sheet, scoring
│   └── load/                    # Locust profile
├── docs/
│   ├── PRD.md · DESIGN.md · TECHNICAL.md · DEPLOYMENT.md · SECURITY.md
│   ├── adr/                     # 35 architecture decision records
│   ├── implementation-notes.md · friction-log.md · feature-requests.md
│   ├── brand/                   # logo, lockups, social card, colour tokens
│   └── perf/                    # EXPLAIN plans for the hot queries
```

`services/agents`, `services/harness/api` and `infra` are separate uv projects so the Lambda or
container each ships carries only what it needs ([ADR-011](docs/adr/0011-agents-standalone-project.md)).
`services/harness/web/dist` and the MCP views' build output are generated and gitignored.

---

## Decisions that shape it

Thirty-five records in [`docs/adr`](docs/adr). The ones that matter most:

| Decision | Why | ADR |
|---|---|---|
| Stateless MCP on 2025-11-25, JSON responses | Fits Lambda and Fargate alike; no session affinity; trivially scalable | [001](docs/adr/0001-stateless-mcp-2025-11-25.md) |
| A deterministic hot path | A voice answer cannot wait for a model | [002](docs/adr/0002-deterministic-hot-path.md) |
| Propose, then confirm, with a single-use token | A misheard word must not change a deal | [003](docs/adr/0003-two-tool-confirmation.md) |
| Agents as tools, not a swarm; they read only through MCP | Bounded cost, bounded authority, one audit trail | [004](docs/adr/0004-agents-as-tools.md) · [007](docs/adr/0007-agents-read-via-mcp.md) |
| Postgres is the source of truth for rules and preferences | One consistent place; no drift between a cache and the truth | [005](docs/adr/0005-postgres-rules-and-prefs.md) |
| A self-hosted minimal OAuth 2.1 server | Account linking needs it; nothing else offered resource indicators | [006](docs/adr/0006-self-hosted-oauth.md) |
| Transactional outbox for every external effect | A Slack outage never loses a task or double-posts | [008](docs/adr/0008-transactional-outbox.md) |
| Explainable scoring (RIGS) over ML scoring | Every number can be defended to the person it concerns | [010](docs/adr/0010-rigs-heuristic.md) |
| The Lead writes a draft; a deterministic gate ships it | A model's judgement is an input, not an authority | [012](docs/adr/0012-lead-draft-and-output-gate.md) |
| People see *health*, not risk | "78 healthy" reads better aloud than "22 risk" | [020](docs/adr/0020-health-score.md) |
| Undo on the receipt, more time on a preview | Mistakes are cheap to reverse when they are caught fast | [021](docs/adr/0021-undo-and-more-time.md) |
| Nova on the Free plan; Gemini when Bedrock cannot run | The credits are the only budget | [022](docs/adr/0022-nova-on-the-free-plan.md) · [023](docs/adr/0023-gemini-when-bedrock-cannot-run.md) |
| ECS Fargate while Lambda is refused | Same code, same URLs, a different place to run | [025](docs/adr/0025-ecs-when-lambda-is-refused.md) |
| Five pages, local personalisation (its 3D orb was replaced by [032](docs/adr/0032-the-night-scope.md)) | A voice product needs a screen worth glancing at | [026](docs/adr/0026-harness-pages-orb-and-personalisation.md) |
| Voice-only control of every page | Hands full, eyes elsewhere | [027](docs/adr/0027-voice-only-control.md) |
| Hands-free is a conversation | Nobody should tap a mic twice | [028](docs/adr/0028-hands-free-conversation.md) |
| A companion, not a command line | Change your mind, be known, be told the board | [029](docs/adr/0029-voice-companion.md) |
| A command must account for the whole sentence | Half a sentence must never run silently | [030](docs/adr/0030-voice-precision.md) |
| Totals from the server, read-backs from code, corrections learned | The model words the answer; it never does the sums or decides what a yes saves | [031](docs/adr/0031-learning-from-corrections.md) |
| A night scope instead of a 3D orb and floor | Every fact must survive without colour, motion or sight | [032](docs/adr/0032-the-night-scope.md) |
| The recogniser's runner-up guesses; Amazon Polly reads replies | Hear what was said, and say it in a voice worth hearing | [033](docs/adr/0033-hearing-alternatives-and-polly-voice.md) |
| Kiro and Kiro Crew as read-only MCP clients, no AWS | Agents can triage and draft; only a person confirms a change | [034](docs/adr/0034-kiro-as-an-mcp-client.md) |
| A 3D drowned-coast chart behind the pages (lit sea, datum grid, caustics, card lean), luminance-capped and optional | Atmosphere that moves with the assistant, never a fact and never in the way of a word | [035](docs/adr/0035-the-chart-behind-the-pages.md) |

---

## Run it locally

You need Python 3.12 (the projects pin `>=3.12,<3.13`), [uv](https://docs.astral.sh/uv/), Docker,
Node 20.19+ (the web builds use Vite 8; CI runs Node 24), and `make` (Git Bash works on Windows).

```bash
make bootstrap                  # Python projects (workspace, agents, harness) + both web builds
cp .env.example .env            # local placeholders only
make db-up                      # Postgres 16 on 127.0.0.1:55432
make migrate                    # schema, row-level security, roles
make seed                       # Lumenridge workspace; risk scores Kestrel 71 · Harbor & Pine 66 · Orbital 58
make dev-keys                   # local ES256 signing key in .secrets/ (gitignored)
WF_DEMO_PASSWORD='choose-12+-chars' make auth-bootstrap   # OAuth clients + demo logins
make dev                        # MCP :8080 · auth :8081 · harness :8082 · agent :8083 · workers
```

Open <http://localhost:8082> (use `localhost`, not `127.0.0.1`: the sign-in callback sets its cookie
there), sign in as a seeded user such as `priya@lumenridge.example` with your `WF_DEMO_PASSWORD`, and
ask "What needs my attention today?". Tap a deal for its detail, **Dig into it** for a deep review,
then **Propose** and **Approve**. For the voice experience, use Chrome or Edge, allow the microphone,
and turn on **Hands-free**. The page is labelled *Simulated Alexa+ experience*; the MCP server,
sign-in, tools and views behind it are the real ones.

Call the server directly with a local token:

```bash
TOKEN=$(uv run python scripts/dev_token.py)
curl -s http://localhost:8080/mcp \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" -H "MCP-Protocol-Version: 2025-11-25" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_risk_briefing","arguments":{}}}'
```

**No model needed.** Without Bedrock model ids or a Gemini key, the harness uses a deterministic
keyword router and the agents use a deterministic offline engine; both say so in their output
([ADR-013](docs/adr/0013-offline-triage-engine.md)). In `dev` and `demo` the offline engine never
stands in for a missing model. Without a Slack webhook, Slack messages print to the console.

The harness's offline router answers specific questions from the tools' own figures, never from
guesses: totals and counts by band or segment, deals over or under an amount, the top *n*, deals
closing in a window, a rep's deals, and, for one deal, why it's at risk, who owns it, when it
closes (and whether that falls in this month or next), the next step, its stage, worth, how long it has been in its band, or 14-day trend. It also names the riskiest deal, the soonest to close, and whose deals hold the most money among those it can see. Follow-ups work ("it", "the third one",
"and Harbor?"; the deal in focus is kept in a sealed 30-minute cookie, `wf_h_focus`). A name that
matches several deals is asked about, never guessed; a change request is refused plainly and
nothing is changed. What it can't do, it says so (averages, filtering by quiet or missing fields,
forecasting) instead of answering a different question. It is still less fluent than a model: it
recognises phrasings, it does not reason about them, so unusual wording can still get "Sorry, I
didn't follow that".

### The tools

| For Alexa+ (user scope) | For the triage agents (job scope) |
|---|---|
| `get_risk_briefing` · `explain_deal_risk` · `lookup_deal` | `explain_deal_risk` · `get_deal_timeline` · `get_deal_history` |
| `start_deep_triage` · `get_triage_result` | `get_account_context` · `get_prior_triage` · `get_playbook` |
| `propose_action` · `confirm_action` · `undo_action` · `extend_preview` | |
| `log_deal_update` · `update_briefing_preferences` | |
| `set_standing_order` · `list_standing_orders` · `remove_standing_order` | |

The exact schemas are pinned in
[`services/mcp_server/schemas/tools.snapshot.json`](services/mcp_server/schemas/tools.snapshot.json).

### Demo operations

| Command | Effect |
|---|---|
| `make demo-reset` | Re-seed relative to now and assert the scenario scores |
| `make demo-clock OFFSET=+14h` | Jump the workspace clock to "next morning", then run the watcher (needs `WF_DEMO_MODE=true`) |
| `make demo-watch-now` | One watcher run: fires standing orders now |
| `make demo-status` | Bands, scenario scores, board, pending proposals, jobs, outbox backlog |

Each takes `ENV=dev|demo` to act on a deployed environment. Details in [Deploy](#deploy).

---

## Testing and quality

| Suite | Count | Covers |
|---|---:|---|
| Root (`pytest`) | 468 | RIGS golden and property tests, tokens, envelopes, rate limits, Slack blocks, protocol conformance, security (tenant isolation, exactly-once races, database privileges, Host and Origin checks, log hygiene), docs sync; plus integration tests against a real Postgres (database and delivery faults, a restore drill, the offline router's answers checked against SQL) |
| Harness API | 271 | OAuth flow, a full turn, pending changes, exact read-backs, a spoken no dropping a held change, numbers the user never said, every spoken number and date checked against the turn's tool results, greeting back in kind with no repeated offer, the unheard guard, dismissal, page-action validation, learned corrections (only from a correction), hardening, views, the offline router (specific answers, follow-ups, plain refusals, reading the screen) |
| Harness web | 138 | The hands-free state machine on a fake clock, the spoken grammar, compound commands, whole-sentence precision, board places, the board watcher, learning from corrections, pure logic, and the backdrop's contrast proof (every area colour at or under its luminance cap, so text stays at 4.5:1 or better) |
| Triage agents | 37 | The output gate, the specialists, the runner, the offline engine, against a fake MCP server |
| Infrastructure | 38 | Config validation and CDK synth assertions (TLS-only Postgres, ES256 key, queue timeouts, alarms for the database, queue age and authentication, an agent role with no database access) |
| **Total** | **952** | |

```bash
make test          # unit + protocol conformance
make test-all      # everything, including the agents' and the harness's own suites
make lint          # ruff check + format
```

Beyond unit tests:

- **Browser end-to-end runs** drove the real harness in headless Edge during development: sign-in,
  every page, a full approve/undo, failure paths, an axe accessibility pass, a phone viewport, and
  scripted hands-free speech against the running stack. Those throwaway scripts are not in this
  repository, so this is a manual result, not a check CI repeats.
- **Against a running stack:** `make conformance-remote` (15 protocol checks over HTTPS),
  `make latency` (p95 ≤ 150 ms, p99 ≤ 300 ms per read tool), `make evals` (40 driver and 10
  prompt-injection deep reviews), and a Locust load profile.
- **Two guards keep the docs honest:** a test fails if the tool schemas drift from the pinned
  snapshot, and another fails if the file trees in `docs/TECHNICAL.md` or this README name a path that
  doesn't exist.

What cannot be tested offline is called out, not hidden: with no model in the loop, the *prompted*
behaviour (amending a preview, resolving "the second one", using your name) is covered by the
deterministic guards around it, not by the model itself.

---

## Deploy

Eight CDK stacks per environment (`dev`, `demo`) in `us-east-1`, seven of them deployed while AgentCore
is refused. Pushes to `main` deploy `dev`;
`v*.*.*` tags deploy `demo`. Highlights:

- **Credits only.** On an AWS Free plan account the models are Amazon Nova (on demand, paid from
  credits). `dev` runs Gemini through Strands when Bedrock's quotas are zero. Deploy one environment
  at a time. Roughly $25–30 a month for `dev` before credits, plus about $40 for the five Fargate
  services; budgets e-mail at $15, $25 and $35.
- **Without a domain** nothing needs buying: the MCP and auth servers answer on their API Gateway
  hosts and the harness on its own.
- **Secrets are generated in Secrets Manager and never printed;** two are filled in by hand (the Slack
  webhook and, where used, the Gemini key).

The full runbook (bootstrap, the GitHub OIDC deploy role, first deploy of an environment, the secret
inventory, domains, demo operations against AWS) is in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
Rollbacks, redrives and incident playbooks are in [docs/TECHNICAL.md](docs/TECHNICAL.md) §17, and the
cost model in [ADR-014](docs/adr/0014-free-plan-instance-types.md) and
[ADR-022](docs/adr/0022-nova-on-the-free-plan.md).

---

## Status and honest limits

| Built | Not yet / blocked |
|---|---|
| Core domain, RIGS, seed · MCP server (14 user tools, 6 agent tools) · OAuth server · workers · triage agents · harness API and web (five pages, night scope, hands-free voice companion) · MCP Apps views · demo operations · CDK stacks and CI/CD · latency probe, evals, load profile (run locally) · the Alexa+ add-on side, ready if toolkit access opens | **AWS dev is down:** since 2026-10-16 AWS blocks all compute on the account until Support lifts it (the deploy path is unchanged and was last exercised before the block); meanwhile the stack runs on Render's free plan ([deploy/render](deploy/render/README.md)) · Bedrock-backed eval run (quotas are 0, so `dev` uses Gemini) · a separate `demo` environment (one environment fits the credits) · CloudFront and reserved concurrency in `dev` |

Honest limits of the voice experience:

- Speech recognition is the browser's (Chrome or Edge); the audio is processed by the browser vendor's
  service while hands-free is on. The Guide and the settings say so. Watchfloor itself acts only on
  requests that follow the wake word or a reply, and sends nothing else anywhere.
- On speakers, the assistant's own voice can occasionally be heard as a request; headphones remove the
  problem, and the wake word always works. Changes are never made on that alone: every write waits for
  your yes.
- A standing order cannot be edited in place: the assistant sets the new rule and, after your yes,
  removes the old one, each step confirmed on its own.
- Reading the board in the background marks the briefing as seen; this is disclosed in
  [ADR-029](docs/adr/0029-voice-companion.md).
- The code checks every number and date the assistant says, but not facts without one: a made-up reason
  or a wrong stage is held back only by the prompt and the same-turn tool rule. How often a model breaks
  that is measured by live runs (one model, one script so far), as recorded in
  [ADR-031](docs/adr/0031-learning-from-corrections.md).

Honest limits of the security work ([docs/SECURITY.md](docs/SECURITY.md) has the full register):

- **Not verified:** a staging deploy and migrations there, an RDS snapshot or point-in-time restore
  (the restore drill proves its dump, restore and compare logic against local Postgres only), load
  against a deployed environment (a local concurrency curve exists and is not an AWS number), a RIGS
  backtest (there is no historical CRM data, so no accuracy figure is claimed), an independent review,
  a scan of any built container image, and the security workflow itself, which has not yet run on
  GitHub. A Trivy scan of the working tree found nothing at HIGH or CRITICAL.
- **Model providers:** the Gemini API's unpaid tier may use submitted content to improve Google's
  services and let humans read it, so only the synthetic seed workspace may go to it. Real CRM data
  needs the paid tier or Bedrock.

Where the build departs from the specs, and why, is recorded in
[docs/implementation-notes.md](docs/implementation-notes.md). Platform rough edges found along the
way are in the [friction log](docs/friction-log.md) and [feature requests](docs/feature-requests.md).

---

## Documentation

| Document | What's in it |
|---|---|
| [PRD](docs/PRD.md) | Who it's for, journeys, requirements, metrics |
| [Design](docs/DESIGN.md) | Contracts, algorithms, trade-offs |
| [Technical](docs/TECHNICAL.md) | Every file, diagram, command, runbook and checklist |
| [Deployment](docs/DEPLOYMENT.md) | Bootstrap, first deploy, secrets, domains, demo operations on AWS |
| [Security](docs/SECURITY.md) | What was found and fixed, each with its test; what is accepted; what is not verified |
| [ADRs](docs/adr) | The 35 decisions and their alternatives |
| [Implementation notes](docs/implementation-notes.md) | Where the build departs from the specs |
| [Friction log](docs/friction-log.md) · [Feature requests](docs/feature-requests.md) | What the platforms made hard, and what we'd ask for |
| [Brand](docs/brand/README.md) | The mark, the lockups, the colours |
| [Performance](docs/perf/README.md) | EXPLAIN output for the hot queries; a local concurrency curve |

## License

[Apache License 2.0](LICENSE). The demo data is synthetic; the people and companies in it are made up.
