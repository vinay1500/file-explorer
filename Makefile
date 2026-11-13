# File Explorer — development commands.
# Run `make` or `make help` for the list.

SHELL := /bin/bash
COMPOSE := docker compose
API := $(COMPOSE) exec -T api
WEB := $(COMPOSE) exec -T web

.DEFAULT_GOAL := help
.PHONY: help db up down restart logs ps shell-api shell-db \
        migrate revision test-api test-web test-e2e test \
        lint format typecheck openapi verify-guards a11y ci clean

# apps/ arrives with the subtree graft. Until then, fail with a useful message
# instead of a confusing build error.
define need-apps
	@test -d apps/api || { \
		echo ""; \
		echo "  apps/api is not here yet."; \
		echo "  It arrives with the subtree graft — see .state/RESUME.md, task M1.3."; \
		echo "  Until then: make db  (postgres runs on its own)"; \
		echo ""; \
		exit 1; }
endef

help: ## Show this help
	@echo ""
	@echo "  File Explorer"
	@echo ""
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ---------------------------------------------------------------- stack

db: ## Start postgres only (works before the graft)
	$(COMPOSE) up -d postgres
	@echo "  postgres ready on localhost:5432  db=file_explorer  user=fileexplorer"

up: ## Start the whole stack
	$(need-apps)
	$(COMPOSE) up -d --build
	@echo "  web http://localhost:5173    api http://localhost:5000"

down: ## Stop everything, keep the data
	$(COMPOSE) down

restart: down up ## Restart the stack

logs: ## Tail all logs
	$(COMPOSE) logs -f --tail=100

ps: ## Show container status
	$(COMPOSE) ps

shell-api: ## Shell inside the api container
	$(COMPOSE) exec api /bin/bash

shell-db: ## psql inside the database
	$(COMPOSE) exec postgres psql -U fileexplorer -d file_explorer

# ---------------------------------------------------------------- database

migrate: ## Apply migrations to the local database
	$(need-apps)
	$(API) alembic upgrade head

revision: ## New migration — make revision m="add owner_id"
	$(need-apps)
	@test -n "$(m)" || { echo "  usage: make revision m=\"what changed\""; exit 1; }
	$(API) alembic revision --autogenerate -m "$(m)"

# ---------------------------------------------------------------- tests

test-api: ## API tests against real postgres
	$(need-apps)
	$(API) pytest -q

test-web: ## Web unit tests
	$(need-apps)
	$(WEB) npm run test -- --run

test-e2e: ## Playwright, against the running stack
	$(need-apps)
	$(WEB) npx playwright test

test: test-api test-web ## Unit and integration tests

# ---------------------------------------------------------------- quality

lint: ## Ruff, ESLint, Prettier
	$(need-apps)
	$(API) ruff check .
	$(WEB) npm run lint

format: ## Apply formatting
	$(need-apps)
	$(API) ruff format .
	$(WEB) npm run format

typecheck: ## mypy and tsc
	$(need-apps)
	$(API) mypy src
	$(WEB) npx tsc --noEmit

openapi: ## Regenerate the OpenAPI document and the web client types
	$(need-apps)
	$(API) python -m file_explorer.cli openapi > apps/api/openapi.json
	$(WEB) npx openapi-typescript /app/../api/openapi.json -o src/api/generated/schema.d.ts

a11y: ## axe-core against the running web app
	$(need-apps)
	$(WEB) npx playwright test --grep @a11y

verify-guards: ## Revert each guard and prove the suite catches it
	$(need-apps)
	./scripts/verify-guards.sh

ci: lint typecheck test verify-guards ## Everything CI runs

clean: ## Remove containers and the database volume — destroys local data
	$(COMPOSE) down -v
