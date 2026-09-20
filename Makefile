# AZ-IR — one entrypoint for every routine operation.
#
#   make install     # python venv + node modules
#   make check       # lint + types + tests (backend and frontend)
#   make dev-backend # API on :8000 with the fixtures driver (no database needed)
#   make tiles       # build the static PMTiles archive the map serves (ADR-0018)
#   make dev-users   # the four dev logins on PostgreSQL (fixtures has them built in)
#   make lint-data   # the data-quality rules over the fixture corpus
#   make dev-web     # Next.js on :3000
#   make db-up       # PostGIS in Docker, then `make migrate seed`
#   make db-test     # the azir_test database for `make test-postgis`
#
# Override the interpreter for a prebuilt environment:
#   make check PYTHON=/path/to/venv/bin/python

# Prefer the project venv once `make install` has created it, so the documented commands work
# without `PYTHON=…` on every line; fall back to whatever python3 is on PATH.
# Commands below often `cd backend`; keep the interpreter absolute so the project venv remains
# reachable after that directory change.
PYTHON      ?= $(if $(wildcard $(VENV)/bin/python),$(abspath $(VENV)/bin/python),python3)
NPM         ?= npm
BACKEND     := backend
FRONTEND    := frontend
VENV        := .venv
DB_URL      ?= postgresql+psycopg2://azir:azir@localhost:5432/azir
TEST_DB_URL ?= postgresql+psycopg2://azir:azir@localhost:5432/azir_test
# Absolute, because several targets cd into $(BACKEND) before using it.
FIXTURES    ?= $(CURDIR)/$(BACKEND)/seeds/fixtures
TILES_DIR   ?= $(CURDIR)/$(BACKEND)/tiles
# z0..z10 is 1010 tiles / ~850 kB per locale for the pilot corpus; raise it as the data grows.
TILES_MAX_ZOOM ?= 10
# Labels are baked into tiles, so every locale the atlas ships needs its own archive (ADR-0018).
TILES_LOCALES ?= fa en
UVICORN     := $(PYTHON) -m uvicorn

.DEFAULT_GOAL := help
.PHONY: help install venv lint typecheck test test-postgis check clean smoke wsl-setup \
        migrate seed doctor dev-backend dev-web build db-up db-down db-logs db-test \
        dev-users users lint-data tiles tiles-verify tiles-inspect

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

venv: ## Create .venv and install the backend with dev extras
	@test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip >/dev/null
	$(VENV)/bin/pip install -e "$(BACKEND)[dev]"

wsl-setup: ## Bootstrap a Linux/WSL2 machine (see scripts/wsl-setup.sh --help)
	./scripts/wsl-setup.sh --with-apt

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
	cd $(BACKEND) && AZIR_FIXTURES_DIR="$(FIXTURES)" AZIR_DB_URL="$(DB_URL)" \
	  $(PYTHON) -m azir.cli --human-logs doctor

dev-backend: ## API with reload (fixtures by default; AZIR_DB_DRIVER=postgis for real spatial SQL)
	cd $(BACKEND) && AZIR_FIXTURES_DIR="$(FIXTURES)" AZIR_TILES_DIR="$(TILES_DIR)" \
	  AZIR_DB_URL="$(DB_URL)" AZIR_DEBUG=true \
	  $(UVICORN) azir.main:app --app-dir src --host 0.0.0.0 --port 8000 --reload

dev-web: ## Next.js dev server
	cd $(FRONTEND) && $(NPM) run dev

dev-users: ## Create the four development logins in PostgreSQL (idempotent)
	@echo "password comes from $$AZIR_DEV_PASSWORD (default: atlas-dev-password)"
	cd $(BACKEND) && AZIR_DB_DRIVER=postgis AZIR_DB_URL="$(DB_URL)" AZIR_FIXTURES_DIR="$(FIXTURES)" \
	  $(PYTHON) -m azir.cli --human-logs user sync-dev

users: ## List editorial accounts (PostgreSQL)
	cd $(BACKEND) && AZIR_DB_DRIVER=postgis AZIR_DB_URL="$(DB_URL)" \
	  $(PYTHON) -m azir.cli --human-logs user list

lint-data: ## Data-quality rules over the corpus (docs/07 §3); exit 1 if anything blocks
	cd $(BACKEND) && AZIR_FIXTURES_DIR="$(FIXTURES)" AZIR_DB_URL="$(DB_URL)" \
	  $(PYTHON) -m azir.cli --human-logs lint

tiles: ## Build the static tile archives, one per locale (PostgreSQL when configured)
	@for locale in $(TILES_LOCALES); do \
	  echo "→ locale $$locale"; \
	  (cd $(BACKEND) && AZIR_FIXTURES_DIR="$(FIXTURES)" AZIR_TILES_DIR="$(TILES_DIR)" \
	    AZIR_DB_URL="$(DB_URL)" \
	    $(PYTHON) -m azir.cli --human-logs tiles build --locale $$locale \
	      --max-zoom $(TILES_MAX_ZOOM)) || exit 1; \
	done
	@echo "the API and the map pick them up on the next request; verify with \`make tiles-verify\`"

tiles-verify: ## Re-read every built archive with the reference (browser) PMTiles reader
	@ARCHIVES=$$(ls -t $(TILES_DIR)/*.pmtiles 2>/dev/null); \
	if [ -z "$$ARCHIVES" ]; then echo "no archive in $(TILES_DIR) -- run \`make tiles\` first"; exit 1; fi; \
	node scripts/verify-pmtiles.mjs $$ARCHIVES

tiles-inspect: ## Report the contents of the built archive, per zoom level
	@ARCHIVE=$$(ls -t $(TILES_DIR)/*.pmtiles 2>/dev/null | head -1); \
	if [ -z "$$ARCHIVE" ]; then echo "no archive in $(TILES_DIR) -- run \`make tiles\` first"; exit 1; fi; \
	cd $(BACKEND) && AZIR_FIXTURES_DIR="$(FIXTURES)" AZIR_DB_URL="$(DB_URL)" \
	  $(PYTHON) -m azir.cli --human-logs tiles inspect "$$ARCHIVE"

db-up: ## PostGIS in Docker
	docker compose up -d db
	@echo "waiting for postgres..."; sleep 5; docker compose ps db

db-test: ## Create the azir_test database that `make test-postgis` runs against (idempotent)
	@docker compose exec -T db psql -U azir -d postgres -tc \
	  "SELECT 1 FROM pg_database WHERE datname = 'azir_test'" | grep -q 1 \
	  || docker compose exec -T db createdb -U azir azir_test
	@docker compose exec -T db psql -U azir -d azir_test -c \
	  "CREATE EXTENSION IF NOT EXISTS postgis" >/dev/null
	@echo "azir_test ready (PostGIS enabled)"

db-down: ## Stop the database (keeps the volume)
	docker compose stop db

db-logs: ## Tail the API logs
	docker compose logs -f api

clean: ## Remove build artefacts (never touches data)
	rm -rf $(FRONTEND)/.next $(FRONTEND)/tsconfig.tsbuildinfo $(TILES_DIR)
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	find . -name '.pytest_cache' -o -name '.mypy_cache' -o -name '.ruff_cache' | xargs rm -rf 2>/dev/null || true
