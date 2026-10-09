#!/bin/sh
# services/auth_server/run.sh: Lambda Web Adapter entry point (TD §10.2).
exec python -m uvicorn auth_server.app:app --host 127.0.0.1 --port "${PORT:-8080}" --no-access-log
