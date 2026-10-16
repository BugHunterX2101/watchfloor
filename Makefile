SHELL := /bin/bash
ENV ?= local

.PHONY: bootstrap db-up db-down migrate seed dev-keys auth-bootstrap dev workers-once canary conformance-remote latency perf-plans evals test test-all test-agents agents-sync agents-image lint snapshot snapshot-check \
        demo-reset demo-clock demo-watch-now demo-status harness-sync harness-web test-harness ui \
        infra-sync package synth test-infra deploy cdk-deploy default-hosts agents-push migrate-remote seed-remote

bootstrap:            ## install Python dependencies (the workspace, then the agents' and harness's own projects)
	uv sync
	cd services/agents && uv sync --frozen
	cd services/harness/api && uv sync --frozen
	cd services/harness/web && npm ci && npm run build
	cd services/mcp_server/ui && npm ci && npm run build

ui:                   ## build the MCP Apps views into mcp_server/ui_dist (single files, ≤ 150 KB gzip each)
	cd services/mcp_server/ui && npm run build

harness-sync:         ## services/harness/api has its own uv.lock too (ADR-011)
	cd services/harness/api && uv sync --frozen

harness-web:          ## build the harness page into services/harness/web/dist (served on :8082)
	cd services/harness/web && npm run build

agents-sync:          ## services/agents has its own uv.lock (strands-agents pins mcp<2.2)
	cd services/agents && uv sync --frozen

db-up:                ## local Postgres 16 on 127.0.0.1:55432
	docker compose up -d db

db-down:
	docker compose down

migrate:              ## apply db/migrations and enable local role logins
	uv run python -m db.migrate --dev-passwords

seed:                 ## demo workspace (Kestrel 71 · Harbor & Pine 66 · Orbital 58), relative to now
	uv run python -m db.seed --workspace ws_lumenridge --seed 42

dev-keys:             ## local ES256 key pair in .secrets/ (gitignored)
	uv run python scripts/dev_keys.py

auth-bootstrap:       ## OAuth clients + demo logins (needs WF_DEMO_PASSWORD)
	uv run python -m auth_server.clients

dev:                  ## auth :8081 + mcp :8080 with reload, harness :8082, the agent :8083, plus the workers
	PYTHONUTF8=1 PYTHONIOENCODING=utf-8 uv run honcho start -f Procfile.dev  # a cp1252 console can't print the logs' → and ·

workers-once:         ## one watcher run, one sweep and one outbox pass, then exit
	uv run python -m workers --once

canary:               ## sign in as WF_CANARY_EMAIL, list tools, fetch a briefing (needs `make dev` running)
	uv run python -m workers.canary

conformance-remote:   ## CF-01..15 against a running server as the canary user (ENV=local|dev|demo)
	uv run python scripts/conformance_remote.py --env $(ENV)

latency:              ## p95/p99 of the read tools, paced under the per-user rate limit (N=200 by default)
	uv run python scripts/latency_probe.py --env $(ENV) --n $(or $(N),200)

perf-plans:           ## EXPLAIN (ANALYZE, BUFFERS) of the hot-path queries on the local seed → docs/perf/
	uv run python scripts/explain_plans.py --check

evals:                ## deep-triage evals: 40 driver + 10 injection cases (ENV=local uses the offline engine)
	uv run python tests/evals/run_triage_eval.py --env $(ENV) $(if $(OUT),--out $(OUT),)

test:                 ## unit + conformance (no database needed except CF-15)
	uv run pytest tests/unit tests/conformance -q

test-all:
	uv run pytest -q
	$(MAKE) test-agents
	$(MAKE) test-harness

test-agents:          ## the agents' own suite (no Bedrock, no database)
	cd services/agents && uv run --frozen pytest -q

test-harness:         ## the harness API's own suite (fake auth server, MCP server and model)
	cd services/harness/api && uv run --frozen pytest -q

agents-image:         ## arm64 image for AgentCore Runtime (TD §10.3)
	docker buildx build --platform linux/arm64 -f services/agents/Dockerfile -t wf-agents:$(ENV) --load .

lint:
	uv run ruff check . && uv run ruff format --check .

