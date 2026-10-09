# services/agents/Dockerfile: arm64 image for AgentCore Runtime (TD §10.3).
# Build from the repo root:
#   docker buildx build --platform linux/arm64 -f services/agents/Dockerfile -t wf-agents:dev --load .
# services/agents is its own uv project (strands-agents pins mcp<2.2), so it
# syncs from its own uv.lock, with watchfloor-core as a path dependency.
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    DOCKER_CONTAINER=1
COPY --from=ghcr.io/astral-sh/uv:0.12.4 /uv /usr/local/bin/uv
WORKDIR /app

# Dependencies first, for layer caching.
COPY packages/core ./packages/core
COPY services/agents/pyproject.toml services/agents/uv.lock ./services/agents/
RUN cd services/agents && uv sync --frozen --no-dev --no-install-project

COPY services/agents/agents ./services/agents/agents
RUN cd services/agents && uv sync --frozen --no-dev

RUN useradd -r -u 10001 agent && chown -R agent /app
USER agent
WORKDIR /app/services/agents
EXPOSE 8080
CMD ["uv", "run", "--no-sync", "python", "-m", "agents.main"]
