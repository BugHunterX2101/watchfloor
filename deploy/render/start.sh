#!/usr/bin/env bash
# deploy/render/start.sh: boot the stack in one container (deploy/render/Dockerfile).
# Every boot: migrate, give the app and auth roles their logins, write the signing key, re-seed the demo
# workspace relative to now, provision the OAuth clients and demo logins; then run the servers, the
# workers and Caddy, and stop everything when any one exits so Render restarts the instance.
set -euo pipefail
cd /app
PY=/app/.venv/bin/python
HPY=/app/services/harness/api/.venv/bin/python

: "${WF_OWNER_DATABASE_URL:?the Render Postgres internal URL}"
: "${WF_APP_DB_PASSWORD:?}" "${WF_AUTH_DB_PASSWORD:?}" "${WF_SIGNING_KEY_PEM_B64:?}" "${WF_DEMO_PASSWORD:?}"
: "${WF_HARNESS_REDIRECT_URI:?https://<host>/oauth/callback}"

# The app and auth roles reach the owner's database with their own credentials (DD §13.3).
hostdb="${WF_OWNER_DATABASE_URL#*://}"
hostdb="${hostdb#*@}"
export WF_DATABASE_URL="postgresql://wf_app:${WF_APP_DB_PASSWORD}@${hostdb}"
export WF_AUTH_DATABASE_URL="postgresql://wf_auth:${WF_AUTH_DB_PASSWORD}@${hostdb}"

umask 077
printf '%s' "$WF_SIGNING_KEY_PEM_B64" | base64 -d > .secrets/es256.pem
export WF_DEV_SIGNING_KEY_PATH=/app/.secrets/es256.pem

"$PY" deploy/render/bootstrap.py
"$PY" -m db.seed --workspace ws_lumenridge --seed 42
"$PY" -m auth_server.clients --harness-redirect-uri "$WF_HARNESS_REDIRECT_URI"

# No API Gateway cuts a turn at 30 s here, and the free instance's CPU is slow: give the model longer.
export WF_TURN_TIMEOUT_S="${WF_TURN_TIMEOUT_S:-40}"

trap 'kill 0 2>/dev/null; exit 0' TERM INT
"$PY" -m uvicorn auth_server.app:app --host 127.0.0.1 --port 8081 --no-access-log &
"$PY" -m uvicorn mcp_server.app:app --host 127.0.0.1 --port 8080 --no-access-log &
"$HPY" -m uvicorn harness_api.app:app --host 127.0.0.1 --port 8082 --no-access-log &
if [ "${WF_WORKERS:-on}" = on ]; then "$PY" -m workers & fi
caddy run --config deploy/render/Caddyfile --adapter caddyfile &

wait -n
echo "start.sh: a process exited; stopping the rest so Render restarts the instance" >&2
kill 0 2>/dev/null || true
exit 1
