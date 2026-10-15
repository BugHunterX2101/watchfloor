# Watchfloor: Technical Reference and Implementation Guide

> The engineering handbook for building, running, deploying and operating **Watchfloor**, the ambient pipeline-risk triage add-on for Alexa+.
> **PRD** defines *what* and *why*. **Design Document (DD)** defines *how* and *which trade-offs*. **This document (TD)** defines *exactly what to type, where the code lives, and how to keep it running.*

| Field | Value |
|---|---|
| Document | Technical Document (TD), v1.0 |
| Date | Thu, Oct 8, 2026 |
| Builds on | `Watchfloor PRD.md` (v1.0) · `Watchfloor-Design-Doc.md` (v1.0) |
| Audience | Builders, reviewers, hackathon judges reading the repo |
| Deadline | Fri, Oct 23, 2026, 12:00 PT (Sat, Oct 24, 00:30 IST). Internal target: Thu, Oct 22, 17:00 PT |
| Protocol | MCP **2025-11-25**, Streamable HTTP, stateless, JSON responses |
| Region | AWS `us-east-1` |

**Conventions.** Shell commands use bash (Git Bash works on Windows). `⚠ Verify` marks behavior of third-party tools that must be confirmed against the pinned version before relying on it (spike IDs S1–S7 come from DD §27). Every example uses the synthetic "Lumen Ridge Software" workspace. Money is stored as integer cents and times are ISO 8601.

---

## Table of contents

