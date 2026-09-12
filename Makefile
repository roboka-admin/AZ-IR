# AZ-IR — one entrypoint for every routine operation.
#
#   make install     # python venv + node modules
#   make check       # lint + types + tests (backend and frontend)
#   make dev-backend # API on :8000 with the fixtures driver (no database needed)
#   make dev-web     # Next.js on :3000
#   make db-up       # PostGIS in Docker, then `make migrate seed`
#
# Override the interpreter for a prebuilt environment:
#   make check PYTHON=/path/to/venv/bin/python

PYTHON      ?= python3
NPM         ?= npm
BACKEND     := backend
FRONTEND    := frontend
VENV        := .venv
DB_URL      ?= postgresql+psycopg2://azir:azir@localhost:5432/azir
TEST_DB_URL ?= postgresql+psycopg2://azir:azir@localhost:5432/azir_test
FIXTURES    ?= $(BACKEND)/seeds/fixtures
UVICORN     := $(PYTHON) -m uvicorn

.DEFAULT_GOAL := help
.PHONY: help install venv lint typecheck test test-postgis check clean smoke \
        migrate seed doctor dev-backend dev-web build db-up db-down db-logs

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

venv: ## Create .venv and install the backend with dev extras
	@test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip >/dev/null
	$(VENV)/bin/pip install -e "$(BACKEND)[dev]"

install: venv ## Backend venv + frontend node modules
	cd $(FRONTEND) && $(NPM) ci --no-audit --no-fund

lint: ## ruff (backend) + eslint (frontend)
	cd $(BACKEND) && $(PYTHON) -m ruff check src tests
	cd $(FRONTEND) && $(NPM) run lint

typecheck: ## mypy (backend) + tsc (frontend)
	cd $(BACKEND) && $(PYTHON) -m mypy src
	cd $(FRONTEND) && $(NPM) run typecheck

test: ## Backend test suite (fixtures driver; postgis tests skip without AZIR_TEST_DB_URL)
	cd $(BACKEND) && $(PYTHON) -m pytest tests

test-postgis: ## Contract suite against a real PostGIS database
	cd $(BACKEND) && AZIR_TEST_DB_URL="$(TEST_DB_URL)" $(PYTHON) -m pytest tests -m postgis

check: lint typecheck test ## Everything CI runs

smoke: ## End-to-end contract check against a running API (AZIR_API=... to point elsewhere)
	./scripts/smoke.sh

build: ## Production frontend build (needs a reachable API for server-rendered pages)
	cd $(FRONTEND) && $(NPM) run build

migrate: ## alembic upgrade head
	cd $(BACKEND) && AZIR_DB_URL="$(DB_URL)" $(PYTHON) -m alembic upgrade head

seed: ## Load the fixture corpus into PostgreSQL+PostGIS
	cd $(BACKEND) && AZIR_DB_DRIVER=postgis AZIR_DB_URL="$(DB_URL)" AZIR_FIXTURES_DIR="$(FIXTURES)" \
	  $(PYTHON) -m azir.cli seed --fixtures-dir "$(FIXTURES)"

doctor: ## Configuration + data health report
	cd $(BACKEND) && AZIR_FIXTURES_DIR="$(FIXTURES)" $(PYTHON) -m azir.cli --human-logs doctor

dev-backend: ## API with reload, fixtures driver (no database required)
	cd $(BACKEND) && AZIR_FIXTURES_DIR="$(FIXTURES)" AZIR_DEBUG=true \
	  $(UVICORN) azir.main:app --app-dir src --host 0.0.0.0 --port 8000 --reload

dev-web: ## Next.js dev server
	cd $(FRONTEND) && $(NPM) run dev

db-up: ## PostGIS in Docker
	docker compose up -d db
	@echo "waiting for postgres..."; sleep 5; docker compose ps db

db-down: ## Stop the database (keeps the volume)
	docker compose stop db

db-logs: ## Tail the API logs
	docker compose logs -f api

clean: ## Remove build artefacts (never touches data)
	rm -rf $(FRONTEND)/.next $(FRONTEND)/tsconfig.tsbuildinfo
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	find . -name '.pytest_cache' -o -name '.mypy_cache' -o -name '.ruff_cache' | xargs rm -rf 2>/dev/null || true
