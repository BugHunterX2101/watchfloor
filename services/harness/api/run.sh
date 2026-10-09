#!/bin/sh
# services/harness/api/run.sh: Lambda Web Adapter entry point (TD §10.2).
# The adapter forwards to 127.0.0.1:8080; locally, `make dev` runs the harness on :8082.
exec python -m uvicorn harness_api.app:app --host 127.0.0.1 --port "${PORT:-8080}" --no-access-log