- [0. How the three documents fit together](#0-how-the-three-documents-fit-together)
- [1. System at a glance](#1-system-at-a-glance)
- [2. Implementation deltas from the Design Document](#2-implementation-deltas-from-the-design-document)
- [3. Technology stack](#3-technology-stack)
- [4. Architecture diagrams](#4-architecture-diagrams)
- [5. Repository file structure](#5-repository-file-structure)
- [6. Module reference](#6-module-reference)
- [7. Configuration and secrets](#7-configuration-and-secrets)
- [8. Local development](#8-local-development)
- [9. Database operations](#9-database-operations)
- [10. Build and packaging](#10-build-and-packaging)
- [11. Infrastructure as code (CDK)](#11-infrastructure-as-code-cdk)
- [12. CI/CD pipelines](#12-cicd-pipelines)
- [13. API and protocol reference](#13-api-and-protocol-reference)
- [14. Alexa+ add-on lifecycle](#14-alexa-add-on-lifecycle)
- [15. Testing guide](#15-testing-guide)
- [16. Observability guide](#16-observability-guide)
- [17. Operations runbooks](#17-operations-runbooks)
- [18. Troubleshooting](#18-troubleshooting)
- [19. Engineering checklists](#19-engineering-checklists)
- [20. Coding standards and conventions](#20-coding-standards-and-conventions)
- [Appendix A: Root project files](#appendix-a-root-project-files)
- [Appendix B: Glossary of identifiers](#appendix-b-glossary-of-identifiers)

---

## 0. How the three documents fit together

```mermaid
flowchart LR
  R["Research .docx<br/>(source, audited)"] --> PRD["PRD v1.0<br/>what & why<br/>personas · journeys · FRs · metrics"]
  PRD --> DD["Design Doc v1.0<br/>how & trade-offs<br/>contracts · algorithms · ADRs"]
  DD --> TD["Technical Doc v1.0<br/>build · run · deploy · operate<br/>files · code · commands · runbooks"]
  TD --> REPO["watchfloor/ repo<br/>code · tests · infra"]
  REPO --> SUB["Devpost submission<br/>video · README · feedback · friction log"]
```

| Question | Go to |
|---|---|
| Who is this for, and what must it do? | PRD §03–§09 |
| What are the exact tool schemas, RIGS formulas and ADRs? | DD §6, §7, §26 |
| Where does a file live, what does it export, how do I run or deploy it? | **This document** |

---

## 1. System at a glance

| Dimension | Value |
|---|---|
| Customer-facing MCP tools | **12** (7 read, 5 write) |
| Agent-only MCP tools | **5** (+ `explain_deal_risk`), scope `watchfloor.agent.read` |
| MCP App UI resources | **3** (`triage-board`, `deal-detail`, `confirm-sheet`) |
| Lambda functions | **7** (`mcp-server`, `auth-server`, `dispatcher`, `watcher`, `sweeper`, `canary`, `harness-api`) |
| Agent runtime | **1** AgentCore Runtime hosting **5** Strands agents |
| Datastore | RDS PostgreSQL 16: **18** tables in `public` and **4** in `auth`, with row-level security |
| Queues | **2** SQS queues (`jobs`, `outbox`), each with a DLQ |
| Schedules | Watcher every 15 min · sweeper every 1 min · canary every 5 min · daily snapshot 00:05 |
| CDK stacks | **8** (`Net`, `Data`, `Auth`, `Mcp`, `Async`, `Agents`, `Harness`, `Ops`) |
| Latency SLO | Server p95 ≤ 150 ms on read tools (Alexa+ round trip must stay < 500 ms) |
| Triage SLO | p50 ≤ 20 s · p95 ≤ 45 s · hard stop 90 s |
| Budget | ≈ $70–100 for Oct 8 – Nov 20 (DD §25) |

---

## 2. Implementation deltas from the Design Document

Writing implementation-level code exposed five places where the DD needed precision. They are binding for the build, and DD v1.1 should absorb them.

| # | DD said | Implementation does | Reason |
|---|---|---|---|
| T1 | `FastMCP(...)` assembly | Low-level `mcp.server.lowlevel.Server` + `StreamableHTTPSessionManager(stateless=True, json_response=True)` | Per-scope `tools/list` filtering, our own validation and error mapping, and exact `outputSchema` control need the low-level handlers |
| T2 | Out-of-scope tool returns `-32601` (DD §20.4) | Unknown or out-of-scope tool returns **`-32602` "Unknown tool"** | Matches the MCP tools specification's own error example. Scope filtering means the tool is "unknown" to that caller |
| T3 | Speakable dates use the weekday when within 6 days | `*_display` strings always read **"Thursday, October 22"**. Relative phrasing ("tomorrow") is left to Alexa+ | Unambiguous on screen and in Slack. Alexa+ owns phrasing anyway (DD constraint C4) |
| T4 | EventBridge **Scheduler** | EventBridge **rules** (`events.Rule` + `Schedule.rate`) | Stable CDK L2 constructs. Behavior is identical at these frequencies |
| T5 | Protocol-version check in middleware | `EdgeGuard` **buffers the body (≤ 64 KiB)** to read the JSON-RPC `method` before the version check, then replays it | `initialize` is exempt from the header requirement, which needs the method name |

---

## 3. Technology stack

Exact versions are pinned in `uv.lock` and `package-lock.json`. The table lists the minimum constraints and why each choice was made.

| Layer | Technology | Constraint | Why |
|---|---|---|---|
| Language (services) | Python | 3.12 | Lambda runtime `python3.12`; Strands, AgentCore and the MCP SDK are all Python-first |
| Package manager | uv (workspace mode) | latest stable, pinned in CI | Fast, reproducible lockfile, workspace members |
| MCP | `mcp` (official Python SDK) | First version whose server negotiates `2025-11-25` **⚠ Verify (S5)** | Spec-conformant Streamable HTTP and the low-level server |
| ASGI | Starlette + Uvicorn | current stable | Minimal overhead; Lambda Web Adapter fronts Uvicorn |
| Validation | Pydantic v2 | ≥ 2.7 | Schemas generated from models; fast `model_dump_json` |
| DB driver | asyncpg | current stable | Fastest Postgres driver for Python; prepared-statement cache |
| JWT | PyJWT[crypto] | ≥ 2.8 | ES256 verification, `PyJWKClient` with caching |
| OAuth server | Authlib | current stable | OAuth 2.1 primitives (PKCE, code grant, client credentials) |
| Password hashing | argon2-cffi | current stable | Argon2id |
| Canonical JSON | `rfc8785` | current stable | JCS hashing for confirmation-token binding |
| IDs | `python-ulid` | current stable | Sortable prefixed IDs |
| Observability | AWS Lambda Powertools (Python) | v3 | Logger, Metrics (EMF), Tracer |
| Agents | `strands-agents` | current stable | Agents-as-tools, MCP client, Bedrock models |
| Agent runtime | `bedrock-agentcore` | 1.24.x (still classified "Alpha" on PyPI) | `BedrockAgentCoreApp`, Memory client |
| Models | Amazon Bedrock: Claude Sonnet 5.5 (inference profile), Claude Haiku-class | IDs **⚠ Verify (S6)** | Quality for the Lead, Strategist and Critic; speed and cost for the Analyst and Context agents |
| Database | PostgreSQL | 16 (RDS `db.t4g.micro`) | RLS, `pg_trgm`, JSONB |
| UI (MCP Apps) | TypeScript + Preact + Vite + `vite-plugin-singlefile` | Node 20 LTS | Single-file HTML resources |
| MCP Apps SDK | `@modelcontextprotocol/ext-apps` | pinned **⚠ Verify (S3)** | Host bridge (tool results in, tool calls out) |
| IaC | AWS CDK v2 (Python) | `aws-cdk-lib` 2.x | Same language as the services |
| NAT | `cdk-fck-nat` | pinned **⚠ Verify** | Cheap egress (ADR-009) |
| Testing | pytest, pytest-asyncio, Hypothesis, testcontainers, httpx, Locust | current stable | Unit, property, integration, conformance, load |
| Lint and type | ruff, pyright | current stable | `pyright --strict` on `watchfloor_core` |
| Secrets scanning | gitleaks (pre-commit) + GitHub push protection | — | Public repo hygiene |

---

## 4. Architecture diagrams

### 4.1 System context

```mermaid
flowchart LR
  subgraph People
    PR["Priya · founder<br/>Echo Show"]
    MA["Marcus / Maya · team<br/>Slack"]
    JU["Judges<br/>repo · video · harness"]
  end
  subgraph Amazon["Amazon"]
    AX["Alexa+<br/>speech · reasoning · response"]
    BR["Amazon Bedrock<br/>Claude models"]
    AC["Bedrock AgentCore<br/>Runtime · Memory"]
  end
  WF["Watchfloor<br/>MCP server · auth · workers · agents"]
  SL["Slack"]

  PR -- voice / touch --> AX
  AX -- "MCP 2025-11-25 · Streamable HTTP · OAuth 2.1" --> WF
  WF -- "deep triage jobs" --> AC
  AC -- inference --> BR
  WF -- "Block Kit alerts, plans, drafts" --> SL
  SL --> MA
  JU -- "Path B: simulated Alexa+" --> WF
```

### 4.2 Logical layered architecture

```mermaid
flowchart TB
  subgraph L1["Experience layer"]
    V["Alexa+ voice"]
    UI["MCP Apps · triage-board · deal-detail · confirm-sheet"]
    HS["Harness web (Path B)"]
    SK["Slack messages"]
  end
  subgraph L2["Protocol & edge layer"]
    EG["EdgeGuard · Host · Origin · Accept · body ≤ 64 KiB"]
    AU["Auth · JWT ES256 · aud = resource URI"]
    VR["Version · MCP-Protocol-Version"]
    RL["Rate limit · per user / workspace"]
    SM["StreamableHTTPSessionManager · stateless · JSON"]
  end
  subgraph L3["Application layer"]
    REG["Tool registry · scope-filtered"]
    TL["Tool handlers (12 + 5)"]
    RS["UI resources"]
  end
  subgraph L4["Domain layer · watchfloor_core"]
    RIGS["RIGS engine (pure)"]
    SVC["Services · briefing · actions · rules · triage · updates"]
    TOK["Confirmation tokens · JCS hashing"]
    SPK["Speech formatting"]
  end
  subgraph L5["Infrastructure layer"]
    DB[("Postgres + RLS")]
    Q["SQS · outbox"]
    WK["Workers · dispatcher · watcher · sweeper"]
    AG["AgentCore agents"]
  end
  L1 --> L2 --> L3 --> L4 --> L5
  WK --> AG
  AG -- "MCP (agent scope)" --> L2
```

### 4.3 AWS deployment architecture

```mermaid
flowchart TB
  subgraph Internet
    ALX["Alexa+ add-on bridge"]
    BRW["Browser · harness / login pages"]
    SLK["Slack webhooks"]
  end

  subgraph AWS["AWS account · us-east-1"]
    subgraph Edge["Public endpoints"]
      APIM["HTTP API · mcp.watchfloor.example"]
      APIA["HTTP API · auth.watchfloor.example"]
      APIH["HTTP API · harness API"]
      CFD["CloudFront + S3 · harness web"]
      LH["λ harness-api (no VPC)"]
    end

    subgraph VPC["VPC 10.20.0.0/16 · 2 AZs"]
      subgraph PUB["Public subnets"]
        NAT["fck-nat · t4g.nano"]
      end
      subgraph PRIV["Private subnets"]
        LM["λ mcp-server<br/>alias live · PC=1 · 1024 MB"]
        LA["λ auth-server"]
        LD["λ dispatcher · reserved 3"]
        LW["λ watcher"]
        LS["λ sweeper"]
        LC["λ canary"]
        RDS[("RDS PostgreSQL 16<br/>db.t4g.micro · encrypted")]
      end
    end

    subgraph REG["Regional managed services"]
      SQJ["SQS jobs + DLQ"]
      SQO["SQS outbox + DLQ"]
      EVB["EventBridge rules"]
      ACR["AgentCore Runtime<br/>arm64 container"]
      ACM["AgentCore Memory"]
      BED["Bedrock · Claude"]
      KMS["KMS · ES256 signing key"]
      SEC["Secrets Manager"]
      ECR["ECR · agent image"]
      CW["CloudWatch · logs · EMF · alarms"]
      BUD["AWS Budgets → SNS email"]
    end
  end

  ALX --> APIM --> LM
  BRW --> APIA --> LA
  BRW --> CFD
  BRW --> APIH --> LH
  LH -- "MCP + user token" --> APIM
  LM --> RDS
  LA --> RDS
  LA --> KMS
  LM --> SQJ
  LM --> SQO
  SQJ --> LD
  SQO --> LD
  LD --> ACR
  ECR --> ACR
  ACR --> BED
  ACR --> ACM
  ACR -- "MCP · agent scope" --> APIM
  LD --> RDS
  EVB --> LW
  EVB --> LS
  EVB --> LC
  LW --> RDS
  LW --> SQO
  LS --> RDS
  LS --> SQO
  LC --> APIM
  LD --> NAT --> SLK
  LM -.-> CW
  LD -.-> CW
```

> Lambdas in private subnets reach SQS, KMS, Secrets Manager, Bedrock and AgentCore through the NAT instance. This avoids interface VPC endpoints, which cost about $7 per endpoint per AZ each month. An S3 **gateway** endpoint is free and is included.

### 4.4 Security zones and trust boundaries

```mermaid
flowchart LR
  subgraph Z0["Zone 0 · untrusted"]
    A1["Alexa+ requests"]
    A2["Browsers"]
    A3["CRM free text<br/>(activity bodies)"]
  end
  subgraph Z1["Zone 1 · validated edge"]
    E1["EdgeGuard 421/403/406/413"]
    E2["Auth 401 (no WWW-Authenticate)"]
    E3["Version 400 · Rate 429"]
  end
  subgraph Z2["Zone 2 · trusted core"]
    C1["Tool handlers + services"]
    C2[("Postgres · RLS FORCE")]
    C3["KMS / Secrets"]
  end
  subgraph Z3["Zone 3 · model zone (outputs untrusted)"]
    M1["AgentCore agents<br/>read-only agent scope"]
    M2["Bedrock"]
  end
  A1 --> E1 --> E2 --> E3 --> C1 --> C2
  A2 --> E1
  A3 -. "stored as data, wrapped as untrusted when shown to models" .-> C2
  C1 -- "job-scoped token" --> M1
  M1 --> M2
  M1 -- "TriageOutput · schema-gated" --> C1
  C1 --> C3
```

### 4.5 MCP server internal components

```mermaid
flowchart TB
  IN["HTTP request (API Gateway → LWA → Uvicorn)"] --> TEL["Telemetry · trace id · timing"]
  TEL --> EG["EdgeGuard · Host · Origin · Accept · buffer body"]
  EG --> RT{"Route"}
  RT -- "GET /.well-known/*" --> WK["wellknown.py · PRM"]
  RT -- "GET /healthz" --> HZ["healthz"]
  RT -- "GET or DELETE /mcp" --> M405["405 · Allow: POST"]
  RT -- "POST /mcp" --> AUTH["auth.py · JWT verify · Principal"]
  AUTH --> VER["version.py · 2025-11-25"]
  VER --> RL["ratelimit.py · Postgres window"]
  RL --> SMG["StreamableHTTPSessionManager (stateless, JSON)"]
  SMG --> SRV["server.py · low-level Server"]
  SRV --> LT["list_tools → registry.filter(scopes)"]
  SRV --> CT["call_tool → validate → handler → audit"]
  SRV --> LR["list/read_resources → ui_dist/*.html"]
  CT --> SVC["watchfloor_core.services"]
  SVC --> DBX["db.ws_tx(workspace) · RLS"]
  SVC --> OBX["outbox + best-effort SQS"]
```

### 4.6 Request pipeline decision flow

```mermaid
flowchart TD
  S(["POST /mcp"]) --> H{"Host allowed?"}
  H -- no --> R421["421 Misdirected Request"]
  H -- yes --> O{"Origin present and not allowed?"}
  O -- yes --> R403["403 Forbidden"]
  O -- no --> AC{"Accept lists application/json<br/>and text/event-stream?"}
  AC -- no --> R406["406 Not Acceptable"]
  AC -- yes --> SZ{"Body ≤ 64 KiB and valid JSON?"}
  SZ -- "too big" --> R413["413"]
  SZ -- "bad JSON" --> R400a["400 · parse error"]
  SZ -- ok --> T{"Valid Bearer JWT?<br/>iss · aud · exp · sig"}
  T -- no --> R401["401 · no WWW-Authenticate"]
  T -- yes --> V{"method == initialize<br/>or MCP-Protocol-Version == 2025-11-25?"}
  V -- no --> R400b["400 Bad Request"]
  V -- yes --> RL{"Within rate limit?"}
  RL -- no --> R429["429 · Retry-After"]
  RL -- yes --> N{"JSON-RPC notification<br/>or response?"}
  N -- yes --> R202["202 Accepted · empty"]
  N -- no --> OK["200 application/json · JSON-RPC result or error"]
```

### 4.7 Journey J1: morning briefing (hot path)

```mermaid
sequenceDiagram
  autonumber
  actor P as Priya
  participant AX as Alexa+
  participant M as mcp-server
  participant DB as Postgres
  P->>AX: "What's at risk in my pipeline?"
  AX->>M: tools/call get_risk_briefing {}
  M->>DB: set_config(ws) · SELECT prefs, last_briefing_at
  M->>DB: SELECT ranked risks (risk_scores ⋈ deals ⋈ accounts ⋈ users)
  M->>DB: SELECT rules fired since last briefing
  M->>DB: UPDATE users.last_briefing_at · INSERT audit_log
  M-->>AX: structuredContent {summary, deals[3], triggered_rules} + ui://triage-board
  AX-->>P: Spoken briefing + board on Echo Show
```

### 4.8 Journey J2: deep triage (warm path, end to end)

```mermaid
sequenceDiagram
  autonumber
  actor P as Priya
  participant AX as Alexa+
  participant M as mcp-server
  participant DB as Postgres
  participant Q as SQS jobs
  participant D as dispatcher
  participant AS as auth-server
  participant R as AgentCore (Lead + specialists)
  participant B as Bedrock
  participant S as Slack
  P->>AX: "Dig into Kestrel."
  AX->>M: tools/call start_deep_triage {deal_id}
  M->>DB: lock deal · dedupe check · INSERT triage_jobs(queued)
  M->>Q: SendMessage {job_id}
  M-->>AX: {job_id, eta_seconds: 20}
  AX-->>P: "Review started, plan goes to Slack."
  Q->>D: deliver message
  D->>DB: mark running
  D->>AS: client_credentials (ws, job) → job token (5 min)
  D->>R: InvokeAgentRuntime {deal_id, mcp_token}
  R->>M: tools/call get_deal_timeline / get_deal_history / get_account_context (agent scope)
  R->>B: Converse (Haiku-class specialists, Sonnet lead/strategist/critic)
  R-->>D: final TriageOutput JSON (streamed)
  D->>D: Pydantic schema gate
  D->>DB: complete job · INSERT outbox(slack plan)
  D->>S: Block Kit plan
  P->>AX: "What did it find?"
  AX->>M: tools/call get_triage_result {deal_id}
  M-->>AX: voice_summary · drivers · actions
```

### 4.9 Journey J3: act with confirmation

```mermaid
sequenceDiagram
  autonumber
  actor P as Priya
  participant AX as Alexa+
  participant M as mcp-server
  participant DB as Postgres
  participant Q as SQS outbox
  participant D as dispatcher
  participant S as Slack
  P->>AX: "Have Maya book their CFO by Thursday."
  AX->>M: propose_action {deal_id, create_task, assignee_name: Maya, due_date}
  M->>DB: resolve assignee · INSERT action_proposals(pending, token_hash, params_hash)
  M-->>AX: preview + confirmation_token (5 min)
  AX-->>P: "Create a task for Maya… Go ahead?"
  P->>AX: "Yes."
  AX->>M: confirm_action {confirmation_token}
  M->>DB: BEGIN · claim token (UPDATE … WHERE pending) · INSERT tasks · INSERT outbox · INSERT audit · COMMIT
  M->>Q: SendMessage {outbox_id} (best effort)
  M-->>AX: {status: executed, audit_id}
  Q->>D: deliver
  D->>S: "Task for @Maya: Book CFO call with Kestrel Biotech"
```

### 4.10 Journey J4: standing order and watcher (cold path)

```mermaid
sequenceDiagram
  autonumber
  actor P as Priya
  participant AX as Alexa+
  participant M as mcp-server
  participant DB as Postgres
  participant E as EventBridge (15 min)
  participant W as watcher
  participant Q as SQS outbox
  participant D as dispatcher
  participant S as Slack
  P->>AX: "From now on flag deals over 50K silent for 5 days."
  AX->>M: set_standing_order {min_amount_usd: 50000, quiet_days: 5}
  M->>DB: INSERT standing_orders (deterministic description)
  M-->>AX: {rule_id, description}
  E->>W: scheduled run
  W->>DB: recompute RIGS (hysteresis) · UPSERT risk_scores
  W->>DB: evaluate rules · INSERT alerts · INSERT outbox (dedupe_key)
  W->>Q: SendMessage per outbox row
  Q->>D: deliver
  D->>S: "At Risk · Harbor & Pine (66) · Standing order fired"
  Note over P,AX: Next morning, get_risk_briefing returns triggered_rules first
```

### 4.11 Journey J5: voice CRM update

```mermaid
sequenceDiagram
  autonumber
  actor P as Priya
  participant AX as Alexa+
  participant M as mcp-server
  participant DB as Postgres
  P->>AX: "Log for Harbor and Pine: security review, decision moved to Nov 15."
  AX->>M: lookup_deal {name_query: "Harbor and Pine"}
  M-->>AX: candidates[1]
  AX->>M: log_deal_update {deal_id, note, next_step, close_date: 2026-11-15}
  M->>DB: apply note + next_step (+ deal_history) · RIGS recompute
  M->>DB: close_date change → INSERT action_proposals(pending)
  M-->>AX: {applied_fields, score_before, score_after, pending_proposal}
  AX-->>P: "Saved. Move the close date to November 15?"
  P->>AX: "Yes."
  AX->>M: confirm_action {token}
  M-->>AX: executed · score recomputed
```

### 4.12 Authentication and account linking

```mermaid
sequenceDiagram
  autonumber
  participant AX as Alexa+ / Alexa app
  participant RS as mcp-server (resource)
  participant AS as auth-server
  participant K as KMS
  AX->>RS: POST /mcp (no token)
  RS-->>AX: 401 (no WWW-Authenticate)
  AX->>RS: GET /.well-known/oauth-protected-resource
  RS-->>AX: {resource, authorization_servers}
  AX->>AS: GET /.well-known/oauth-authorization-server
  AX->>AS: GET /authorize?response_type=code&code_challenge=…&code_challenge_method=S256&resource=…
  AS-->>AX: login + consent → 302 redirect_uri?code=…&state=…&iss=…
  AX->>AS: POST /token (code, code_verifier, resource, client auth)
  AS->>K: Sign(ES256, JWT claims)
  AS-->>AX: access_token (60 min) + refresh_token (rotating)
  AX->>RS: POST /mcp · Authorization: Bearer
  RS->>RS: verify with cached JWKS (10 min)
```

### 4.13 Deep-triage agent topology

```mermaid
flowchart TB
  D["dispatcher"] -->|"InvokeAgentRuntime"| APP["BedrockAgentCoreApp · @app.entrypoint"]
  APP --> LEAD["Triage Lead · Sonnet 5.5 · temp 0 · ≤ 8 steps"]
  LEAD -->|tool| AN["signal_analyst · Haiku-class"]
  LEAD -->|tool| CX["account_context · Haiku-class"]
  LEAD -->|tool| ST["playbook_strategist · Sonnet 5.5 · temp 0.3"]
  LEAD -->|tool| CR["critic · Sonnet 5.5 · temp 0 · 1 revision"]
  AN -->|"MCP agent scope"| T1["get_deal_timeline · get_deal_history · explain_deal_risk"]
  CX -->|"MCP agent scope"| T2["get_account_context · get_prior_triage"]
  CX --> MEM["AgentCore Memory · actorId = ws:user"]
  ST -->|"MCP agent scope"| T3["get_playbook"]
  LEAD --> OUT["TriageOutput JSON → dispatcher schema gate"]
```

### 4.14 Core domain model

```mermaid
classDiagram
  class Principal {
    +str user_id
    +str workspace_id
    +frozenset~str~ scopes
    +str|None job_id
    +bool is_agent()
  }
  class DealSnapshot {
    +str deal_id
    +Stage stage
    +int amount_cents
    +str|None economic_buyer_id
    +str|None next_step
    +date|None next_step_date
    +str|None decision_process
    +datetime|None last_two_way_at
    +Engagement engagement_14d
    +list~FieldChange~ history_90d
    +datetime now
  }
  class RigsResult {
    +int score
    +float r
    +float i
    +float g
    +float s
    +list~Reason~ reasons
    +str model_version
    +Band band(previous)
  }
  class Reason {
    +str code
    +str factor
    +float contribution
    +dict params
    +list~str~ evidence_ids
  }
  class ToolSpec {
    +str name
    +str scope
    +str description
    +type input_model
    +type output_model
    +ToolAnnotations annotations
    +str|None ui_resource
    +int budget_ms
    +handler(ctx, args) ToolOutcome
  }
  class ToolOutcome {
    +dict structured
    +bool is_error
    +to_call_tool_result() CallToolResult
  }
  class Proposal {
    +str id
    +str action_type
    +dict params
    +bytes params_hash
    +bytes token_hash
    +ProposalStatus status
    +datetime expires_at
  }
  class TriageOutput {
    +str voice_summary
    +list~Driver~ risk_drivers
    +list~RecommendedAction~ recommended_actions
    +float confidence
  }
  DealSnapshot ..> RigsResult : rigs.score()
  RigsResult "1" *-- "many" Reason
  ToolSpec ..> ToolOutcome : returns
  ToolSpec ..> Principal : authorizes
  Proposal ..> Principal : bound to
  TriageOutput ..> Proposal : actions become proposals
```

### 4.15 Data model (compact ER)

```mermaid
erDiagram
  WORKSPACES ||--o{ USERS : has
  USERS ||--|| USER_PREFS : has
  WORKSPACES ||--o{ ACCOUNTS : has
  ACCOUNTS ||--o{ CONTACTS : has
  ACCOUNTS ||--o{ DEALS : has
  DEALS ||--o{ ACTIVITIES : logs
  DEALS ||--o{ DEAL_HISTORY : changes
  DEALS ||--|| RISK_SCORES : current
  DEALS ||--o{ RISK_SCORE_DAILY : trend
  DEALS ||--o{ TASKS : has
  USERS ||--o{ STANDING_ORDERS : sets
  STANDING_ORDERS ||--o{ ALERTS : fires
  USERS ||--o{ ACTION_PROPOSALS : proposes
  DEALS ||--o{ TRIAGE_JOBS : investigated
  WORKSPACES ||--o{ OUTBOX : queues
  WORKSPACES ||--o{ AUDIT_LOG : records
```

### 4.16 CI/CD and environment promotion

```mermaid
flowchart LR
  DEV["Feature branch"] --> PR["Pull request"]
  PR --> CI{"ci.yml<br/>ruff · pyright · unit · conformance (ASGI)<br/>schema snapshot · cdk synth · ui build"}
  CI -- fail --> DEV
  CI -- pass --> MAIN["main"]
  MAIN --> DD{"deploy-dev.yml<br/>integration (testcontainers)<br/>cdk deploy dev · migrate · seed<br/>smoke: tools/list via canary"}
  DD -- pass --> TAG["git tag v1.x"]
  TAG --> RD{"release-demo.yml<br/>cdk deploy demo · migrate<br/>evals · conformance vs URL<br/>Local Inspector report"}
  RD --> MAN["Manual gate:<br/>alexa-ai deploy if schema changed<br/>utterance eval in simulator"]
  MAN --> FREEZE["Demo frozen<br/>Oct 22 → Nov 20"]
```

### 4.17 Path A / Path B decision (spike S1)

```mermaid
flowchart TD
  S(["Oct 9 · spike S1"]) --> A{"alexa-ai configure works and<br/>web simulator reachable?"}
  A -- yes --> B{"Hello-world tool invoked<br/>from simulator?"}
  B -- yes --> PA["Path A · real Alexa+ add-on<br/>harness = dev tool only"]
  B -- no --> FIX{"Fixable by Oct 10?<br/>(auth, URL, transport)"}
  FIX -- yes --> B
  FIX -- no --> PB
  A -- no --> PB["Path B · simulated Alexa+ (harness)<br/>same MCP server · real OAuth · real protocol"]
  PA --> LOG["Friction log entry"]
  PB --> LOG
```

---

## 5. Repository file structure

Every file listed below has a defined owner and purpose. `(gen)` marks generated artifacts, which are produced by the build and never edited by hand.

```
watchfloor/
├── .github/
│   ├── workflows/
│   │   ├── ci.yml                          # PR gate: lint, types, unit, conformance, snapshots, synth, ui build
│   │   ├── deploy-dev.yml                  # main → integration tests → cdk deploy dev → migrate → seed → smoke
│   │   └── release-demo.yml                # tag → deploy demo → evals → conformance vs URL → inspector report
│   ├── CODEOWNERS
│   └── pull_request_template.md            # checklist: schema changed? alexa-ai deploy? friction log?
├── .env.example                            # every env var with safe placeholder values (§7.1)
├── .gitignore
├── .pre-commit-config.yaml                 # ruff, ruff-format, gitleaks, end-of-file-fixer
├── LICENSE                                 # Apache-2.0 (visible in GitHub "About")
├── README.md                               # judge-facing: what, demo link, setup, run, architecture, testing
├── Makefile                                # developer entry points (Appendix A.1)
├── Procfile.dev                            # local multi-process runner (Appendix A.5)
├── docker-compose.yml                      # local Postgres 16 (Appendix A.2)
├── pyproject.toml                          # uv workspace root + ruff/pyright/pytest config (Appendix A.3)
├── uv.lock                                 # (gen) pinned dependency graph
│
├── docs/
│   ├── PRD.md                              # Watchfloor PRD v1.0
│   ├── DESIGN.md                           # Design Document v1.0
│   ├── TECHNICAL.md                        # this document
│   ├── adr/
│   │   ├── 0001-stateless-mcp-2025-11-25.md
│   │   ├── 0002-deterministic-hot-path.md
│   │   ├── 0003-two-tool-confirmation.md
│   │   ├── 0004-agents-as-tools.md
│   │   ├── 0005-postgres-rules-and-prefs.md
│   │   ├── 0006-self-hosted-oauth.md
│   │   ├── 0007-agents-read-via-mcp.md
│   │   ├── 0008-transactional-outbox.md
│   │   ├── 0009-nat-instance.md
│   │   └── 0010-rigs-heuristic.md
│   ├── friction-log.md                     # live; required fields per entry (DD §27)
│   ├── feature-requests.md                 # description · why · priority
│   ├── perf/                               # EXPLAIN ANALYZE plans, Locust reports
│   └── images/                             # architecture PNG exports, screenshots for README/Devpost
│
├── packages/
│   └── core/
│       ├── pyproject.toml                  # name = "watchfloor-core"
│       └── watchfloor_core/
│           ├── __init__.py
│           ├── settings.py                 # typed env settings shared by all services
│           ├── clock.py                    # Clock: real now + demo offset (DEMO_MODE only)
│           ├── db.py                       # asyncpg pool, ws_tx() workspace-scoped transactions
│           ├── ids.py                      # new_id("dl") → "dl_01J9…" (ULID)
│           ├── errors.py                   # WatchfloorError + ErrorCode enum (DD §5.5)
│           ├── speech.py                   # money_display, date_display, list joining
│           ├── tokens.py                   # confirmation tokens, JCS params hash
│           ├── principal.py                # Principal dataclass + scope constants
│           ├── rigs.py                     # pure RIGS engine (DD §7)
│           ├── rigs_params.py              # loads config/rigs.yaml → StageParams, Weights
│           ├── config/
│           │   └── rigs.yaml               # stage norms, baselines, weights, thresholds (rigs-1.1)
│           ├── models/
│           │   ├── domain.py               # DealSnapshot, Engagement, FieldChange, Reason, RigsResult, Band
│           │   ├── tools.py                # Pydantic I/O models for all 19 tools (source of JSON Schemas)
│           │   └── triage.py               # TriageOutput, Driver, RecommendedAction
│           ├── repos/                      # SQL only; one module per aggregate
│           │   ├── deals.py                # load_snapshot(), find_by_name(), apply_update()
│           │   ├── scores.py               # upsert_score(), ranked_risks(), trend_14d()
│           │   ├── proposals.py            # create(), claim(), get_by_token_hash(), supersede()
│           │   ├── rules.py                # create(), list_active(), deactivate(), matches(), fired_since()
│           │   ├── jobs.py                 # create_or_dedupe(), mark_running(), complete(), fail()
│           │   ├── outbox.py               # enqueue(), due(), mark_sent(), backoff()
│           │   ├── prefs.py                # get(), update(), last_briefing_at(), touch_last_briefing()
│           │   ├── tasks.py                # create()
│           │   ├── workspaces.py           # all_ids(), clock_offset()
│           │   ├── maintenance.py          # purge_old()
│           │   ├── audit.py                # record()
│           │   └── ratelimit.py            # hit(subject, limit) → allowed
│           └── services/                   # business logic used by tools AND workers
│               ├── briefing.py             # build_briefing()
│               ├── explain.py              # explain_deal()
│               ├── actions.py              # propose(), confirm(), executors
│               ├── rules.py                # describe_rule(), create_rule(), evaluate_rules()
│               ├── triage.py               # start_triage(), get_result()
│               ├── updates.py              # log_update()
│               └── scoring.py              # recompute_deal(), recompute_workspace(), daily_snapshot_if_due()
│
├── services/
│   ├── mcp_server/
│   │   ├── pyproject.toml                  # depends on watchfloor-core, mcp, starlette, uvicorn, pyjwt, powertools
│   │   ├── run.sh                          # exec uvicorn mcp_server.app:app --host 127.0.0.1 --port 8080
│   │   ├── mcp_server/
│   │   │   ├── __init__.py
│   │   │   ├── app.py                      # Starlette app: routes, lifespan, middleware wiring
│   │   │   ├── server.py                   # low-level Server: list_tools / call_tool / resources
│   │   │   ├── session.py                  # StreamableHTTPSessionManager(stateless, json_response)
│   │   │   ├── context.py                  # current_principal() contextvar + request fallback
│   │   │   ├── wellknown.py                # PRM (RFC 9728), /healthz
│   │   │   ├── middleware/
│   │   │   │   ├── __init__.py             # build_middleware() order (§6.2)
│   │   │   │   ├── telemetry.py            # trace id, timing, EMF
│   │   │   │   ├── edge_guard.py           # Host 421 · Origin 403 · Accept 406 · body buffer 413/400
│   │   │   │   ├── auth.py                 # Bearer JWT → Principal · 401 without WWW-Authenticate
│   │   │   │   ├── version.py              # MCP-Protocol-Version → 400
│   │   │   │   └── ratelimit.py            # 429 + Retry-After
│   │   │   ├── tools/
│   │   │   │   ├── __init__.py             # REGISTRY: dict[str, ToolSpec]
│   │   │   │   ├── base.py                 # ToolSpec, ToolOutcome, ToolContext, descriptor()
│   │   │   │   ├── descriptions.py         # exact description strings (DD §6.3)
│   │   │   │   ├── snapshot.py             # writes tools.snapshot.json (CI diff)
│   │   │   │   ├── briefing.py             # get_risk_briefing
│   │   │   │   ├── explain.py              # explain_deal_risk
│   │   │   │   ├── lookup.py               # lookup_deal
│   │   │   │   ├── triage.py               # start_deep_triage, get_triage_result
│   │   │   │   ├── actions.py              # propose_action, confirm_action
│   │   │   │   ├── rules.py                # set/list/remove_standing_order
│   │   │   │   ├── updates.py              # log_deal_update
│   │   │   │   ├── prefs.py                # update_briefing_preferences
│   │   │   │   └── agent_read.py           # 5 agent-only read tools
│   │   │   ├── resources.py                # ui:// resources → ui_dist/*.html
│   │   │   └── ui_dist/                    # (gen) single-file HTML built from ui/
│   │   ├── ui/                             # MCP Apps source (TypeScript + Preact)
│   │   │   ├── package.json
│   │   │   ├── tsconfig.json
│   │   │   ├── vite.config.ts              # multi-entry + vite-plugin-singlefile
│   │   │   ├── board.html · detail.html · confirm.html
│   │   │   └── src/
│   │   │       ├── bridge.ts               # ext-apps App wrapper: onResult, callTool
│   │   │       ├── tokens.css              # design tokens light/dark (DD §15.2)
│   │   │       ├── format.ts               # band labels, relative timers
│   │   │       ├── Board.tsx               # triage board
│   │   │       ├── Detail.tsx              # RIGS bars, evidence, plan tab
│   │   │       └── Confirm.tsx             # Approve / Not now, countdown
│   │   └── schemas/
│   │       └── tools.snapshot.json         # (gen) tools/list snapshot, diffed in CI
│   │
│   ├── auth_server/
│   │   ├── pyproject.toml                  # authlib, argon2-cffi, boto3 (KMS), starlette, jinja2
│   │   ├── run.sh
│   │   └── auth_server/
│   │       ├── app.py                      # routes + CSRF + rate limits
│   │       ├── metadata.py                 # RFC 8414 document
│   │       ├── authorize.py                # /authorize (PKCE S256 required, resource param)
│   │       ├── token.py                    # /token: authorization_code, refresh_token, client_credentials
│   │       ├── revoke.py                   # /revoke
│   │       ├── jwks.py                     # /jwks.json (KMS public keys by kid)
│   │       ├── signer.py                   # KmsSigner (aws) · FileSigner (local dev)
│   │       ├── clients.py                  # pre-registered clients (Alexa+, harness, dispatcher)
│   │       ├── storage.py                  # auth schema repos (codes, refresh families)
│   │       └── templates/
│   │           ├── login.html
│   │           └── consent.html
│   │
│   ├── workers/
│   │   ├── pyproject.toml
│   │   └── workers/
│   │       ├── dispatcher.py               # SQS handler: triage_job, slack_*, draft_followup
│   │       ├── watcher.py                  # 15-min recompute + rules + daily snapshot + purges
│   │       ├── sweeper.py                  # outbox re-enqueue
│   │       ├── canary.py                   # tools/list + get_risk_briefing with canary user
│   │       ├── migrator.py                 # one-shot Lambda: applies db/migrations in VPC
│   │       ├── slack.py                    # Block Kit builders + webhook client (429 aware)
│   │       ├── drafts.py                   # Haiku-class follow-up drafting (Bedrock Converse)
│   │       ├── agentcore_client.py         # invoke_agent_runtime + stream collection
│   │       ├── job_tokens.py               # client_credentials against auth-server
│   │       ├── outbox_publisher.py         # SQS sends for due outbox rows
│   │       └── memory_events.py            # writes outcome events to AgentCore Memory
│   │
│   ├── agents/
│   │   ├── Dockerfile                      # arm64 image for AgentCore Runtime (§10.3)
│   │   ├── pyproject.toml                  # strands-agents, bedrock-agentcore, mcp
│   │   └── agents/
│   │       ├── main.py                     # BedrockAgentCoreApp entrypoint
│   │       ├── lead.py                     # Triage Lead construction
│   │       ├── models.py                   # sonnet() / haiku() BedrockModel factories
│   │       ├── schemas.py                  # re-exports TriageOutput from watchfloor_core
│   │       ├── envelope.py                 # wrap_untrusted(text, source, id)
│   │       ├── memory.py                   # recall_preferences tool (AgentCore Memory)
│   │       ├── specialists/
│   │       │   ├── __init__.py             # make_specialist_tools(data_tools)
│   │       │   ├── analyst.py
│   │       │   ├── context.py
│   │       │   ├── strategist.py
│   │       │   └── critic.py
│   │       ├── prompts/
│   │       │   └── lead.md · analyst.md · context.md · strategist.md · critic.md
│   │       └── playbook.yaml               # P01–P12 (DD §10.8)
│   │
│   └── harness/                            # Path B + dev tool
│       ├── api/
│       │   ├── pyproject.toml              # fastapi, strands-agents, mcp
│       │   ├── run.sh
│       │   └── harness_api/
│       │       ├── app.py                  # POST /converse, GET /oauth/callback
│       │       ├── agent.py                # Strands agent + MCPClient (user token)
│       │       ├── pkce.py                 # public-client PKCE helper
│       │       └── prompts/alexa_style.md
│       └── web/
│           ├── package.json · vite.config.ts · index.html
│           └── src/
│               ├── main.ts                 # chat loop, latency drawer
│               ├── speech.ts               # Web Speech API in/out
│               ├── apphost.ts              # sandboxed iframe host for ui:// resources
│               └── banner.ts               # "Simulated Alexa+ experience" label
│
├── addon-package/                          # created by `alexa-ai new mcp`; we edit, never hand-create
│   ├── addon.json
│   └── assets/
│       ├── icons/light/{72,64,88,126,180,241}.png
│       ├── icons/dark/{72,64,88,126,180,241}.png
│       ├── carousel/01.png                 # 600×900
│       └── banner/01.png                   # 1200×600
│
├── db/
│   ├── migrate.py                          # applies migrations/*.sql in order; schema_migrations table
│   ├── migrations/
│   │   ├── 0001_init.sql                   # types, tables, indexes (DD §13.2)
│   │   ├── 0002_rls.sql                    # ENABLE/FORCE RLS + ws_isolation policies on 15 tables
│   │   ├── 0003_auth_schema.sql            # auth.clients, auth.auth_codes, auth.refresh_tokens, auth.credentials
│   │   └── 0004_roles_grants.sql           # wf_app, wf_auth, wf_readonly grants
│   └── seed/
│       ├── __main__.py                     # python -m db.seed --workspace ws_lumenridge --seed 42
│       ├── generator.py                    # background accounts/deals/activities (relative time)
│       ├── scenarios.py                    # Kestrel 71 · Harbor & Pine 66 · Orbital 58 · Northgate 18
│       ├── hostile_notes.py                # prompt-injection corpus for security evals
│       └── names.py                        # synthetic names/companies
│
├── infra/
│   ├── pyproject.toml                      # aws-cdk-lib, constructs, cdk-fck-nat
│   ├── app.py                              # CDK app entry; env from infra/config/<env>.yaml
│   ├── cdk.json
│   ├── config/
│   │   ├── dev.yaml
│   │   └── demo.yaml
│   ├── constructs/
│   │   ├── python_lambda.py                # uv-bundled Lambda + Powertools + LWA option
│   │   └── alarms.py                       # standard alarm factory
│   └── stacks/
│       ├── net.py · data.py · auth.py · mcp.py
│       └── async_.py · agents.py · harness.py · ops.py
│
├── scripts/
│   ├── dev_keys.py                         # generates local ES256 key pair (gitignored output)
│   ├── dev_token.py                        # mints a local JWT for curl testing (local/dev only)
│   ├── demo.py                             # reset · clock · watch-now · status
│   ├── invoke_migrator.py                  # invokes the migrator Lambda for an env
│   ├── invoke_seeder.py                    # invokes seeding in an env (demo/dev only)
│   ├── conformance_remote.py               # runs tests/conformance against a URL
│   └── latency_probe.py                    # N calls per tool → p50/p95/p99 table
│
└── tests/
    ├── conftest.py                         # pg container, migrated DB, seeded ws, ASGI client, token factory
    ├── unit/
    │   ├── test_rigs.py · test_rigs_properties.py · test_rigs_golden.py
    │   └── test_speech.py · test_tokens.py · test_rule_descriptions.py
    ├── conformance/
    │   ├── test_transport.py               # CF-01..CF-10
    │   ├── test_edge.py                    # CF-12, CF-13
    │   ├── test_tools_list.py              # CF-03, CF-04 + snapshot
    │   ├── test_prm.py                     # CF-14
    │   └── test_output_schemas.py          # CF-15
    ├── integration/
    │   ├── test_briefing.py · test_explain.py · test_actions.py
    │   └── test_rules_watcher.py · test_triage_jobs.py · test_outbox.py · test_rls.py
    ├── security/
    │   └── test_origin_host.py · test_token_replay.py · test_agent_scope.py · test_injection.py
    ├── evals/
    │   └── golden_triage.jsonl · run_triage_eval.py · utterances.csv · results/
    └── load/
        └── locustfile.py
```

### 5.1 Ownership and dependency rules

```mermaid
flowchart LR
  CORE["packages/core<br/>watchfloor_core"]
  MCP["services/mcp_server"]
  AUTH["services/auth_server"]
  WRK["services/workers"]
  AGT["services/agents"]
  HAR["services/harness"]
  INF["infra"]
  MCP --> CORE
  WRK --> CORE
  AGT -->|"models only (triage.py)"| CORE
  AUTH -->|"settings, db, ids"| CORE
  HAR -.->|"MCP over HTTP only"| MCP
  AGT -.->|"MCP over HTTP only"| MCP
  INF -.->|"packages"| MCP
  INF -.->|"packages"| AUTH
  INF -.->|"packages"| WRK
  INF -.->|"image"| AGT
  INF -.->|"packages"| HAR
```

| Rule | Enforced by |
|---|---|
| `watchfloor_core` imports nothing from `services/*` | ruff `flake8-tidy-imports` banned-api config |
| `rigs.py` has no I/O imports (no asyncpg, boto3, httpx) | Unit test inspects the module's imports |
| SQL lives only in `repos/` | Code review + a grep check in CI for `execute(` / `fetch(` outside `repos/` |
| Agents and the harness never import repos or db | Separate packages without that dependency |
| Tool handlers call `services/*`, never `repos/*` directly | Code review |

---

## 6. Module reference

### 6.1 `watchfloor_core` key interfaces

```python
# settings.py
class Settings(BaseModel):
    env: Literal["local", "dev", "demo"]
    database_url: str | None = None          # local
    db_secret_arn: str | None = None         # aws
    resource_uri: str                        # https://mcp.watchfloor.example/mcp
    issuer: str                              # https://auth.watchfloor.example
    jwks_url: str
    allowed_hosts: frozenset[str]
    allowed_origins: frozenset[str] = frozenset()
    protocol_versions: frozenset[str] = frozenset({"2025-11-25"})
    jobs_queue_url: str | None = None
    outbox_queue_url: str | None = None
    demo_mode: bool = False
    rate_limit_user: int = 60
    rate_limit_ws: int = 600

def get_settings() -> Settings: ...          # cached; reads WF_* env vars
```

```python
# db.py
async def open_pool(*, min_size: int = 1, max_size: int = 2) -> None: ...
async def close_pool() -> None: ...

@asynccontextmanager
async def ws_tx(workspace_id: str) -> AsyncIterator[asyncpg.Connection]:
    """Transaction with RLS scoped to one workspace. Fails closed if unset."""
    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.workspace_id', $1, true)", workspace_id)
            await conn.execute("SET LOCAL statement_timeout = '2s'")
            yield conn
```

```python
# clock.py
class Clock:
    def __init__(self, demo_mode: bool): self._demo = demo_mode
    def now(self, workspace_offset: timedelta | None = None) -> datetime:
        base = datetime.now(UTC)                       # the only allowed call site (ruff ban elsewhere)
        return base + workspace_offset if (self._demo and workspace_offset) else base
```

```python
# tokens.py
import base64, hashlib, secrets, rfc8785

TOKEN_PREFIX = "wf1."

def new_confirmation_token() -> tuple[str, bytes]:
    raw = secrets.token_bytes(32)
    token = TOKEN_PREFIX + base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return token, hash_token(token)

def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()

def params_hash(params: dict) -> bytes:
    return hashlib.sha256(rfc8785.dumps(params)).digest()   # RFC 8785 canonical JSON
```

```python
# speech.py  (implements delta T3)
def money_display(cents: int) -> str:
    dollars = cents / 100
    if dollars >= 1_000_000:
        return f"${dollars / 1_000_000:.1f}M".replace(".0M", "M")
    if dollars >= 1_000:
        return f"${round(dollars / 1_000):,}K"
    return f"${dollars:,.0f}"

def date_display(d: date) -> str:
    return f"{d:%A}, {d:%B} {d.day}"          # "Thursday, October 22"

# money_display(8_400_000) == "$84K" · money_display(850_000_000) == "$8.5M"
```

```python
# errors.py
class ErrorCode(StrEnum):
    DEAL_NOT_FOUND = "DEAL_NOT_FOUND"
    AMBIGUOUS_DEAL = "AMBIGUOUS_DEAL"
    AMBIGUOUS_ASSIGNEE = "AMBIGUOUS_ASSIGNEE"
    UNKNOWN_ASSIGNEE = "UNKNOWN_ASSIGNEE"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    TOKEN_ALREADY_USED = "TOKEN_ALREADY_USED"
    TOKEN_MISMATCH = "TOKEN_MISMATCH"
    INVALID_DATE = "INVALID_DATE"
    RULE_LIMIT_REACHED = "RULE_LIMIT_REACHED"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    JOB_LIMIT_REACHED = "JOB_LIMIT_REACHED"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"

class WatchfloorError(Exception):
    def __init__(self, code: ErrorCode, message: str, *, recovery: str | None = None,
                 suggestions: list[dict] | None = None): ...
    def to_payload(self) -> dict: ...         # {"error": {code, message, recovery, suggestions}}
```

```python
# services/briefing.py
async def build_briefing(conn, p: Principal, *, limit: int | None, segment: str | None,
                         now: datetime) -> BriefingOut:
    prefs = await repos.prefs.get(conn, p.user_id)
    last = await repos.prefs.last_briefing_at(conn, p.user_id)
    lim = limit or prefs.briefing_limit
    seg = None if segment == "all" else (segment or prefs.focus_segment)
    rows = await repos.scores.ranked_risks(conn, limit=lim, segment=seg, since=last)
    fired = await repos.rules.fired_since(conn, p.user_id, since=last)
    await repos.prefs.touch_last_briefing(conn, p.user_id, now)
    return BriefingOut.compose(rows, fired, include_amounts=prefs.include_amounts, now=now)
```

### 6.2 `mcp_server` key code

**Middleware order** (`middleware/__init__.py`):

```python
def build_middleware() -> list[Middleware]:
    s = get_settings()
    return [
        Middleware(Telemetry),                                   # 1 trace id + latency
        Middleware(EdgeGuard, hosts=s.allowed_hosts,             # 2 Host/Origin/Accept/body (T5)
                   origins=s.allowed_origins, max_body=65_536),
        Middleware(BearerAuth, issuer=s.issuer,                  # 3 401 without WWW-Authenticate
                   audience=s.resource_uri, jwks_url=s.jwks_url,
                   public_prefixes=("/.well-known/", "/healthz")),
        Middleware(ProtocolVersion, allowed=s.protocol_versions),# 4 400 unless initialize
        Middleware(RateLimit, per_user=s.rate_limit_user,        # 5 429
                   per_ws=s.rate_limit_ws),
    ]
```

**EdgeGuard** (pure ASGI, abridged):

```python
class EdgeGuard:
    def __init__(self, app, *, hosts, origins, max_body):
        self.app, self.hosts, self.origins, self.max_body = app, hosts, origins, max_body

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        h = Headers(scope=scope)
        if h.get("host", "").split(":")[0].lower() not in self.hosts:
            return await respond(send, 421)
        origin = h.get("origin")
        if origin is not None and origin not in self.origins:
            return await respond_jsonrpc_error(send, 403, -32600, "Origin not allowed")
        if scope["path"] == "/mcp" and scope["method"] == "POST":
            accept = h.get("accept", "")
            if "application/json" not in accept or "text/event-stream" not in accept:
                return await respond(send, 406)
            body = await read_body(receive, limit=self.max_body)   # None when over the limit
            if body is None:
                return await respond(send, 413)
            try:
                msg = orjson.loads(body)
            except orjson.JSONDecodeError:
                return await respond_jsonrpc_error(send, 400, -32700, "Parse error")
            state = scope.setdefault("state", {})
            state["jsonrpc_method"] = msg.get("method") if isinstance(msg, dict) else None
            receive = replay(body)                                   # hand body downstream
        await self.app(scope, receive, send)
```

**Endpoint wiring** (`app.py`):

```python
session_manager = build_session_manager(server)        # session.py

async def mcp_endpoint(scope, receive, send):
    if scope["method"] != "POST":                          # GET/DELETE → 405 (stateless)
        return await respond(send, 405, headers=[(b"allow", b"POST")])
    await session_manager.handle_request(scope, receive, send)

@asynccontextmanager
async def lifespan(_app):
    await open_pool()
    async with session_manager.run():
        yield
    await close_pool()

app = Starlette(
    routes=[
        Route("/.well-known/oauth-protected-resource", prm),
        Route("/.well-known/oauth-protected-resource/mcp", prm),
        Route("/healthz", healthz),
        Mount("/mcp", app=mcp_endpoint),
    ],
    middleware=build_middleware(),
    lifespan=lifespan,
)
```

```python
# session.py
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

def build_session_manager(server) -> StreamableHTTPSessionManager:
    return StreamableHTTPSessionManager(app=server, stateless=True, json_response=True)
```

> ⚠ **Verify (S5):** constructor arguments of `StreamableHTTPSessionManager` in the pinned SDK, and how `Mount("/mcp")` interacts with the trailing-slash redirect. If Starlette redirects `/mcp` → `/mcp/`, route `/mcp` through a thin ASGI dispatcher that checks `scope["path"]` instead. Conformance test CF-01 catches either way.

**Server and registry** (`server.py`, abridged):

```python
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.shared.exceptions import McpError
import mcp.types as types

server = Server("watchfloor", version=__version__, instructions=INSTRUCTIONS)

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    p = current_principal()
    return [spec.descriptor() for spec in REGISTRY.values() if spec.scope in p.scopes]

@server.call_tool(validate_input=False)          # we validate (CF-11 → -32602) ⚠ Verify flag name
async def call_tool(name: str, arguments: dict) -> types.CallToolResult:
    p = current_principal()
    spec = REGISTRY.get(name)
    if spec is None or spec.scope not in p.scopes:                     # delta T2
        raise McpError(types.ErrorData(code=types.INVALID_PARAMS, message=f"Unknown tool: {name}"))
    try:
        args = spec.input_model.model_validate(arguments or {})
    except ValidationError as e:
        raise McpError(types.ErrorData(code=types.INVALID_PARAMS, message=first_error(e)))

    t0 = time.perf_counter()
    async with ws_tx(p.workspace_id) as conn:
        ctx = ToolContext(principal=p, conn=conn, now=await workspace_now(conn, p))
        try:
            outcome = await spec.handler(ctx, args)
        except WatchfloorError as e:
            outcome = ToolOutcome.error(e)
        await repos.audit.record(conn, p, spec.name, arguments, outcome, ms_since(t0))
    await outcome.flush_after_commit()             # best-effort SQS sends after COMMIT
    metrics.add_metric("ToolLatencyMs", ms_since(t0), dims={"tool": spec.name})
    return outcome.to_call_tool_result()

@server.list_resources()
async def list_resources() -> list[types.Resource]:
    return UI_RESOURCES                             # 3 ui:// resources

@server.read_resource()
async def read_resource(uri) -> list[ReadResourceContents]:
    return [ReadResourceContents(content=load_ui(str(uri)), mime_type="text/html;profile=mcp-app")]
```

> ⚠ **Verify:** accepted return types of `call_tool` (direct `CallToolResult`), the `validate_input` parameter, and the `ReadResourceContents` import path in the pinned SDK. If the SDK always validates input, CF-11 asserts the SDK's behavior instead and the DD is updated.

**Principal propagation** (`context.py`): `BearerAuth` stores the `Principal` in `scope["state"]["principal"]` **and** in a `ContextVar`. `current_principal()` reads the ContextVar first, then falls back to `server.request_context.request.scope["state"]["principal"]` when the SDK exposes the HTTP request. Test CF-04 proves the agent scope sees exactly 6 tools, so a propagation failure can't go unnoticed.

**ToolSpec** (`tools/base.py`):

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    scope: str                                   # watchfloor.read | .write | .agent.read
    description: str                             # exact text from DD §6.3
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    annotations: types.ToolAnnotations
    handler: Callable[[ToolContext, BaseModel], Awaitable["ToolOutcome"]]
    ui_resource: str | None = None
    budget_ms: int = 150

    def descriptor(self) -> types.Tool:
        payload = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_model.model_json_schema(),
            "outputSchema": self.output_model.model_json_schema(),
            "annotations": self.annotations.model_dump(exclude_none=True),
        }
        if self.ui_resource:
            payload["_meta"] = {"ui": {"resourceUri": self.ui_resource}}   # ⚠ Verify key (S3)
        return types.Tool.model_validate(payload)

@dataclass
class ToolOutcome:
    structured: dict
    is_error: bool = False
    after_commit: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    @classmethod
    def error(cls, e: WatchfloorError) -> "ToolOutcome":
        return cls(e.to_payload(), is_error=True)

    async def flush_after_commit(self) -> None:
        for fn in self.after_commit:
            with suppress(Exception):              # sweeper re-sends anything missed
                await fn()

    def to_call_tool_result(self) -> types.CallToolResult:
        text = orjson.dumps(self.structured).decode()
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            structuredContent=self.structured,
            isError=self.is_error,
        )
```

**Example tool** (`tools/explain.py`):

```python
async def handle(ctx: ToolContext, args: ExplainIn) -> ToolOutcome:
    out = await services.explain.explain_deal(ctx.conn, ctx.principal, args.deal_id, now=ctx.now)
    return ToolOutcome(out.model_dump(mode="json"))

SPEC = ToolSpec(
    name="explain_deal_risk",
    scope=Scope.READ,
    description=DESCRIPTIONS["explain_deal_risk"],
    input_model=ExplainIn,
    output_model=ExplainOut,
    annotations=types.ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    handler=handle,
    ui_resource="ui://watchfloor/deal-detail",
)
```

### 6.3 Key SQL (`repos/`)

**Ranked risks** (`repos/scores.py`). RLS supplies the workspace filter:

```sql
SELECT d.id                               AS deal_id,
       d.name, a.name                     AS account,
       d.amount_cents, d.stage::text      AS stage,
       u.display_name                     AS owner,
       rs.score, rs.band::text            AS band,
       rs.reasons -> 0 ->> 'text'         AS top_reason,
       rs.band_since > COALESCE($2::timestamptz, '-infinity') AS new_since_last_briefing
FROM risk_scores rs
JOIN deals    d ON d.id = rs.deal_id
JOIN accounts a ON a.id = d.account_id
JOIN users    u ON u.id = d.owner_id
WHERE rs.band IN ('at_risk', 'watch')
  AND d.stage NOT IN ('won', 'lost')
  AND ($3::text IS NULL OR a.segment = $3)
ORDER BY (rs.band = 'at_risk') DESC,
         rs.score::bigint * d.amount_cents DESC
LIMIT $1;
```

**Standing-order match** (`repos/rules.py`). `$6` is "now" from the Clock, which keeps the demo offset deterministic:

```sql
SELECT d.id
FROM deals d
JOIN risk_scores rs ON rs.deal_id = d.id
JOIN accounts a     ON a.id = d.account_id
LEFT JOIN LATERAL (
  SELECT max(occurred_at) AS last_inbound
  FROM activities
  WHERE deal_id = d.id AND direction = 'inbound'
) li ON true
WHERE d.stage NOT IN ('won', 'lost')
  AND ($1::bigint       IS NULL OR d.amount_cents >= $1)
  AND ($2::int          IS NULL OR COALESCE(li.last_inbound, '-infinity') < $6::timestamptz - make_interval(days => $2))
  AND ($3::deal_stage[] IS NULL OR d.stage = ANY ($3))
  AND ($4::int          IS NULL OR rs.score >= $4)
  AND ($5::text         IS NULL OR a.segment = $5)
  AND NOT EXISTS (
        SELECT 1 FROM alerts al
        WHERE al.rule_id = $7 AND al.deal_id = d.id
          AND al.sent_at > $6::timestamptz - interval '24 hours');
```

**Token claim** (`repos/proposals.py`):

```sql
UPDATE action_proposals
SET status = 'executed', executed_at = $3
WHERE token_hash = $1
  AND user_id    = $2
  AND status     = 'pending'
  AND expires_at > $3
RETURNING *;
```

Zero rows → `get_by_token_hash()` to report `TOKEN_ALREADY_USED` (returning the stored `result`), `TOKEN_EXPIRED` or `TOKEN_MISMATCH`.

**Rate limit** (`repos/ratelimit.py`). The `rate_limits` table has no `workspace_id`, so RLS doesn't apply:

```sql
INSERT INTO rate_limits (subject, window_start, count)
VALUES ($1, date_trunc('minute', now()), 1)
ON CONFLICT (subject, window_start) DO UPDATE SET count = rate_limits.count + 1
RETURNING count;
```

### 6.4 Workers

```python
# workers/watcher.py (abridged)
@metrics.log_metrics
def handler(event, _ctx):
    return asyncio.run(run())

async def run() -> dict:
    await open_pool(min_size=1, max_size=1)
    stats = {"scored": 0, "alerts": 0}
    for ws in await repos.workspaces.all_ids():
        async with ws_tx(ws) as conn:
            now = await workspace_now(conn, ws)
            stats["scored"] += await services.scoring.recompute_workspace(conn, now=now)
            stats["alerts"] += await services.rules.evaluate_rules(conn, now=now)   # writes alerts + outbox
            await services.scoring.daily_snapshot_if_due(conn, now=now)
            await repos.maintenance.purge_old(conn, now=now)                        # audit/outbox 30 d
    await outbox_publisher.flush_due()                                              # SQS sends
    metrics.add_metric(name="WatcherAlertsQueued", unit=MetricUnit.Count, value=stats["alerts"])
    return stats
```

```python
# workers/slack.py: risk alert builder
def risk_alert_blocks(deal: AlertDeal, rule_desc: str | None) -> dict:
    reasons = "\n".join(f"• {escape(r)}" for r in deal.reasons[:3])
    ctx = f"Standing order: _{escape(rule_desc)}_ · Watchfloor" if rule_desc else "Watchfloor"
    return {
        "text": f"{deal.account} is {deal.band_label} ({deal.score})",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text",
             "text": f"{deal.band_label} · {deal.account} ({deal.score})"}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Deal*\n{escape(deal.name)}"},
                {"type": "mrkdwn", "text": f"*Amount*\n{deal.amount_display}"},
                {"type": "mrkdwn", "text": f"*Stage*\n{deal.stage_label}"},
                {"type": "mrkdwn", "text": f"*Owner*\n{escape(deal.owner)}"}]},
            {"type": "section", "text": {"type": "mrkdwn", "text": reasons}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": ctx}]},
        ],
    }
```

### 6.5 Agents

```python
# agents/models.py
def sonnet(temperature: float = 0.0, max_tokens: int = 2048) -> BedrockModel:
    return BedrockModel(model_id=os.environ["WF_SONNET_MODEL_ID"],      # inference profile id
                        temperature=temperature, max_tokens=max_tokens)  # never also set top_p

def haiku(temperature: float = 0.0, max_tokens: int = 1024) -> BedrockModel:
    return BedrockModel(model_id=os.environ["WF_HAIKU_MODEL_ID"],
                        temperature=temperature, max_tokens=max_tokens)
```

```python
# agents/specialists/__init__.py
def make_specialist_tools(data_tools: list) -> list:
    by_name = {t.tool_name: t for t in data_tools}          # ⚠ Verify attribute name (S6)

    analyst = Agent(model=haiku(), system_prompt=load_prompt("analyst"),
                    tools=[by_name[n] for n in ("get_deal_timeline", "get_deal_history", "explain_deal_risk")])
    context = Agent(model=haiku(), system_prompt=load_prompt("context"),
                    tools=[by_name["get_account_context"], by_name["get_prior_triage"], recall_preferences])
    strategist = Agent(model=sonnet(temperature=0.3), system_prompt=load_prompt("strategist"),
                       tools=[by_name["get_playbook"]])
    critic = Agent(model=sonnet(), system_prompt=load_prompt("critic"), tools=[])

    @tool
    def signal_analyst(deal_id: str) -> str:
        """Analyze a deal's activity timeline and field history. Returns Evidence JSON with ids."""
        return str(analyst(f"Analyze deal {deal_id}. Return Evidence JSON only."))

    @tool
    def account_context(deal_id: str) -> str:
        """Return account facts, stakeholders, prior triage outcomes and learned user preferences."""
        return str(context(f"Build context for deal {deal_id}. Return AccountFacts JSON only."))

    @tool
    def playbook_strategist(evidence_json: str, facts_json: str) -> str:
        """Map evidence to up to 3 playbook actions with rationale and evidence ids."""
        return str(strategist(f"EVIDENCE:\n{evidence_json}\nFACTS:\n{facts_json}\nReturn Actions JSON."))

    @tool
    def critic_review(draft_json: str, evidence_json: str) -> str:
        """Verify a draft TriageOutput against evidence. Returns {"ok": bool, "issues": [...]}."""
        return str(critic(f"DRAFT:\n{draft_json}\nEVIDENCE:\n{evidence_json}"))

    return [signal_analyst, account_context, playbook_strategist, critic_review]
```

```python
# agents/envelope.py
def wrap_untrusted(text: str, *, source: str, ref_id: str) -> str:
    safe = text.replace("</crm_data>", "&lt;/crm_data&gt;")[:4000]
    return f'<crm_data trust="untrusted" source="{source}" id="{ref_id}">\n{safe}\n</crm_data>'
```

The agent-only tools (`get_deal_timeline`) apply `wrap_untrusted` on the **server side** before returning bodies. Untrusted text therefore never reaches a model without the envelope, whichever agent asked.

### 6.6 MCP App bridge (TypeScript)

```ts
// ui/src/bridge.ts
import { App } from "@modelcontextprotocol/ext-apps";       // ⚠ Verify API surface (S3)

export type Structured = Record<string, unknown>;

export async function connect(onResult: (s: Structured) => void) {
  const app = new App({ name: "watchfloor-ui", version: "1.0.0" });
  app.ontoolresult = (r: { structuredContent?: Structured }) => {
    if (r.structuredContent) onResult(r.structuredContent);
  };
  await app.connect();
  return {
    call: (name: string, args: Record<string, unknown>) =>
      app.callServerTool({ name, arguments: args }),
  };
}
```

```ts
// ui/vite.config.ts
import { defineConfig } from "vite";
import preact from "@preact/preset-vite";
import { viteSingleFile } from "vite-plugin-singlefile";

export default defineConfig({
  plugins: [preact(), viteSingleFile()],
  build: {
    outDir: "../mcp_server/ui_dist",
    emptyOutDir: true,
    rollupOptions: { input: { board: "board.html", detail: "detail.html", confirm: "confirm.html" } },
  },
});
```

> `vite-plugin-singlefile` inlines best with a single entry per build. If multi-entry inlining misbehaves, build three times with `--mode board|detail|confirm` from a small npm script.

---

## 7. Configuration and secrets

### 7.1 Environment variables

| Variable | Used by | Example (dev) | Notes |
|---|---|---|---|
| `WF_ENV` | all | `dev` | `local` · `dev` · `demo` |
| `WF_DATABASE_URL` | local only | `postgresql://wf_app:devpassword@127.0.0.1:5432/watchfloor` | Never set in AWS |
| `WF_DB_SECRET_ARN` | aws services | `arn:aws:secretsmanager:…:secret:wf-db-…` | RDS-managed secret |
| `WF_RESOURCE_URI` | mcp, auth | `https://mcp.dev.watchfloor.example/mcp` | Must equal token `aud` **exactly** |
| `WF_ISSUER` | mcp, auth | `https://auth.dev.watchfloor.example` | |
| `WF_JWKS_URL` | mcp | `https://auth.dev.watchfloor.example/jwks.json` | Cached 10 min |
| `WF_ALLOWED_HOSTS` | mcp | `mcp.dev.watchfloor.example,abc123.execute-api.us-east-1.amazonaws.com` | Include the execute-api host |
| `WF_ALLOWED_ORIGINS` | mcp | `https://harness.dev.watchfloor.example` | Absent Origin is allowed (server-to-server) |
| `WF_PROTOCOL_VERSIONS` | mcp | `2025-11-25` | Comma list |
| `WF_JOBS_QUEUE_URL` / `WF_OUTBOX_QUEUE_URL` | mcp, workers | `https://sqs.us-east-1.amazonaws.com/…/wf-jobs` | |
| `WF_DEMO_MODE` | mcp, workers | `false` (`true` on demo only) | Enables clock offsets |
| `WF_RATE_LIMIT_USER` / `WF_RATE_LIMIT_WS` | mcp | `60` / `600` | Per minute |
| `WF_KMS_KEY_ID` | auth | `alias/wf-jwt-signing` | ES256 |
| `WF_DEV_SIGNING_KEY_PATH` | auth (local) | `.secrets/dev_es256.pem` | Gitignored |
| `WF_ACCESS_TTL_S` / `WF_REFRESH_TTL_DAYS` | auth | `3600` / `30` | |
| `WF_AGENT_RUNTIME_ARN` | dispatcher | `arn:aws:bedrock-agentcore:…:runtime/wf-triage-…` | |
| `WF_MEMORY_ID` | dispatcher, agents | `wf-mem-…` | |
| `WF_SONNET_MODEL_ID` / `WF_HAIKU_MODEL_ID` | agents, dispatcher, harness | inference profile IDs | ⚠ Verify (S6) |
| `WF_SLACK_SECRET_ARN` | dispatcher | `arn:…:secret:wf-slack-…` | Webhook URL |
| `WF_SLACK_WEBHOOK_URL` | local only | (empty → messages printed to console) | Never set in AWS |
| `WF_DISPATCHER_CLIENT_SECRET_ARN` | dispatcher | `arn:…:secret:wf-dispatcher-client-…` | client_credentials |
| `WF_MCP_URL` | agents, harness, canary | `https://mcp.dev.watchfloor.example/mcp` | |
| `WF_HARNESS_CLIENT_ID` | harness | `wf-harness` | Public PKCE client |
| `POWERTOOLS_SERVICE_NAME` | all Lambdas | `mcp-server` | |
| `POWERTOOLS_METRICS_NAMESPACE` | all Lambdas | `Watchfloor` | |
| `LOG_LEVEL` | all | `INFO` | |
| `AWS_LAMBDA_EXEC_WRAPPER` | mcp, auth, harness | `/opt/bootstrap` | Lambda Web Adapter |
| `AWS_LWA_PORT` | mcp, auth, harness | `8080` | |
| `AWS_LWA_READINESS_CHECK_PATH` | mcp, auth, harness | `/healthz` | |

### 7.2 Configuration files

**`packages/core/watchfloor_core/config/rigs.yaml`**

```yaml
model_version: rigs-1.1
weights: { r: 0.30, i: 0.25, g: 0.25, s: 0.20 }
bands:
  at_risk: { enter: 65, exit_below: 60 }
  watch:   { enter: 40, exit_below: 36 }
engagement_points: { email_inbound: 1.0, meeting: 2.0, call_inbound: 1.5, doc_view: 0.2, doc_view_cap: 10 }
stages:
  discovery:   { norm_days: 10, intent_baseline: 3.0 }
  demo:        { norm_days: 8,  intent_baseline: 3.5 }
  proposal:    { norm_days: 6,  intent_baseline: 4.0 }
  negotiation: { norm_days: 4,  intent_baseline: 5.0 }
  closing:     { norm_days: 3,  intent_baseline: 5.0 }
slippage: { push: 0.35, stage_regression: 0.40, amount_cut: 0.25, amount_cut_threshold: 0.15,
            magnitude_kicker: 0.10, magnitude_days: 30, window_days: 90 }
gaps: { per_gap: 0.25, overdue_grace_days: 2 }
```

**`addon-package/addon.json`** (values to fill in. Keep the structure the CLI generates; the shape below is abridged and illustrative):

```json
{
  "manifestVersion": "1.0",
  "distributionCountries": ["US"],
  "name": { "value": "Watchfloor" },
  "shortDescription": "Hear which deals are at risk, why, and act by voice. Nothing happens without your yes.",
  "fullDescription": "Watchfloor watches your B2B sales pipeline … (≤ 4000 chars)",
  "examplePhrases": [
    "What's at risk in my pipeline?",
    "Why is Kestrel at risk?",
    "Dig into Kestrel",
    "From now on, flag deals over 50K that go quiet for five days"
  ],
  "privacyPolicyUrl": "https://watchfloor.example/privacy",
  "termsOfUseUrl": "https://watchfloor.example/terms",
  "integrations": [{ "type": "MCP" }],
  "endpoints": { "default": { "type": "HTTPS", "uri": "https://mcp.watchfloor.example/mcp" } }
}
```

Constraints from the Alexa+ quickstart: name ≤ 30 chars, short description ≤ 123, full ≤ 4000, 3–4 example phrases ≤ 200 chars each, icons in all six sizes, at least one 600×900 carousel image, HTTPS URLs only, and PNG/JPG/JPEG/WEBP images.

### 7.3 Secrets inventory

| Secret | Where | Created by | Rotation |
|---|---|---|---|
| DB master + app credentials | Secrets Manager (RDS-managed) | `Data` stack | Managed |
| JWT signing key | KMS asymmetric (ECC_NIST_P256) | `Auth` stack | Runbook §17.4 |
| Slack webhook URL | Secrets Manager `wf/slack` | Manual (`aws secretsmanager put-secret-value`) | On leak |
| Dispatcher client secret | Secrets Manager `wf/dispatcher-client` | `scripts/` bootstrap | 30 days |
| Alexa+ client secret (if confidential) | `auth.clients` (Argon2id hash) | Admin script | On demand |
| Demo judge credentials | Devpost testing instructions only | Manual | After Nov 20 |

---

## 8. Local development

### 8.1 Prerequisites

| Tool | Purpose |
|---|---|
| Python 3.12 + uv | Services and tests |
| Node 20 LTS + npm | MCP App UI, harness web |
| Docker Desktop | Postgres, testcontainers |
| AWS CLI v2 + CDK CLI (`npm i -g aws-cdk`) | Deploys |
| cloudflared | Public tunnel for the Alexa+ development stage |
| Alexa AI CLI (`alexa-ai`) | Add-on scaffold and deploy (per Amazon's setup page) |
| MCP Inspector (`npx @modelcontextprotocol/inspector`) | Manual protocol testing |

### 8.2 First run (about 10 minutes)

```bash
git clone https://github.com/<you>/watchfloor.git && cd watchfloor
```

```bash
cp .env.example .env && make bootstrap
```

```bash
make db-up && make migrate && make seed
```

```bash
make dev-keys && make dev
```

`make bootstrap` runs `uv sync`, `npm ci` in the UI and harness folders, and installs pre-commit hooks. `make dev` starts the auth server on `127.0.0.1:8081` and the MCP server on `127.0.0.1:8080` with auto-reload.

### 8.3 Smoke test with curl

```bash
export TOKEN=$(uv run python scripts/dev_token.py --user priya --scopes "watchfloor.read watchfloor.write")
```

```bash
curl -sS -X POST http://127.0.0.1:8080/mcp -H "Host: localhost" -H "Authorization: Bearer $TOKEN" -H "Accept: application/json, text/event-stream" -H "Content-Type: application/json" -H "MCP-Protocol-Version: 2025-11-25" -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_risk_briefing","arguments":{}}}' | jq .result.structuredContent.summary
```

Expected: `"3 deals need attention … Top risk: Kestrel Biotech, $84K, silent for 16 days."`

### 8.4 MCP Inspector

```bash
npx @modelcontextprotocol/inspector
```

Connect with transport **Streamable HTTP** to `http://127.0.0.1:8080/mcp` and add the header `Authorization: Bearer $TOKEN`. Then check that `tools/list` returns 14 tools and that each `outputSchema` validates.

### 8.5 Exposing the local server to Alexa+ (development stage)

```bash
cloudflared tunnel --url http://127.0.0.1:8080
```

A quick tunnel gives a random `https://<name>.trycloudflare.com` URL. Set `WF_RESOURCE_URI` and `WF_ALLOWED_HOSTS` to match, restart `make dev`, then follow §14. Because OAuth audiences must match the public URL exactly, **prefer deploying `dev` to AWS by Slice 1** and use tunnels only for the Slice 0 hello-world. **⚠ Verify (S1/S2)** whether Alexa+ accepts an MCP server during development before account linking is configured.

### 8.6 Daily loop

| Task | Command |
|---|---|
| Run unit + conformance tests | `make test` |
| Full suite incl. integration (Docker) | `make test-all` |
| Lint + types | `make lint` |
| Rebuild UI resources | `make ui` |
| Regenerate the tool schema snapshot (intentional change) | `make snapshot` → then `make addon-deploy` |
| Reset the demo workspace | `make demo-reset` |
| Probe latency against a URL | `make latency URL=https://mcp.dev.watchfloor.example/mcp` |

---

## 9. Database operations

### 9.1 Migrations

- Plain SQL files in `db/migrations/NNNN_name.sql`, applied in order by `db/migrate.py`. Each runs in a transaction and is recorded in `schema_migrations(version, applied_at, checksum)`.
- **Forward-only.** A fix is a new migration. Checksums are verified, and editing an applied file fails CI.
- In AWS, migrations run from the one-shot `migrator` Lambda (inside the VPC), invoked by `deploy-dev.yml` and `release-demo.yml` after `cdk deploy`. The DB is never public.

```bash
make migrate ENV=local
```

```bash
make migrate-remote ENV=dev
```

### 9.2 Roles and RLS checks

```sql
-- Verify FORCE RLS is on everywhere it should be
SELECT relname, relrowsecurity, relforcerowsecurity
FROM pg_class
WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'
ORDER BY relname;
```

Expect `relrowsecurity` and `relforcerowsecurity` to be true for the 15 tenant tables. `workspaces`, `rate_limits` and `schema_migrations` have no RLS by design. `workspaces` access is limited to the `wf_app` role reading its own row via the principal.

### 9.3 Seeding

```bash
uv run python -m db.seed --workspace ws_lumenridge --seed 42 --relative-now
```

Assertions run at the end of the seed and fail loudly if the scenario scores drift: Kestrel **71** At Risk, Harbor & Pine **66** At Risk, Orbital Dental **58** Watch, Northgate **18** Healthy.

### 9.4 Performance hygiene

- `EXPLAIN (ANALYZE, BUFFERS)` for `ranked_risks`, the rule match and `load_snapshot` is checked into `docs/perf/` at each release. Ranked risks must stay under 5 ms on the seed.
- `pg_stat_statements` is enabled on RDS through the parameter group. Review the top 10 statements before recording.

---

## 10. Build and packaging

### 10.1 Lambda zip packages (uv)

Each Lambda is a zip built by the `PythonLambda` CDK construct:

1. `uv export --package <svc> --frozen --no-dev --no-editable > requirements.txt`
2. `uv pip install --target build/<svc> --python-platform aarch64-manylinux2014 --python-version 3.12 -r requirements.txt`
3. Copy the service source and `run.sh` (for LWA services).
4. Strip `__pycache__`, tests and other non-runtime files. The MCP server zip target is under 25 MB unzipped, which keeps cold starts low.

### 10.2 Lambda Web Adapter services

`mcp-server`, `auth-server` and `harness-api` run Uvicorn behind the Lambda Web Adapter layer. Each sets handler `run.sh`, `AWS_LAMBDA_EXEC_WRAPPER=/opt/bootstrap`, `AWS_LWA_PORT=8080` and `AWS_LWA_READINESS_CHECK_PATH=/healthz`. The LWA arm64 layer ARN is pinned in `infra/config/*.yaml`. **⚠ Verify** the current layer version.

```bash
#!/bin/sh
# services/mcp_server/run.sh
exec python -m uvicorn mcp_server.app:app --host 127.0.0.1 --port 8080 --no-access-log --lifespan on
```

### 10.3 Agent container (AgentCore Runtime)

```dockerfile
# services/agents/Dockerfile
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=ghcr.io/astral-sh/uv:<pinned-version> /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/core ./packages/core
COPY services/agents ./services/agents
RUN uv sync --frozen --no-dev --package watchfloor-agents
RUN useradd -r -u 10001 agent && chown -R agent /app
USER agent
EXPOSE 8080
CMD ["uv", "run", "--no-sync", "python", "-m", "agents.main"]
```

`BedrockAgentCoreApp` serves the runtime contract (`/invocations`, `/ping`) on port 8080. Build and push with `make agents-image ENV=dev` (buildx `--platform linux/arm64`), then deploy via the `Agents` stack. **⚠ Verify (S6):** CDK resource support for AgentCore Runtime. The fallback is the AgentCore starter toolkit CLI, invoked from a `make agents-deploy` target.

### 10.4 UI builds

```bash
make ui
```

This runs `npm run build` in `services/mcp_server/ui`, writes `ui_dist/{board,detail,confirm}.html`, and checks each file is ≤ 150 KB gzipped with no `http(s)://` references (a grep in CI). The harness web app builds to `services/harness/web/dist` and is synced to S3 by the `Harness` stack.

---

## 11. Infrastructure as code (CDK)

### 11.1 App composition

```python
# infra/app.py
app = cdk.App()
env_name = app.node.try_get_context("env") or "dev"
cfg = load_config(env_name)                              # infra/config/<env>.yaml
aws_env = cdk.Environment(account=cfg.account, region="us-east-1")

net     = NetStack(app,     f"wf-{env_name}-net",     cfg=cfg, env=aws_env)
data    = DataStack(app,    f"wf-{env_name}-data",    cfg=cfg, net=net, env=aws_env)
auth    = AuthStack(app,    f"wf-{env_name}-auth",    cfg=cfg, net=net, data=data, env=aws_env)
mcp     = McpStack(app,     f"wf-{env_name}-mcp",     cfg=cfg, net=net, data=data, auth=auth, env=aws_env)
asyncs  = AsyncStack(app,   f"wf-{env_name}-async",   cfg=cfg, net=net, data=data, mcp=mcp, env=aws_env)
agents  = AgentsStack(app,  f"wf-{env_name}-agents",  cfg=cfg, mcp=mcp, asyncs=asyncs, env=aws_env)
harness = HarnessStack(app, f"wf-{env_name}-harness", cfg=cfg, mcp=mcp, auth=auth, env=aws_env)
ops     = OpsStack(app,     f"wf-{env_name}-ops",     cfg=cfg, mcp=mcp, asyncs=asyncs, env=aws_env)
cdk.Tags.of(app).add("project", "watchfloor")
cdk.Tags.of(app).add("env", env_name)
app.synth()
```

### 11.2 Stack dependency graph

```mermaid
flowchart LR
  NET["Net<br/>VPC · fck-nat · SGs"] --> DATA["Data<br/>RDS · secrets"]
  NET --> AUTH
  DATA --> AUTH["Auth<br/>KMS · λ auth · API"]
  DATA --> MCP
  AUTH --> MCP["Mcp<br/>λ mcp · alias · API"]
  MCP --> ASYNC["Async<br/>SQS · λ workers · rules"]
  DATA --> ASYNC
  MCP --> AGENTS["Agents<br/>ECR · Runtime · Memory"]
  ASYNC --> AGENTS
  MCP --> HARNESS["Harness<br/>S3 · CloudFront · λ api"]
  AUTH --> HARNESS
  MCP --> OPS["Ops<br/>dashboard · alarms · canary · budgets"]
  ASYNC --> OPS
```

### 11.3 Network stack (excerpt)

```python
from cdk_fck_nat import FckNatInstanceProvider       # ⚠ Verify package/API

nat = FckNatInstanceProvider(instance_type=ec2.InstanceType("t4g.nano"))
self.vpc = ec2.Vpc(self, "Vpc",
    ip_addresses=ec2.IpAddresses.cidr("10.20.0.0/16"),
    max_azs=2,
    nat_gateway_provider=nat,
    nat_gateways=1,
    subnet_configuration=[
        ec2.SubnetConfiguration(name="public",  subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24),
        ec2.SubnetConfiguration(name="private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, cidr_mask=22),
    ],
    gateway_endpoints={"S3": ec2.GatewayVpcEndpointOptions(service=ec2.GatewayVpcEndpointAwsService.S3)},
)
nat.security_group.add_ingress_rule(ec2.Peer.ipv4(self.vpc.vpc_cidr_block), ec2.Port.all_traffic())
self.lambda_sg = ec2.SecurityGroup(self, "LambdaSg", vpc=self.vpc, allow_all_outbound=True)
self.db_sg = ec2.SecurityGroup(self, "DbSg", vpc=self.vpc, allow_all_outbound=False)
self.db_sg.add_ingress_rule(self.lambda_sg, ec2.Port.tcp(5432), "Lambdas to Postgres")
```

### 11.4 MCP stack (excerpt)

```python
fn = PythonLambda(self, "McpFn",
    package="watchfloor-mcp-server", entry_script="run.sh",
    memory_size=1024, timeout=Duration.seconds(10), architecture=lambda_.Architecture.ARM_64,
    vpc=net.vpc, security_groups=[net.lambda_sg], layers=[lwa_layer(self, cfg)],
    environment={**common_env(cfg), **lwa_env(),
                 "POWERTOOLS_SERVICE_NAME": "mcp-server",
                 "WF_ALLOWED_HOSTS": ",".join(cfg.mcp_hosts)},
    reserved_concurrent_executions=20)
data.db_secret.grant_read(fn)
self.function = fn                                     # queue send grants are added in the Async stack

alias = lambda_.Alias(self, "Live", alias_name="live",
                      version=fn.current_version, provisioned_concurrent_executions=1)

api = apigwv2.HttpApi(self, "McpApi", api_name=f"wf-{cfg.env}-mcp",
                      default_domain_mapping=domain_mapping(self, cfg.mcp_domain))
integ = integrations.HttpLambdaIntegration("McpInteg", alias)
api.add_routes(path="/mcp", methods=[apigwv2.HttpMethod.POST, apigwv2.HttpMethod.GET,
                                     apigwv2.HttpMethod.DELETE], integration=integ)
api.add_routes(path="/.well-known/{proxy+}", methods=[apigwv2.HttpMethod.GET], integration=integ)
api.add_routes(path="/healthz", methods=[apigwv2.HttpMethod.GET], integration=integ)

stage = api.default_stage.node.default_child          # escape hatch for throttling
stage.default_route_settings = apigwv2.CfnStage.RouteSettingsProperty(
    throttling_burst_limit=50, throttling_rate_limit=25)
```

### 11.5 Async stack (excerpt)

```python
jobs_dlq = sqs.Queue(self, "JobsDlq", retention_period=Duration.days(14))
jobs = sqs.Queue(self, "Jobs", visibility_timeout=Duration.seconds(180),
                 dead_letter_queue=sqs.DeadLetterQueue(queue=jobs_dlq, max_receive_count=3))
outbox_dlq = sqs.Queue(self, "OutboxDlq", retention_period=Duration.days(14))
outbox = sqs.Queue(self, "Outbox", visibility_timeout=Duration.seconds(130),
                   dead_letter_queue=sqs.DeadLetterQueue(queue=outbox_dlq, max_receive_count=5))

dispatcher = PythonLambda(self, "Dispatcher", package="watchfloor-workers",
    handler="workers.dispatcher.handler", timeout=Duration.seconds(120), memory_size=512,
    reserved_concurrent_executions=3, vpc=net.vpc, security_groups=[net.lambda_sg],
    environment={...})
dispatcher.add_event_source(sources.SqsEventSource(jobs, batch_size=1))
dispatcher.add_event_source(sources.SqsEventSource(outbox, batch_size=1))
dispatcher.add_to_role_policy(iam.PolicyStatement(
    actions=["bedrock-agentcore:InvokeAgentRuntime"], resources=[cfg.agent_runtime_arn_pattern]))

watcher = PythonLambda(self, "Watcher", package="watchfloor-workers",
                       handler="workers.watcher.handler", timeout=Duration.seconds(60), ...)
events.Rule(self, "Every15", schedule=events.Schedule.rate(Duration.minutes(15)),
            targets=[targets.LambdaFunction(watcher)])
sweeper = PythonLambda(self, "Sweeper", package="watchfloor-workers",
                       handler="workers.sweeper.handler", timeout=Duration.seconds(30), ...)
events.Rule(self, "Every1", schedule=events.Schedule.rate(Duration.minutes(1)),
            targets=[targets.LambdaFunction(sweeper)])

jobs.grant_send_messages(mcp.function)
outbox.grant_send_messages(mcp.function)
outbox.grant_send_messages(watcher)
outbox.grant_send_messages(sweeper)
```

> SQS visibility timeouts must be ≥ the consuming Lambda's timeout. The dispatcher consumes both queues with a 120 s timeout, so `jobs` is 180 s and `outbox` is 130 s.

### 11.6 IAM least-privilege matrix

| Principal | Allowed |
|---|---|
| `mcp-server` role | `secretsmanager:GetSecretValue` (DB secret) · `sqs:SendMessage` (jobs, outbox) · CloudWatch/X-Ray write |
| `auth-server` role | DB secret · `kms:Sign`, `kms:GetPublicKey` on the signing key |
| `dispatcher` role | DB secret · Slack and client secrets · `sqs:ReceiveMessage/DeleteMessage` · `bedrock-agentcore:InvokeAgentRuntime` (one runtime) · `bedrock:InvokeModel` / Converse (Haiku-class profile, for drafts) · AgentCore Memory event write |
| `watcher` / `sweeper` | DB secret · `sqs:SendMessage` (outbox) |
| `migrator` | DB owner secret (migrations only) |
| AgentCore runtime role | `bedrock:InvokeModel*` on 2 inference profiles · AgentCore Memory read · ECR pull · logs. **No** DB and **no** VPC |
| GitHub OIDC deploy role | `cdk deploy` permissions scoped to `wf-*` stacks via the CDK bootstrap roles |

---

## 12. CI/CD pipelines

### 12.1 `ci.yml` (pull requests)

```yaml
name: ci
on: [pull_request]
permissions: { contents: read }
jobs:
  python:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --frozen
      - run: uv run ruff check . && uv run ruff format --check .
      - run: uv run pyright packages/core services
      - run: uv run pytest tests/unit tests/conformance -q
      - name: Tool schema snapshot unchanged
        run: |
          uv run python -m mcp_server.tools.snapshot --write /tmp/tools.json
          diff -u services/mcp_server/schemas/tools.snapshot.json /tmp/tools.json || {
            echo "::error::Tool schemas changed. Run 'make snapshot' and 'make addon-deploy'."; exit 1; }
  ui:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 20 }
      - run: npm ci && npm run build
        working-directory: services/mcp_server/ui
      - run: "! grep -E 'https?://' services/mcp_server/mcp_server/ui_dist/*.html"
  synth:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - uses: actions/setup-node@v4
        with: { node-version: 20 }
      - run: npm i -g aws-cdk && uv sync --frozen
      - run: cdk synth -c env=dev --quiet
        working-directory: infra
```

### 12.2 `deploy-dev.yml` (main)

```yaml
name: deploy-dev
on: { push: { branches: [main] } }
permissions: { contents: read, id-token: write }
concurrency: deploy-dev
jobs:
  integration:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --frozen && uv run pytest tests/integration tests/security -q
  deploy:
    needs: integration
    runs-on: ubuntu-latest
    environment: dev
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with: { role-to-assume: "${{ secrets.AWS_DEPLOY_ROLE_DEV }}", aws-region: us-east-1 }
      - uses: astral-sh/setup-uv@v5
      - uses: actions/setup-node@v4
        with: { node-version: 20 }
      - run: npm i -g aws-cdk && uv sync --frozen && (cd services/mcp_server/ui && npm ci) && make ui
      - run: cdk deploy --all -c env=dev --require-approval never
        working-directory: infra
      - run: make migrate-remote ENV=dev && make seed-remote ENV=dev
      - run: uv run python scripts/conformance_remote.py --url "${{ vars.MCP_URL_DEV }}"
```

### 12.3 `release-demo.yml` (tags)

Same as `deploy-dev` against `env=demo`, plus:
1. `uv run python tests/evals/run_triage_eval.py --env demo --out tests/evals/results/${GITHUB_REF_NAME}.json`, which fails on any pass threshold (DD §22.5).
2. `uv run python scripts/latency_probe.py --url $MCP_URL_DEMO --n 200`, which fails if p95 > 150 ms.
3. Local Inspector run with its report uploaded as an artifact. **⚠ Verify** the command name in the Alexa+ toolkit docs.
4. A manual approval gate (GitHub environment protection) before any `alexa-ai deploy` (§14).

---

## 13. API and protocol reference

### 13.1 HTTP surface

| Host | Method + path | Auth | Purpose |
|---|---|---|---|
| mcp | `POST /mcp` | Bearer | MCP JSON-RPC |
| mcp | `GET` · `DELETE /mcp` | — | 405 |
| mcp | `GET /.well-known/oauth-protected-resource[/mcp]` | — | PRM |
| mcp | `GET /healthz` | — | Liveness + DB ping |
| auth | `GET /.well-known/oauth-authorization-server` | — | RFC 8414 |
| auth | `GET /authorize` | session cookie | Code flow (PKCE S256 required) |
| auth | `POST /token` | client auth or PKCE | Code, refresh, client_credentials |
| auth | `POST /revoke` | client auth | Refresh revocation |
| auth | `GET /jwks.json` | — | Public keys |
| auth | `GET/POST /login` · `/consent` | CSRF | Server-rendered pages |
| harness | `POST /converse` | harness session | Path B chat turn |

### 13.2 Headers

| Header | Direction | Rule |
|---|---|---|
| `Authorization: Bearer <jwt>` | → mcp | Required on `/mcp` |
| `Accept: application/json, text/event-stream` | → mcp | Both media types required (406 otherwise) |
| `Content-Type: application/json` | → mcp | Required |
| `MCP-Protocol-Version: 2025-11-25` | → mcp | Required except on `initialize` (400) |
| `Origin` | → mcp | If present, must be allowlisted (403) |
| `MCP-Session-Id` | ← mcp | **Never issued** (stateless) |
| `WWW-Authenticate` | ← mcp | **Never sent** on 401 (Alexa+ constraint) |
| `Retry-After` | ← mcp | On 429 |
| `traceparent` | ↔ | Echoed for correlation |

### 13.3 Tool quick reference

Full schemas live in DD §6.4 and the generated `schemas/tools.snapshot.json`.

| Tool | Scope | Required args | Returns (key fields) |
|---|---|---|---|
| `get_risk_briefing` | read | — | `summary`, `all_clear`, `deals[]`, `triggered_rules[]` |
| `explain_deal_risk` | read | `deal_id` | `risk{score,band,components}`, `reasons[3]`, `trend_14d` |
| `lookup_deal` | read | `name_query` | `candidates[≤3]` |
| `start_deep_triage` | read | `deal_id` | `job_id`, `eta_seconds`, `deduplicated` |
| `get_triage_result` | read | `job_id` or `deal_id` | `status`, `voice_summary`, `drivers`, `recommended_actions` |
| `propose_action` | read | `deal_id`, `action_type` (+ per type) | `preview`, `confirmation_token`, `expires_at` |
| `list_standing_orders` | read | — | `rules[]` |
| `confirm_action` | write | `confirmation_token` | `status`, `result`, `audit_id` |
| `set_standing_order` | write | ≥ 1 condition | `rule_id`, `description` |
| `remove_standing_order` | write | `rule_id` | `removed` |
| `log_deal_update` | write | `deal_id` | `applied_fields`, `score_before/after`, `pending_proposal?` |
| `update_briefing_preferences` | write | ≥ 1 field | `preferences`, `summary` |
| `get_deal_timeline` | agent | `deal_id` | Activities with untrusted envelopes |
| `get_deal_history` | agent | `deal_id` | Field changes |
| `get_account_context` | agent | `account_id` | Account, contacts (no PII channels), history |
| `get_prior_triage` | agent | `deal_id` | Last ≤ 3 results + outcomes |
| `get_playbook` | agent | — | P01–P12 |

### 13.4 Status and error mapping

| Situation | HTTP | JSON-RPC | Tool result |
|---|---|---|---|
| Bad Host / Origin | 421 / 403 | (optional error, no id) | — |
| Missing Accept types | 406 | — | — |
| Body > 64 KiB / not JSON | 413 / 400 | `-32700` | — |
| No or invalid token | 401 (no `WWW-Authenticate`) | — | — |
| Unsupported protocol version | 400 | — | — |
| Rate limited | 429 + `Retry-After` | — | — |
| Notification or response accepted | 202 | — | — |
| Unknown method | 200 | `-32601` | — |
| Unknown or out-of-scope tool | 200 | `-32602` "Unknown tool" | — |
| Invalid arguments | 200 | `-32602` (field path) | — |
| Business failure (not found, expired…) | 200 | — | `isError: true` + `error{code,…}` |
| Unexpected server error | 200 | `-32603` (no internals) | — |

### 13.5 Remote test commands

```bash
curl -sS https://mcp.dev.watchfloor.example/.well-known/oauth-protected-resource | jq .
```

```bash
curl -sS -o /dev/null -w "%{http_code}\n" -X POST https://mcp.dev.watchfloor.example/mcp -H "Origin: https://evil.example" -H "Accept: application/json, text/event-stream" -H "Content-Type: application/json" -d '{}'
```

Expected: `403`.

```bash
curl -sS -D - -o /dev/null -X POST https://mcp.dev.watchfloor.example/mcp -H "Accept: application/json, text/event-stream" -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Expected: `HTTP/2 401` and **no** `www-authenticate` header.

---

## 14. Alexa+ add-on lifecycle

Commands follow Amazon's pages "Set Up Your Development Environment", the MCP toolkit quickstart,
"Account Linking for MCP Add-ons" and "Local Inspector". Flags beyond those documented there are not
assumed.

**Prerequisites.** macOS or Ubuntu (on Windows, Ubuntu under WSL), Node.js 24+, AWS CLI v2, and an
AWS account that Amazon has allowlisted for the toolkit (the one given to the Alexa Solutions
Architect). The CLI is in Amazon's private npm registry, reached through a role in Amazon's account
`372468808636`:

1. In the allowlisted AWS account, create an IAM user (e.g. `alexa-ai-tools`) with access keys and
   one inline policy: `sts:AssumeRole` on `arn:aws:iam::372468808636:role/AddOn3PDeveloperToolsRead`.
   It needs nothing else: Watchfloor is not a Category Action add-on, so no CDK runs in this flow.
2. `aws configure --profile alexa-ai-user` with those keys; then a profile `alexa-ai` that assumes
   the role with `source_profile = alexa-ai-user` and `region = us-west-2`.
   `aws sts get-caller-identity --profile alexa-ai` must show account `372468808636`.
3. Git credential helper for `https://git-codecommit.us-west-2.amazonaws.com` with
   `aws codecommit credential-helper --profile alexa-ai`, and `UseHttpPath = true` for that host.
4. `aws codeartifact login --tool npm --domain alexa-ai --repository npm-packages
   --domain-owner 372468808636 --namespace @alexa-ai --region us-west-2 --profile alexa-ai`.
5. `npm install -g @alexa-ai/cli`, then `alexa-ai --version`.

**Lifecycle.**

```bash
alexa-ai configure                      # Login with Amazon; --no-browser on headless machines
alexa-ai new mcp --name "Watchfloor" --locale en-US \
  --mcp-server-url "https://k04yht6ng0.execute-api.us-east-1.amazonaws.com/mcp"   # answer Y to account linking
uv run python scripts/addon_assets/fill_addon.py --check   # listing text, URLs and images (§7.2)
alexa-ai deploy                         # development stage; prints the Add-on ID
alexa-ai configure-account-linking --addon-id <id> --stage development --client-id wf-alexa   # prompts for the secret
```

| Step | Notes |
|---|---|
| `new mcp` | Scaffolds `addon-package/`; commit it. `fill_addon.py` keeps every key the CLI wrote |
| `deploy` | Uploads, builds and deploys to **development**. **Required after any change to tool names, descriptions or schemas** (tool info refreshes only on deploy). Alexa+ lists the tools with a Tier 1 `mcp:service` token (ADR-024) |
| `configure-account-linking` | The secret is `client_secret` in `wf-<env>/auth/alexa-client`: paste it at the masked prompt (or export `ALEXA_CLIENT_SECRET`), never as an argument. The CLI then prints Alexa's redirect URIs: put them all in `alexa_redirect_uris` in the env config and run `make seed-remote ENV=<env> SEED_EVENT='{"seed": false}'` |
| Test | Web simulator (text + simulated screens), optionally routed to a physical device; the 30 utterances in `tests/evals/utterances.csv` |
| Inspect | `npm install -g @alexa-ai/addon-local-inspector`, then `addon-local-inspector <mcp-url>`. It is interactive (browser UI); its verdict lands in `certification-verdict.json` under the temp directory |
| `submit` | Certification. **Not required for the hackathon.** Only run it deliberately |

**Alternative:** the Add-on Agent Skill can drive the same flow from a coding agent (introspection → scaffold → assets → deploy). Record either path in the friction log.

---

## 15. Testing guide

### 15.1 Commands

| Suite | Command | Needs |
|---|---|---|
| Unit + property | `uv run pytest tests/unit -q` | — |
| Conformance (in-process ASGI) | `uv run pytest tests/conformance -q` | — |
| Conformance (remote) | `uv run python scripts/conformance_remote.py --url $URL --token $TOKEN` | Deployed env |
| Integration | `uv run pytest tests/integration -q` | Docker |
| Security | `uv run pytest tests/security -q` | Docker |
| Agent evals | `uv run python tests/evals/run_triage_eval.py --env dev` | Deployed agents |
| Load | `uv run locust -f tests/load/locustfile.py --headless -u 40 -r 4 -t 7m --host $URL` | Deployed env |

### 15.2 Fixtures (`tests/conftest.py`)

| Fixture | Scope | Provides |
|---|---|---|
| `pg` | session | `PostgresContainer("postgres:16")`, migrated with `db/migrate.py` |
| `seeded_ws` | session | Workspace `ws_test` seeded with seed 42 (scenario deals included) |
| `token_factory` | session | Mints ES256 JWTs with a test key (any user, scopes, `aud`, expiry) |
| `client` | function | `httpx.AsyncClient(transport=ASGITransport(app))` with `Host: localhost` |
| `frozen_clock` | function | Patches `Clock.now` to a fixed instant |
| `sqs_stub` | function | Records best-effort sends instead of calling AWS |

### 15.3 Representative tests

```python
# tests/unit/test_rigs_golden.py
def test_kestrel_scores_71(kestrel_snapshot, rigs_params):
    res = rigs.score(kestrel_snapshot, rigs_params.stage("proposal"), rigs_params.weights)
    assert res.score == 71
    assert [r.code for r in res.reasons[:3]] == ["SILENT_DAYS", "VIEWS_WITHOUT_REPLY", "CLOSE_DATE_PUSHED"]
    assert round(res.r, 2) == 0.83
    assert (round(res.i, 2), res.g, round(res.s, 2)) == (0.70, 0.50, 0.80)
```

```python
# tests/unit/test_rigs_properties.py
@given(days=st.integers(0, 365), extra=st.integers(1, 30))
def test_recency_is_monotonic(days, extra, base_snapshot, params):
    a = rigs.score(base_snapshot.with_silence(days), params.stage("proposal"))
    b = rigs.score(base_snapshot.with_silence(days + extra), params.stage("proposal"))
    assert b.r >= a.r and 0 <= b.score <= 100
```

```python
# tests/conformance/test_edge.py
async def test_401_has_no_www_authenticate(client):
    r = await client.post("/mcp", headers=MCP_HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    assert "www-authenticate" not in {k.lower() for k in r.headers}
```

```python
# tests/security/test_token_replay.py
async def test_confirm_is_single_use(client, user_token, proposal):
    first = await call_tool(client, user_token, "confirm_action", {"confirmation_token": proposal.token})
    second = await call_tool(client, user_token, "confirm_action", {"confirmation_token": proposal.token})
    assert first["structuredContent"]["status"] == "executed"
    assert second["structuredContent"]["status"] == "already_executed"
    assert await count_tasks(proposal.deal_id) == 1
```

```python
# tests/security/test_agent_scope.py
async def test_agent_cannot_see_or_call_write_tools(client, agent_token):
    tools = await list_tools(client, agent_token)
    assert {t["name"] for t in tools} == AGENT_TOOLS            # 6 tools
    err = await call_tool_raw(client, agent_token, "confirm_action", {"confirmation_token": "wf1.x"})
    assert err["error"]["code"] == -32602                        # delta T2
```

### 15.4 Utterance eval sheet (`tests/evals/utterances.csv`)

Columns: `id, utterance, context, expected_first_tool, expected_args_json, observed_tool, observed_args_json, pass, notes`. The 30 rows come from DD §14.2. Pass: ≥ 27/30, and zero write calls without a prior proposal.

---

## 16. Observability guide

### 16.1 Log record (one per tool call)

```json
{
  "level": "INFO",
  "service": "mcp-server",
  "timestamp": "2026-10-21T16:42:07.311Z",
  "message": "tool_call",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "tool": "explain_deal_risk",
  "ws": "ws_lumenridge",
  "user": "us_01J9PRIYA000000000000000",
  "latency_ms": 41,
  "outcome": "ok",
  "error_code": null,
  "cold_start": false
}
```

Never logged: `Authorization` headers, tokens, activity bodies, tool argument values (only `args_hash`), or Slack webhook URLs.

### 16.2 CloudWatch Logs Insights queries

**p95 latency per tool, 5-minute bins**

```
fields @timestamp, tool, latency_ms
| filter message = "tool_call"
| stats pct(latency_ms, 95) as p95, pct(latency_ms, 99) as p99, count() as calls by tool, bin(5m)
```

**Business errors by code**

```
fields tool, error_code
| filter message = "tool_call" and outcome = "error"
| stats count() as n by tool, error_code
| sort n desc
```

**Follow one request end to end**

```
fields @timestamp, service, message, tool, latency_ms
| filter trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
| sort @timestamp asc
```

**Auth rejections by reason**

```
fields reason
| filter message = "auth_rejected"
| stats count() by reason, bin(15m)
```

### 16.3 Dashboard layout (`wf-<env>`)

| Row | Widgets |
|---|---|
| Hot path | `ToolLatencyMs` p50/p95/p99 by tool · `ToolErrors` by code · Lambda `ProvisionedConcurrencySpilloverInvocations` · 4xx/5xx from API Gateway |
| Edge and auth | `AuthRejections` by reason · `OriginRejections` · `HostRejections` · 429 count |
| Warm path | `TriageDurationS` p50/p95 · job status counts · DLQ depth (jobs) · `TriageTokensIn/Out` |
| Cold path | `WatcherDurationMs` · `WatcherAlertsQueued` · `OutboxBacklog` · `OutboxDead` · DLQ depth (outbox) |
| Availability and cost | `CanarySuccess` · RDS CPU and connections · estimated charges vs. budget |

### 16.4 Alarms (→ SNS email)

| Alarm | Condition |
|---|---|
| `wf-hot-p95` | `ToolLatencyMs` p95 > 300 ms for 2 × 5 min |
| `wf-5xx` | API Gateway 5xx rate > 0.5% for 5 min |
| `wf-spillover` | `ProvisionedConcurrencySpilloverInvocations` > 0 for 15 min |
| `wf-canary` | `CanarySuccess` < 1 for 2 consecutive periods |
| `wf-jobs-dlq` / `wf-outbox-dlq` | `ApproximateNumberOfMessagesVisible` > 0 |
| `wf-outbox-dead` | `OutboxDead` > 0 |
| `wf-triage-fail` | Failed or timed-out jobs > 10% over 1 h |
| `wf-budget-25/60/90` | AWS Budgets actual cost thresholds |

---

## 17. Operations runbooks

### 17.1 Deploy

1. Merge to `main` → `deploy-dev` runs automatically. Watch the dashboard for 15 minutes.
2. `git tag vX.Y.Z && git push --tags` → `release-demo` runs. It requires green evals and the latency probe.
3. If `tools.snapshot.json` changed: `make addon-deploy`, then re-run the utterance eval in the simulator.
4. Record the release in `docs/friction-log.md` if anything was surprising.

### 17.2 Roll back the MCP server (instant)

```bash
aws lambda list-versions-by-function --function-name wf-demo-mcp --query "Versions[-3:].Version"
```

```bash
aws lambda update-alias --function-name wf-demo-mcp --name live --function-version <previous-version>
```

Provisioned concurrency follows the alias and re-warms within a few minutes. Database migrations are forward-only, so every code release must stay compatible with the previous schema (expand → migrate → contract).

### 17.3 Redrive a dead-letter queue

```bash
aws sqs start-message-move-task --source-arn <jobs-dlq-arn>
```

Fix the root cause first (check the dispatcher logs by `trace_id`). Outbox rows marked `dead` can be re-armed by setting `status='pending', attempts=0` for that ID through a `scripts/demo.py outbox-retry` subcommand.

### 17.4 Rotate the JWT signing key

1. Create a new KMS key (`Auth` stack parameter `signing_key_generation += 1`) and deploy. JWKS now serves **both** `kid`s.
2. Switch the signer to the new `kid` (config flag) and deploy.
3. Wait at least 2 hours (access-token TTL is 60 min, plus margin).
4. Remove the old key from JWKS and schedule KMS key deletion (7-day minimum window).

### 17.5 Demo freeze (Oct 22 → Nov 20)

- GitHub environment protection blocks `release-demo`. Only `ops/*` hotfix branches with two approvals can deploy.
- Demo judge credentials are verified daily by the canary (login → token → briefing).
- Budget alarms are confirmed active. The `dev` environment's RDS is stopped when idle, and the `demo` RDS is **never** stopped.
- On Nov 20 after 12:00 PT: rotate the judge credentials and decide on teardown.

### 17.6 Incident playbooks

| Symptom | First checks | Mitigation |
|---|---|---|
| p95 > 300 ms | Spillover metric · RDS CPU · slow-query log · recent deploy | Raise provisioned concurrency to 2 · roll back the alias · `ANALYZE` hot tables |
| Spike of 401s | `AuthRejections` reason · JWKS reachability · clock skew · `WF_RESOURCE_URI` vs token `aud` | Fix the config · re-link the account · keep the last-known JWKS |
| 421 or 403 from production | `HostRejections` / `OriginRejections` · new custom domain? | Update `WF_ALLOWED_HOSTS` / `WF_ALLOWED_ORIGINS` and deploy |
| Triage stuck in `queued` | Jobs queue depth · dispatcher errors · reserved concurrency | Redrive · check the AgentCore ARN and IAM · Bedrock quotas |
| Triage `failed` spike | Dispatcher logs (`schema_gate_failed`?) · Bedrock throttling | Lower concurrency · roll back the agent image · add an eval case |
| No Slack alerts | `OutboxBacklog` · `OutboxDead` · Slack `404/410` | Rotate the webhook secret · re-arm dead rows |
| Wrong demo scores | `make demo-status` · clock offset | `make demo-reset` · `make demo-clock OFFSET=0` |

---

## 18. Troubleshooting

| Problem | Likely cause | Fix |
|---|---|---|
| Alexa+ shows old tool behavior | Tool info refreshes only on deploy | `make addon-deploy` |
| 401 after successful linking | Token `aud` ≠ `WF_RESOURCE_URI` (trailing slash, http vs https, tunnel URL changed) | Make them byte-identical. Re-link if the issuer or audience changed |
| 421 from API Gateway URL | `WF_ALLOWED_HOSTS` lacks the execute-api hostname | Add it, or use only the custom domain |
| 406 from your client | `Accept` missing `text/event-stream` | Send both media types |
| 400 on every call except initialize | Client not sending `MCP-Protocol-Version` | Send it. If Alexa+ doesn't (S1), relax per DD §22.3 note |
| Briefing returns all clear on seeded data | RLS context not set, or wrong `ws` claim | Check `set_config` in `ws_tx` and the token `ws` claim |
| `tools/list` shows 0 tools | Principal not propagated into SDK handlers | See §6.2 *Principal propagation*. CF-03 should fail first |
| Bedrock `ValidationException` on inference parameters | Both `temperature` and `top_p` set, or wrong model ID | Set only `temperature`. Use the inference-profile ID |
| AgentCore `AccessDeniedException` | Dispatcher role lacks `InvokeAgentRuntime` on that ARN | Fix the IAM resource pattern |
| Agent can't reach MCP | Job token expired (5 min) or wrong `WF_MCP_URL` | Mint per job. Check the URL and the 90 s agent timeout |
| UI resource blank in simulator | External URL in the HTML, or MCP Apps unsupported (S3) | Check the CI grep. Fall back to text-only |
| `cdk deploy` fails on fck-nat | AMI or construct version mismatch | Pin the construct version and AMI owner per its README |
| Cold starts despite provisioned concurrency | Traffic hitting `$LATEST`, not the alias | The API integration must target alias `live` |

---

## 19. Engineering checklists

### 19.1 Security (before every demo release)

- [ ] Conformance CF-12 and CF-13 green against the deployed URL (403 / 421 / 401 without `WWW-Authenticate`)
- [ ] No tokens, bodies or webhook URLs in the last 24 h of logs (Insights query run)
- [ ] RLS FORCE verified (§9.2) and the cross-workspace test green
- [ ] Agent scope sees 6 tools and calling a write tool fails
- [ ] Injection eval: 0 unconfirmed side effects
- [ ] gitleaks clean · GitHub secret scanning has no open alerts
- [ ] Demo credentials only in Devpost testing instructions

### 19.2 Performance

- [ ] `latency_probe.py` p95 ≤ 150 ms, p99 ≤ 300 ms for each read tool
- [ ] Locust profile passes (§15.1)
- [ ] Zero provisioned-concurrency spillover during the load test
- [ ] EXPLAIN plans committed; ranked risks under 5 ms

### 19.3 Hackathon submission engineering

- [ ] Repo public with an Apache-2.0 `LICENSE` visible in About, **or** private and shared with testing@devpost.com and the listed Amazon reviewers (invites expire after 7 days, so send them at submission)
- [ ] README: what it is, the demo video link, architecture diagram, setup and run (§8), how to test with the judge account, Path A/B statement
- [ ] Code imports and calls the MCP SDK at runtime (`services/mcp_server/mcp_server/server.py`)
- [ ] `docs/friction-log.md` has ≥ 8 complete entries · `docs/feature-requests.md` filled in
- [ ] Product feedback drafted per tool: `alexa-ai`, MCP Toolkit, MCP Python SDK, Strands, AgentCore, Bedrock, Slack (5 questions each, AWS usage described)
- [ ] Demo video < 3:00 · English · no unlicensed music · uploaded publicly
- [ ] `demo` environment healthy and frozen through Nov 20, 12:00 PT

---

## 20. Coding standards and conventions

### 20.1 Identifiers

| Prefix | Entity | Example |
|---|---|---|
| `ws_` | Workspace | `ws_lumenridge` (seeded) / `ws_01J9…` |
| `us_` | User | `us_01J9PRIYA…` |
| `ac_` | Account | `ac_01J9…` |
| `ct_` | Contact | `ct_01J9…` |
| `dl_` | Deal | `dl_01J9KESTREL…` |
| `act_` | Activity | `act_01J9…` |
| `hist_` | Deal history row | `hist_01J9…` |
| `pr_` | Action proposal | `pr_01JA…` |
| `tk_` | Task | `tk_01JA…` |
| `job_` | Triage job | `job_01JA…` |
| `rule_` | Standing order | `rule_01JA…` |
| `al_` | Alert | `al_01JA…` |
| `wf1.` | Confirmation token (never stored) | `wf1.Kc2h…` |

IDs are **never spoken** and never shown in UI copy. They appear only in `structuredContent` and logs.

### 20.2 Python

- `ruff` (rules: E, F, I, B, UP, SIM, ASYNC, S, TID) · `ruff format` · `pyright --strict` on `watchfloor_core`.
- Async everywhere on the request path. No sync I/O inside handlers (`ASYNC` rules).
- Pydantic models are the single source of truth for tool I/O. JSON Schemas are generated, never hand-edited.
- SQL lives in `repos/` with numbered parameters (`$1`). No string-formatted SQL (ruff `S608`).
- Time comes only from `Clock`. Direct `datetime.now` calls are banned outside `clock.py` via a ruff banned-api rule.
- Errors: raise `WatchfloorError(ErrorCode.X, …)` for business outcomes. Let everything else become `-32603` with a logged `trace_id`.

### 20.3 TypeScript

- Strict `tsconfig`. Preact function components. No runtime network calls in MCP App code.
- Colors only through CSS custom properties from `tokens.css`. Bands are always labeled in text.

### 20.4 Git

- Trunk-based: short-lived branches → PR → `main`. Conventional Commits (`feat(mcp): add log_deal_update`).
- The PR template asks: Did tool schemas change? (snapshot + `alexa-ai deploy`) · Migration added? (forward-only) · Friction-log entry?
- Tags `vMAJOR.MINOR.PATCH` deploy to `demo`.

### 20.5 Documentation

- Any change to a tool, the RIGS formula or a protocol behavior updates DD and TD in the same PR.
- Every ADR change gets a new ADR file. Existing ADRs are never rewritten.

---

## Appendix A: Root project files

### A.1 `Makefile`

```makefile
SHELL := /bin/bash
ENV ?= local

.PHONY: bootstrap dev db-up db-down migrate migrate-remote seed seed-remote dev-keys test test-all lint ui \
        snapshot addon-deploy latency demo-reset demo-clock demo-watch-now demo-status agents-image deploy

bootstrap:            ## install everything
	uv sync
	cd services/mcp_server/ui && npm ci
	cd services/harness/web && npm ci
	uv run pre-commit install

db-up:                ## local Postgres 16
	docker compose up -d db

db-down:
	docker compose down

migrate:
	uv run python -m db.migrate --env $(ENV)

migrate-remote:
	uv run python scripts/invoke_migrator.py --env $(ENV)

seed:
	uv run python -m db.seed --workspace ws_lumenridge --seed 42 --relative-now

seed-remote:
	uv run python scripts/invoke_seeder.py --env $(ENV)

dev-keys:
	uv run python scripts/dev_keys.py --out .secrets/dev_es256.pem

dev:                  ## auth :8081 + mcp :8080 with reload
	uv run honcho start -f Procfile.dev

test:
	uv run pytest tests/unit tests/conformance -q

test-all:
	uv run pytest -q

lint:
	uv run ruff check . && uv run ruff format --check . && uv run pyright packages/core services

ui:
	cd services/mcp_server/ui && npm run build

snapshot:
	uv run python -m mcp_server.tools.snapshot --write services/mcp_server/schemas/tools.snapshot.json

addon-deploy:
	git diff --quiet services/mcp_server/schemas/tools.snapshot.json || (echo "Commit the snapshot first"; exit 1)
	alexa-ai deploy

latency:
	uv run python scripts/latency_probe.py --url $(URL) --n 200

demo-reset:
	uv run python scripts/demo.py reset --env $(ENV)

demo-clock:
	uv run python scripts/demo.py clock --env $(ENV) --offset $(OFFSET)

demo-watch-now:
	uv run python scripts/demo.py watch-now --env $(ENV)

demo-status:
	uv run python scripts/demo.py status --env $(ENV)

agents-image:
	docker buildx build --platform linux/arm64 -f services/agents/Dockerfile -t wf-agents:$(ENV) --load .

deploy:
	cd infra && cdk deploy --all -c env=$(ENV)
```

### A.2 `docker-compose.yml`

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: wf_owner
      POSTGRES_PASSWORD: devpassword
      POSTGRES_DB: watchfloor
    command: ["postgres", "-c", "shared_preload_libraries=pg_stat_statements"]
    ports: ["127.0.0.1:5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U wf_owner -d watchfloor"]
      interval: 5s
      retries: 10
volumes:
  pgdata: {}
```

### A.3 `pyproject.toml` (root)

```toml
[project]
name = "watchfloor"
version = "1.0.0"
requires-python = ">=3.12,<3.13"

[tool.uv.workspace]
members = ["packages/core", "services/mcp_server", "services/auth_server",
           "services/workers", "services/agents", "services/harness/api", "infra"]

[dependency-groups]
dev = ["pytest", "pytest-asyncio", "hypothesis", "testcontainers[postgres]", "httpx",
       "locust", "ruff", "pyright", "pre-commit", "honcho"]

[tool.ruff]
line-length = 110
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM", "ASYNC", "S", "TID"]
ignore = ["S101"]                       # asserts allowed in tests

[tool.ruff.lint.flake8-tidy-imports.banned-api]
"datetime.datetime.now".msg = "Use watchfloor_core.clock.Clock"

[tool.pyright]
pythonVersion = "3.12"
strict = ["packages/core"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

### A.4 `.env.example`

```dotenv
WF_ENV=local
WF_DATABASE_URL=postgresql://wf_app:devpassword@127.0.0.1:5432/watchfloor
WF_RESOURCE_URI=http://localhost:8080/mcp
WF_ISSUER=http://localhost:8081
WF_JWKS_URL=http://localhost:8081/jwks.json
WF_ALLOWED_HOSTS=localhost,127.0.0.1
WF_ALLOWED_ORIGINS=http://localhost:5173
WF_PROTOCOL_VERSIONS=2025-11-25
WF_DEMO_MODE=true
WF_RATE_LIMIT_USER=60
WF_RATE_LIMIT_WS=600
WF_DEV_SIGNING_KEY_PATH=.secrets/dev_es256.pem
WF_ACCESS_TTL_S=3600
WF_REFRESH_TTL_DAYS=30
WF_MCP_URL=http://localhost:8080/mcp
WF_SONNET_MODEL_ID=<sonnet-5.5-inference-profile-id>
WF_HAIKU_MODEL_ID=<haiku-class-inference-profile-id>
WF_SLACK_WEBHOOK_URL=
LOG_LEVEL=DEBUG
POWERTOOLS_METRICS_NAMESPACE=Watchfloor
```

### A.5 `Procfile.dev`

```
auth: uv run uvicorn auth_server.app:app --host 127.0.0.1 --port 8081 --reload
mcp:  uv run uvicorn mcp_server.app:app --host 127.0.0.1 --port 8080 --reload
ui:   cd services/mcp_server/ui && npm run build -- --watch
```

---

## Appendix B: Glossary of identifiers

| Term | Meaning |
|---|---|
| `alias live` | Lambda alias with provisioned concurrency; the only target API Gateway invokes |
| `ws_tx` | Workspace-scoped DB transaction helper that sets the RLS context |
| `Principal` | Authenticated caller: user or agent job, workspace and scopes |
| `ToolSpec` | Registry entry tying a tool's name, scope, schemas, annotations and handler together |
| `ToolOutcome` | Handler result: structured payload, error flag, after-commit side channels |
| `EdgeGuard` | First ASGI middleware: Host, Origin, Accept, body buffering |
| `TriageOutput` | Schema-gated result of a deep-triage job |
| `rigs-1.1` | Current RIGS model version (DD §7) |
| `Path A / Path B` | Real Alexa+ add-on / simulated Alexa+ harness (decided by spike S1) |
| `S1–S7` | Verification spikes from DD §27 |
| `CF-01…CF-15` | MCP conformance test IDs from DD §22.3 |

*End of document.*
