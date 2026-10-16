#!/bin/sh
# services/auth_server/run.sh: Lambda Web Adapter entry point (TD §10.2). On ECS (ADR-025) the
# container sets WF_BIND_HOST=0.0.0.0 and FORWARDED_ALLOW_IPS, which uvicorn reads.
exec python -m uvicorn auth_server.app:app --host "${WF_BIND_HOST:-127.0.0.1}" --port "${PORT:-8080}" --no-access-log
