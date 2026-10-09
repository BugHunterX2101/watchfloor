-- Watchfloor schema (DD §13.2). Forward-only; fixes go in new migrations.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TYPE deal_stage AS ENUM ('discovery','demo','proposal','negotiation','closing','won','lost');
CREATE TYPE risk_band  AS ENUM ('healthy','watch','at_risk');
CREATE TYPE act_type   AS ENUM ('email','meeting','call','doc_view','note');
CREATE TYPE act_dir    AS ENUM ('inbound','outbound','internal');

CREATE TABLE workspaces (
  id            text PRIMARY KEY,                        -- ws_…
  name          text NOT NULL,
  timezone      text NOT NULL DEFAULT 'America/Los_Angeles',
  currency      char(3) NOT NULL DEFAULT 'USD',
  clock_offset  interval NOT NULL DEFAULT '0',           -- honored only when WF_DEMO_MODE=true
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id               text PRIMARY KEY,                     -- us_…
  workspace_id     text NOT NULL REFERENCES workspaces(id),
  display_name     text NOT NULL,
  email            text NOT NULL UNIQUE,
  role             text NOT NULL CHECK (role IN ('owner','manager','rep')),
  slack_user_id    text,
  last_briefing_at timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX users_ws ON users (workspace_id);
CREATE INDEX users_name_trgm ON users USING gin (display_name gin_trgm_ops);

CREATE TABLE user_prefs (
  user_id         text PRIMARY KEY REFERENCES users(id),
  workspace_id    text NOT NULL REFERENCES workspaces(id),
  briefing_limit  smallint NOT NULL DEFAULT 3 CHECK (briefing_limit BETWEEN 1 AND 5),
  include_amounts boolean  NOT NULL DEFAULT true,
  focus_segment   text CHECK (focus_segment IN ('smb','mid','enterprise')),
  alert_channel   text NOT NULL DEFAULT '#deals-at-risk',
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE accounts (
  id           text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES workspaces(id),
  name         text NOT NULL,
  segment      text NOT NULL CHECK (segment IN ('smb','mid','enterprise')),
  owner_id     text REFERENCES users(id),
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX accounts_name_trgm ON accounts USING gin (name gin_trgm_ops);

CREATE TABLE contacts (
  id                text PRIMARY KEY,
  workspace_id      text NOT NULL REFERENCES workspaces(id),
  account_id        text NOT NULL REFERENCES accounts(id),
  name              text NOT NULL,
  title             text,
  is_economic_buyer boolean NOT NULL DEFAULT false
);
CREATE INDEX contacts_account ON contacts (account_id);

CREATE TABLE deals (
  id                 text PRIMARY KEY,                   -- dl_…
  workspace_id       text NOT NULL REFERENCES workspaces(id),
  account_id         text NOT NULL REFERENCES accounts(id),
  name               text NOT NULL,
  amount_cents       bigint NOT NULL CHECK (amount_cents >= 0),
  stage              deal_stage NOT NULL,
  close_date         date NOT NULL,
  owner_id           text NOT NULL REFERENCES users(id),
  next_step          text,
  next_step_date     date,
  economic_buyer_id  text REFERENCES contacts(id),
  decision_process   text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX deals_open ON deals (workspace_id, stage) WHERE stage NOT IN ('won','lost');
CREATE INDEX deals_name_trgm ON deals USING gin (name gin_trgm_ops);
CREATE INDEX deals_account ON deals (account_id);

CREATE TABLE activities (
  id           text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES workspaces(id),
  deal_id      text NOT NULL REFERENCES deals(id),
  contact_id   text REFERENCES contacts(id),
  type         act_type NOT NULL,
  direction    act_dir NOT NULL,
  occurred_at  timestamptz NOT NULL,
  summary      text NOT NULL,
  body         text                                      -- untrusted free text; never logged
);
CREATE INDEX activities_deal_time ON activities (deal_id, occurred_at DESC);

CREATE TABLE deal_history (
  id           text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES workspaces(id),
  deal_id      text NOT NULL REFERENCES deals(id),
  field        text NOT NULL CHECK (field IN ('close_date','stage','amount_cents','owner_id',
                                              'next_step','next_step_date','decision_process')),
  old_value    text,
  new_value    text,
  changed_by   text,
  changed_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX deal_history_deal_time ON deal_history (deal_id, changed_at DESC);

CREATE TABLE risk_scores (
  deal_id       text PRIMARY KEY REFERENCES deals(id),
  workspace_id  text NOT NULL REFERENCES workspaces(id),
  score         smallint NOT NULL CHECK (score BETWEEN 0 AND 100),
  band          risk_band NOT NULL,
  r numeric(4,3) NOT NULL, i numeric(4,3) NOT NULL, g numeric(4,3) NOT NULL, s numeric(4,3) NOT NULL,
  reasons       jsonb NOT NULL,
  band_since    timestamptz NOT NULL,
  computed_at   timestamptz NOT NULL,
  model_version text NOT NULL
);
CREATE INDEX risk_scores_rank ON risk_scores (workspace_id, band, score DESC);

CREATE TABLE risk_score_daily (
  deal_id      text NOT NULL REFERENCES deals(id),
  day          date NOT NULL,
  workspace_id text NOT NULL REFERENCES workspaces(id),
  score        smallint NOT NULL,
  band         risk_band NOT NULL,
  PRIMARY KEY (deal_id, day)
);

CREATE TABLE tasks (
  id                 text PRIMARY KEY,
  workspace_id       text NOT NULL REFERENCES workspaces(id),
  deal_id            text NOT NULL REFERENCES deals(id),
  assignee_id        text NOT NULL REFERENCES users(id),
  title              text NOT NULL,
  due_date           date,
  status             text NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','cancelled')),
  created_by         text NOT NULL REFERENCES users(id),
  source_proposal_id text,
  created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE standing_orders (
  id                text PRIMARY KEY,
  workspace_id      text NOT NULL REFERENCES workspaces(id),
  user_id           text NOT NULL REFERENCES users(id),
  rule              jsonb NOT NULL,
  description       text NOT NULL,
  active            boolean NOT NULL DEFAULT true,
  created_at        timestamptz NOT NULL DEFAULT now(),
  removed_at        timestamptz,
  last_triggered_at timestamptz
);
CREATE INDEX standing_orders_user_active ON standing_orders (user_id) WHERE active;

CREATE TABLE alerts (
  id           text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES workspaces(id),
  rule_id      text NOT NULL REFERENCES standing_orders(id),
  deal_id      text NOT NULL REFERENCES deals(id),
  sent_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX alerts_dedupe ON alerts (rule_id, deal_id, sent_at DESC);

CREATE TABLE action_proposals (
  id           text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES workspaces(id),
  user_id      text NOT NULL REFERENCES users(id),
  deal_id      text NOT NULL REFERENCES deals(id),
  action_type  text NOT NULL,
  params       jsonb NOT NULL,
  params_hash  bytea NOT NULL,
  token_hash   bytea NOT NULL UNIQUE,
  preview      jsonb NOT NULL,
  status       text NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending','executed','expired','superseded','failed')),
  result       jsonb,
  expires_at   timestamptz NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  executed_at  timestamptz
);
CREATE INDEX action_proposals_pending ON action_proposals (user_id, deal_id, action_type) WHERE status = 'pending';

CREATE TABLE triage_jobs (
  id           text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES workspaces(id),
  user_id      text NOT NULL REFERENCES users(id),
  deal_id      text NOT NULL REFERENCES deals(id),
  status       text NOT NULL CHECK (status IN ('queued','running','done','failed','timed_out')),
  trace_id     text,
  result       jsonb,
  error        text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  started_at   timestamptz,
  finished_at  timestamptz
);
CREATE INDEX triage_jobs_deal_recent ON triage_jobs (deal_id, created_at DESC);

CREATE TABLE outbox (
  id              bigserial PRIMARY KEY,
  workspace_id    text NOT NULL REFERENCES workspaces(id),
  kind            text NOT NULL CHECK (kind IN ('slack_message','slack_dm','draft_followup','triage_job')),
  payload         jsonb NOT NULL,
  dedupe_key      text UNIQUE,
  status          text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','dead')),
  attempts        int NOT NULL DEFAULT 0,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  last_error      text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  sent_at         timestamptz
);
CREATE INDEX outbox_pending ON outbox (next_attempt_at) WHERE status = 'pending';

CREATE TABLE audit_log (
  id           bigserial PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES workspaces(id),
  user_id      text,
  tool         text NOT NULL,
  args_hash    bytea,
  outcome      text NOT NULL,
  error_code   text,
  latency_ms   int,
  trace_id     text,
  at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_ws_time ON audit_log (workspace_id, at DESC);

CREATE TABLE rate_limits (
  subject      text NOT NULL,
  window_start timestamptz NOT NULL,
  count        int NOT NULL,
  PRIMARY KEY (subject, window_start)
);