# ENV=dev|demo runs these through the seeder and watcher Lambdas with your AWS credentials (scripts/demo.py).
demo-reset:           ## re-seed the demo workspace relative to now and assert the scenario scores
	uv run python scripts/demo.py reset --env $(ENV)

demo-clock:           ## OFFSET=+14h for the next-morning scene, OFFSET=0 to undo; runs the watcher once
	uv run python scripts/demo.py clock --env $(ENV) --offset $(OFFSET)

demo-watch-now:       ## one watcher run: fires standing orders on demand
	uv run python scripts/demo.py watch-now --env $(ENV)

demo-status:          ## bands, scenario scores, board, pending proposals, running jobs, outbox backlog
	uv run python scripts/demo.py status --env $(ENV)

# ------------------------------------------------------------------ AWS (TD §10–11; README, Deploy)
CDK := npx --yes aws-cdk@2

infra-sync:           ## infra/ is its own uv project (aws-cdk-lib stays out of the dev environment)
	cd infra && uv sync --frozen

package: ui harness-web  ## build the Lambda zip directories in infra/build/ (arm64; package on Linux for deploys)
	uv run python scripts/package_lambdas.py

synth:                ## synthesize ENV=dev|demo with stub Lambda code (structure check; no build needed)
	cd infra && $(CDK) synth -c env=$(ENV) -c wf:stubAssets=true --quiet

test-infra:           ## synth both environments and assert the properties the specs pin down
	cd infra && uv run --frozen pytest -q

deploy:               ## package, then deploy every stack of ENV=dev|demo; AGENT_RUNTIME=false on the very first deploy
	@case "$(ENV)" in dev|demo) ;; *) echo "set ENV=dev or ENV=demo"; exit 2;; esac
	$(MAKE) package
	$(MAKE) cdk-deploy

# Without custom domains (certificate_arn: null), the hosts AWS assigned go into the config first; a
# first deploy is the one that assigns them, so it is followed by a second that uses them.
CDK_DEPLOY = cd infra && $(CDK) deploy --all -c env=$(ENV) $(if $(filter false,$(AGENT_RUNTIME)),-c wf:agentRuntime=false,) $(CDK_FLAGS)
cdk-deploy:           ## deploy ENV=dev|demo from infra/build as it is (CI: CDK_FLAGS="--require-approval never")
	@case "$(ENV)" in dev|demo) ;; *) echo "set ENV=dev or ENV=demo"; exit 2;; esac
	uv run python scripts/default_hosts.py --env $(ENV)
	$(CDK_DEPLOY)
	@if ! uv run python scripts/default_hosts.py --env $(ENV) --check; then \
	  uv run python scripts/default_hosts.py --env $(ENV) && $(CDK_DEPLOY); fi

default-hosts:        ## write the hosts AWS assigned to ENV=dev|demo into its config (no custom domains)
	uv run python scripts/default_hosts.py --env $(ENV)

agents-push:          ## push the agents image built by agents-image: make agents-push ENV=dev REPO=<RepositoryUri output>
	@test -n "$(REPO)" || { echo "set REPO to the wf-$(ENV)-agents stack's RepositoryUri output"; exit 2; }
	aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin $${REPO%%/*}
	docker tag wf-agents:$(ENV) $(REPO):$(ENV) && docker push $(REPO):$(ENV)

# Both run once on the environment's compute: a Lambda invoke, or a one-off ECS task (ADR-025), and
# fail the make when the job fails (scripts/run_task.py).
migrate-remote:       ## run the migrator in ENV=dev|demo (migrations + role logins)
	@case "$(ENV)" in dev|demo) ;; *) echo "set ENV=dev or ENV=demo"; exit 2;; esac
	uv run python scripts/run_task.py --env $(ENV) migrator

SEED_EVENT ?= {}
seed-remote:          ## run the seeder in ENV=dev|demo: reset the demo workspace + OAuth clients and logins
	@case "$(ENV)" in dev|demo) ;; *) echo "set ENV=dev or ENV=demo"; exit 2;; esac
	uv run python scripts/run_task.py --env $(ENV) seeder '$(SEED_EVENT)'

snapshot:             ## regenerate the tools/list snapshot after changing a tool
	cd services/mcp_server && uv run python -m mcp_server.tools.snapshot

snapshot-check:
	cd services/mcp_server && uv run python -m mcp_server.tools.snapshot --check
