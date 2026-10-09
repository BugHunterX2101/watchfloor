-- Authorization-server storage (DD §12.4). Separate schema, separate role:
-- the MCP server's role has no grants here.
CREATE SCHEMA IF NOT EXISTS auth;

CREATE TABLE auth.clients (
  client_id                  text PRIMARY KEY,
  client_name                text NOT NULL,
  client_secret_hash         text,                       -- Argon2id; NULL for public clients
  redirect_uris              text[] NOT NULL DEFAULT '{}',
  grant_types                text[] NOT NULL,
  token_endpoint_auth_method text NOT NULL
                             CHECK (token_endpoint_auth_method IN ('client_secret_basic','client_secret_post','none')),
  scopes                     text[] NOT NULL,
  created_at                 timestamptz NOT NULL DEFAULT now()
);

-- Login identities. Email is the login name; user/workspace become the sub and ws claims.
CREATE TABLE auth.credentials (
  email         text PRIMARY KEY,
  user_id       text NOT NULL,
  workspace_id  text NOT NULL,
  password_hash text NOT NULL,                           -- Argon2id
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE auth.auth_codes (
  code_hash      bytea PRIMARY KEY,                      -- SHA-256 of the opaque code
  client_id      text NOT NULL REFERENCES auth.clients(client_id),
  user_id        text NOT NULL,
  workspace_id   text NOT NULL,
  redirect_uri   text NOT NULL,
  code_challenge text NOT NULL,                          -- S256 only
  resource       text NOT NULL,
  scope          text NOT NULL,
  expires_at     timestamptz NOT NULL,
  used_at        timestamptz
);

CREATE TABLE auth.refresh_tokens (
  token_hash   bytea PRIMARY KEY,
  family_id    text NOT NULL,
  client_id    text NOT NULL REFERENCES auth.clients(client_id),
  user_id      text NOT NULL,
  workspace_id text NOT NULL,
  scope        text NOT NULL,
  resource     text NOT NULL,
  expires_at   timestamptz NOT NULL,
  rotated_at   timestamptz,
  revoked_at   timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX refresh_tokens_family ON auth.refresh_tokens (family_id);

CREATE TABLE auth.rate_limits (
  subject      text NOT NULL,
  window_start timestamptz NOT NULL,
  count        int NOT NULL,
  PRIMARY KEY (subject, window_start)
);
