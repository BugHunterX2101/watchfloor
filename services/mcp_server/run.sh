#!/bin/sh
# services/mcp_server/run.sh: Lambda Web Adapter entry point (TD §10.2).
# The adapter forwards to 127.0.0.1:8080; locally, `make dev` runs the same command.
exec python -m uvicorn mcp_server.app:app --host 127.0.0.1 --port "${PORT:-8080}" --no-access-log
