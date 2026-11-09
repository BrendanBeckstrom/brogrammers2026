# Every command for Debrief. Config comes from .env (see .env.example).
SHELL := /bin/bash
ENV := set -a; [ -f .env ] && . ./.env; set +a;

.PHONY: setup ingest label index api web test golden cache

setup: ## Install Python and web dependencies
	uv sync --locked
	cd web && npm ci

ingest: ## Download, clean, chunk, tag and label; writes data/chunks.jsonl (tasks 2-4, 12)
	$(ENV) uv run python -m ingest.download
	$(ENV) uv run python -m ingest.chunk
	$(ENV) uv run python -m ingest.tag
	$(ENV) uv run python -m ingest.label --no-elastic

label: ## Label new talks (data/talk_labels.csv) and set talk_label in the chunks and Elastic (task 12)
	$(ENV) uv run python -m ingest.label

index: ## Create the Elastic indexes and load the chunks (task 5)
	$(ENV) uv run python -m ingest.index

api: ## Run the API locally on port 8000
	$(ENV) uv run uvicorn api.main:app --reload --port 8000

web: ## Run the web app locally on port 5173 (proxies /api to :8000)
	cd web && npm run dev

test: ## Lint and unit tests for Python and web
	uv run ruff check .
	uv run ruff format --check .
	uv run pytest -q
	cd web && npm run lint && npm test

golden: ## Run every golden stack against the running API (task 6+); ARGS="--rerank --cache"
	$(ENV) uv run python -m tests.golden $(ARGS)

cache: ## Build the offline demo cache in api/cache/ from a running API (DEBRIEF_API, task 13)
	$(ENV) uv run python -m tests.build_cache
