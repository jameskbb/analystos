# AnalystOS developer tasks. Run `make help` for the list.
SHELL := /bin/bash
.DEFAULT_GOAL := help

UV ?= uv
PNPM ?= pnpm
WEB := apps/web
E2E := tests/e2e
API_PORT ?= 8000
WEB_PORT ?= 3000

.PHONY: help setup setup-e2e dev api web demo test test-py test-web evals lint format typecheck typecheck-all build e2e \
        migrate migrate-postgres docker-env docker-up docker-down clean

help: ## Show this help
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Install Python (uv) and web (pnpm) dependencies
	$(UV) sync --all-packages --all-extras
	$(PNPM) --dir $(WEB) install --frozen-lockfile

setup-e2e: ## Install Playwright and its Chromium browser for the E2E suite
	$(PNPM) --dir $(E2E) install --frozen-lockfile
	$(PNPM) --dir $(E2E) exec playwright install chromium

dev: ## Run API (:8000) and web (:3000) together with reload; Ctrl-C stops both
	@trap 'kill 0' EXIT INT TERM; \
	  export AOS_PROXY_SECRET=$${AOS_PROXY_SECRET:-$$(python3 -c "import secrets; print(secrets.token_hex(24))")}; \
	  $(UV) run uvicorn analystos_api.main:app_factory --factory --reload --port $(API_PORT) \
	    --reload-dir apps/api/src --reload-dir packages & \
	  API_URL=http://localhost:$(API_PORT) $(PNPM) --dir $(WEB) dev & \
	  wait

api: ## Run the API only (http://localhost:8000/api/docs)
	$(UV) run analystos-api

web: ## Run the web app only (http://localhost:3000); set the API's AOS_PROXY_SECRET here too
	API_URL=http://localhost:$(API_PORT) $(PNPM) --dir $(WEB) dev

demo: ## Generate (or verify the cached) Summit Supply Co. dataset and print the planted stories
	$(UV) run python -m analystos_demo ensure
	$(UV) run python -m analystos_demo scenarios

test: test-py test-web ## Run every Python and web test (includes analytical evals)

test-py: ## Python tests: engine, investigator, demo data, API, MCP and evals (one process per suite)
	scripts/test-python.sh

test-web: ## Web unit tests (vitest)
	$(PNPM) --dir $(WEB) test

evals: ## Analytical evals only, with the scorecard printed
	$(UV) run pytest tests/evals -s -q

lint: ## Ruff lint, ruff format check and ESLint
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(PNPM) --dir $(WEB) lint

format: ## Apply ruff formatting and autofixes
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

typecheck: ## Mypy (all Python packages) and TypeScript (tsc --noEmit)
	$(UV) run mypy packages/engine/src packages/investigator/src packages/demo-data/src apps/api/src apps/mcp/src
	$(PNPM) --dir $(WEB) typecheck

typecheck-all: ## Mypy over every Python package (same set as CI)
	$(UV) run mypy packages/engine/src packages/investigator/src packages/demo-data/src apps/api/src apps/mcp/src

build: ## Production build of the web app
	$(PNPM) --dir $(WEB) build

e2e: ## Playwright acceptance flow (boots API + web on free ports with a throwaway data dir)
	$(PNPM) --dir $(E2E) test

migrate: ## Apply Alembic migrations to DATABASE_URL (default: SQLite in ./data)
	$(UV) run analystos-api-migrate

migrate-postgres: ## Apply migrations to a local Postgres (DATABASE_URL must point at it)
	@test -n "$$DATABASE_URL" || (echo "set DATABASE_URL=postgresql+psycopg://..." && exit 1)
	$(UV) run analystos-api-migrate

docker-env: ## Create .env from .env.example with random AOS_SECRET_KEY, AOS_PROXY_SECRET and setup token (never overwrites)
	@if [ -f .env ]; then echo ".env exists; leaving it alone"; else \
	  key=$$(python3 -c "import secrets; print(secrets.token_urlsafe(48))"); \
	  tok=$$(python3 -c "import secrets; print(secrets.token_urlsafe(24))"); \
	  px=$$(python3 -c "import secrets; print(secrets.token_hex(24))"); \
	  sed -e "s|^AOS_SECRET_KEY=.*|AOS_SECRET_KEY=$$key|" -e "s|^AOS_BOOTSTRAP_TOKEN=.*|AOS_BOOTSTRAP_TOKEN=$$tok|" \
	      -e "s|^AOS_PROXY_SECRET=.*|AOS_PROXY_SECRET=$$px|" .env.example > .env; chmod 600 .env; \
	  echo "wrote .env; create the first account with setup token: $$tok"; fi

docker-up: docker-env ## Build and start Postgres + API + web with docker compose
	docker compose up --build -d
	@echo "web: http://localhost:3000   api docs: http://localhost:8000/api/docs (both bound to 127.0.0.1)"

docker-down: ## Stop the docker compose stack (volumes are kept)
	docker compose down

clean: ## Remove caches and build output (keeps ./data)
	rm -rf .pytest_cache .ruff_cache .mypy_cache $(WEB)/.next $(E2E)/test-results $(E2E)/playwright-report
	find . -name __pycache__ -type d -prune -not -path './.venv/*' -exec rm -rf {} +
