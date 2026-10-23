# Watchfloor on Render (free plan)

While the AWS account's compute is blocked, the whole stack runs as **one Docker web service** on Render's
free plan, with Render's **free Postgres 16**. Nothing here is paid.

| Piece | Where |
| --- | --- |
| `Dockerfile` | Node stage builds the harness page and the MCP Apps views; Python stage installs the workspace and the harness API's own project (ADR-011), plus Caddy |
| `Caddyfile` | One origin: `/authorize`, `/login`, `/token`, `/jwks.json`… to the auth server (:8081); `/mcp` and its metadata to the MCP server (:8080); everything else to the harness (:8082) |
| `start.sh` | Every boot: migrate, give `wf_app` and `wf_auth` logins, write the signing key, re-seed the demo workspace relative to now, provision the OAuth clients and demo logins; then run the three servers, the workers and Caddy |
| `bootstrap.py` | The migrate-and-roles step |

## Environment

Set on the service, never in the repo:

- `WF_OWNER_DATABASE_URL`: the Postgres internal URL. `WF_APP_DB_PASSWORD`, `WF_AUTH_DB_PASSWORD`: 24+
  URL-safe characters each.
- `WF_SIGNING_KEY_PEM_B64`: a base64 ES256 PKCS#8 PEM. `WF_SESSION_SECRET`, `WF_HARNESS_SESSION_SECRET`,
  `WF_ALEXA_CLIENT_SECRET`, `WF_DISPATCHER_CLIENT_SECRET`, `WF_DEMO_PASSWORD`.
- `WF_ISSUER=https://<host>`, `WF_RESOURCE_URI=https://<host>/mcp`,
  `WF_HARNESS_REDIRECT_URI=https://<host>/oauth/callback`, `WF_ALLOWED_HOSTS=<host>,localhost,127.0.0.1`,
  `WF_ALLOWED_ORIGINS=https://<host>`; the servers reach each other on loopback
  (`WF_JWKS_URL=http://127.0.0.1:8081/jwks.json`, `WF_MCP_URL=http://127.0.0.1:8080/mcp`).
- A model: `WF_GEMINI_MODEL_ID` + `WF_GEMINI_API_KEY`, or Amazon Nova on Bedrock (`WF_HAIKU_MODEL_ID`
  with `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`). With `WF_KMS_KEY_ID` set, tokens are
  signed by AWS KMS instead of the PEM.
- Optional, Amazon Polly reads replies aloud (ADR-033): `WF_POLLY_VOICE` (a neural voice id, e.g.
  `Ruth`) with `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `AWS_REGION` for an IAM user allowed
  `polly:SynthesizeSpeech` and nothing else. `WF_POLLY_DAILY_CHARS` caps the characters a UTC day
  (default 30,000; audio and word timings count twice); `WF_POLLY_REGION` overrides `AWS_REGION`.
  Unset `WF_POLLY_VOICE` to turn it off: the browser's own voice speaks. To run this deployment without
  AWS (ADR-034), leave it unset.
- `WF_PROTOCOL_VERSIONS=2025-06-18,2025-11-25` (or leave it unset for that default): Alexa+ speaks
  2025-11-25, Kiro and Kiro Crew 2025-06-18 (ADR-034). Every boot also registers the read-only `wf-kiro`
  client; its setup is in [`integrations/kiro`](../../integrations/kiro/README.md).

`WF_ENV=local` keeps the file signer available and runs the workers in-process; the https issuer still
makes every cookie Secure. Caddy (`trusted_proxies_strict`, `header_up X-Forwarded-For {client_ip}`) sends
each server the one address Render's edge saw, and uvicorn, which trusts loopback, makes it the connection's
client, so the auth server's per-address login and token limits are per caller. That was checked against
Caddy 2.10.2 locally; Render's edge appends, with Cloudflare in front, so if it puts a public hop after the
caller the limit falls back to per edge address: still never the address the caller wrote.

## What to expect

- A free instance sleeps after 15 minutes idle; the first request wakes it (about a minute). Each wake
  re-seeds the demo workspace relative to now, so you may need to sign in again.
- The workers run only while the instance is awake.
- Render's free Postgres expires 30 days after it is created.
