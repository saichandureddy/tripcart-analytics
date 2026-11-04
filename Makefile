# Shortcuts for everyday commands. Run `make` or `make help` to list them.
# Each target is a plain docker compose command, so you can also run that
# command yourself (see README).

.DEFAULT_GOAL := help
.PHONY: help env build up down restart ps logs check psql-app psql-dwh clean simulate load status test dbt

help: ## List the available commands
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-10s %s\n", $$1, $$2}'

env: ## Create .env from .env.example with generated secrets
	@./scripts/make_env.sh

build: ## Build the Airflow image (after changing requirements.txt or the Dockerfile)
	docker compose build

up: .env ## Start everything and wait until all services are healthy
	docker compose up -d --build --wait

down: ## Stop everything (data is kept)
	docker compose down

restart: down up ## Stop and start again

ps: ## Show each container and its health
	docker compose ps -a

logs: ## Follow the logs of all services (Ctrl+C to stop)
	docker compose logs -f --tail=100

check: ## Check that every service is up and reachable
	@./scripts/check_stack.sh

psql-app: ## Open a SQL prompt on the app database
	docker compose exec postgres-app sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

psql-dwh: ## Open a SQL prompt on the warehouse
	docker compose exec postgres-dwh sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

# --- Pipeline (milestone 2) ---------------------------------------------------
# These run inside the Airflow scheduler container, which already has Python
# and every package from requirements.txt, so nothing extra goes on your laptop.
PY = docker compose exec -w /opt/tripcart airflow-scheduler /opt/tripcart-venv/bin/python

simulate: ## Generate TripCart activity since the last run (the first run makes 90 days)
	$(PY) -m simulator

load: ## Run all three loaders into the warehouse's raw schema
	$(PY) -m ingestion all

status: ## Show row counts in raw, watermarks and the last loader runs
	$(PY) -m ingestion status

test: ## Run the tests (they use throwaway databases, your data is safe)
	$(PY) -m pytest -v

# --- dbt (milestone 3) -----------------------------------------------------------
# dbt also runs inside the scheduler container. Pass the dbt command in cmd=,
# for example: make dbt cmd=debug   or   make dbt cmd="run --select stg_customers"
DBT = docker compose exec -w /opt/tripcart/dbt airflow-scheduler /opt/tripcart-venv/bin/dbt
cmd ?= --help

dbt: ## Run a dbt command, e.g. make dbt cmd=debug
	$(DBT) $(cmd)

clean: ## Stop everything and DELETE all data volumes (fresh start)
	docker compose down -v --remove-orphans

.env:
	@echo "No .env file yet. Run 'make env' first, then edit the passwords in .env."
	@exit 1
