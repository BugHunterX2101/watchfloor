# deploy/render/Dockerfile: the whole stack in one container, for Render's free plan.
# Build from the repo root:  docker build -f deploy/render/Dockerfile .
# Caddy takes Render's $PORT and routes by path to the auth server (:8081), the MCP server (:8080) and the
# harness (:8082), so sign-in, the MCP endpoint and the page share one https origin. start.sh migrates,
# seeds and provisions on every boot, then supervises the processes (deploy/render/README.md).

FROM node:24-slim AS web
WORKDIR /src
COPY services/harness/web/package.json services/harness/web/package-lock.json services/harness/web/
RUN cd services/harness/web && npm ci
COPY services/mcp_server/ui/package.json services/mcp_server/ui/package-lock.json services/mcp_server/ui/
RUN cd services/mcp_server/ui && npm ci
COPY services/harness/web services/harness/web
COPY services/mcp_server/ui services/mcp_server/ui
RUN cd services/harness/web && npm run build
RUN mkdir -p services/mcp_server/mcp_server && cd services/mcp_server/ui && npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.12.4 /uv /usr/local/bin/uv
COPY --from=caddy:2.10.2 /usr/bin/caddy /tmp/caddy
# The image gives caddy cap_net_bind_service as a file capability, which a non-root process under Render's
# sandbox may not exec (EPERM). A plain cp drops it; Caddy only needs $PORT (10000), not a low port.
RUN cp /tmp/caddy /usr/local/bin/caddy && rm /tmp/caddy
WORKDIR /app

# The workspace (core, MCP server, auth server, workers), then the harness API's own project (ADR-011).
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY services/mcp_server ./services/mcp_server
COPY services/auth_server ./services/auth_server
COPY services/workers ./services/workers
RUN uv sync --frozen --no-dev --all-packages
COPY services/harness/api ./services/harness/api
RUN cd services/harness/api && uv sync --frozen --no-dev

COPY db ./db
COPY scripts/dev_keys.py ./scripts/dev_keys.py
COPY --from=web /src/services/harness/web/dist ./services/harness/web/dist
COPY --from=web /src/services/mcp_server/mcp_server/ui_dist ./services/mcp_server/mcp_server/ui_dist
COPY deploy/render ./deploy/render

RUN useradd -r -u 10001 wf && mkdir -p /app/.secrets /data/caddy && chown -R wf /app/.secrets /data
USER wf
ENV XDG_DATA_HOME=/data XDG_CONFIG_HOME=/data
EXPOSE 10000
CMD ["bash", "deploy/render/start.sh"]
