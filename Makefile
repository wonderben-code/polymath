SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
export UV_CACHE_DIR ?= /tmp/polymath-uv-cache
export npm_config_cache ?= /tmp/polymath-npm-cache
export BUILDX_CONFIG ?= /tmp/polymath-buildx
UV := uv --project backend

.PHONY: install db-up db-stop migrate api web api-client publication-worker literature-worker meta-probe-worker check test integration build image compose-up compose-stop

install:
	$(UV) sync --locked
	npm --prefix web ci

db-up:
	docker compose up -d --wait db

db-stop:
	docker compose stop db

migrate:
	$(UV) run alembic -c backend/alembic.ini upgrade head

api:
	$(UV) run uvicorn polymath.main:app --host 127.0.0.1 --port 8000 --reload

web:
	npm --prefix web run dev -- --host 127.0.0.1

api-client:
	python3 scripts/polymath-client.py --help

publication-worker:
	$(UV) run python -m polymath.publication_runtime

literature-worker:
	$(UV) run python -m polymath.ingestion_runtime

meta-probe-worker:
	$(UV) run python -m polymath.meta_probe_worker

check:
	$(UV) run ruff check backend
	$(UV) run ruff format --check backend
	$(UV) run ruff check --config backend/pyproject.toml scripts/polymath-client.py
	$(UV) run ruff format --check --config backend/pyproject.toml scripts/polymath-client.py
	npm --prefix web run typecheck

test:
	$(UV) run pytest backend/tests -m 'not integration'
	npm --prefix web test -- --run

integration:
	@test -n "$${TEST_DATABASE_URL:-}" || { echo 'Set TEST_DATABASE_URL to a disposable PostgreSQL database.'; exit 1; }
	$(UV) run pytest backend/tests -m integration

build:
	npm --prefix web run build

image:
	bash scripts/build-image.sh

compose-up: image
	docker compose --profile app up -d --no-build --wait

compose-stop:
	docker compose --profile app stop
