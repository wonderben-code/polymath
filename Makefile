SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
export UV_CACHE_DIR ?= /tmp/polymath-uv-cache
export npm_config_cache ?= /tmp/polymath-npm-cache
export BUILDX_CONFIG ?= /tmp/polymath-buildx
UV := uv --project backend

.PHONY: install db-up db-stop migrate api web publication-worker check test integration build image compose-up compose-stop

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

publication-worker:
	$(UV) run python -m polymath.publication_runtime

check:
	$(UV) run ruff check backend
	$(UV) run ruff format --check backend
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
