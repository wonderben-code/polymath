# Developing Polymath

The current release is the application foundation, with a separately tested research-controller prototype. Live research is not enabled. Follow [BUILD_PLAN.md](BUILD_PLAN.md) and [PROGRESS.md](PROGRESS.md) for implementation and verification status.

## Prerequisites

- Python 3.12, uv 0.12.23, Node.js 24, npm, Make.
- Docker Engine and Docker Compose v2 for the local PostgreSQL database and packaged application.
- Existing checkout; no worktree is needed. Application secrets belong in secure runtime settings or a local ignored `.env`, never Git.

Use the repository root as the working directory. Installation uses `backend/uv.lock` and `web/package-lock.json`. Make directs caches to `/tmp` so setup does not require a writable home directory.

## Local development

```bash
# Preserve any existing .env; the example contains only local development values.
test -e .env || cp .env.example .env
make install
make db-up
make migrate
make api
```

In another terminal at the repository root, run `make web`. The Vite development server proxies `/api` to the backend on port 8000. Both development servers bind loopback. Their terminal output gives the local addresses; cloud onboarding does not provide public previews for these addresses.

The initial page supports selecting Explore, Ask, and Surprise Me and displays actual application/database/configuration status. It does not submit research jobs, charge API credits, or invent results. A configured key alone does not enable a feature that has not passed its execution and budget gates.

The development database is PostgreSQL 17 with local-only credentials `polymath`/`polymath`, exposed on loopback port 5432. These are not production credentials. Compose keeps data in a named volume. `make db-stop` stops it without removing data; do not use destructive volume-removal commands on a database containing work you need.

## Verification

```bash
make check
make test
TEST_DATABASE_URL=postgresql+psycopg://polymath:polymath@127.0.0.1:5432/polymath make integration
make build
```

Integration tests require PostgreSQL and create their own isolated schema, then clean up that schema. Use a disposable local/test database, never a production database. Tests cover actual migration upgrade/repeatability, schema drift, persistence, constraints, downgrade/re-upgrade, and readiness. Without `TEST_DATABASE_URL`, integration tests are skipped; `make integration` explicitly fails if the variable is absent.

Research-controller tests use fixture models and injected tools. They validate contracts and decisions without paid calls; they do not establish live Meta protocol compatibility or scientific validity. CI runs backend tests with PostgreSQL, frontend tests/types, and the production frontend build. CI's status is distinct from locally passing these commands.

## Packaged application

```bash
make compose-up
```

This builds the frontend/backend image, starts the database, runs migrations as a separate one-shot service, and starts the API serving the built frontend on loopback port 8000. It is a local packaging check, not public deployment. Images are digest-pinned; dependency locks are retained. The application runs as a non-root user. Migration and application containers use the database's private Compose hostname.

The foundation's Compose application deliberately does not forward model credentials. Local process configuration and container configuration are separate; model bindings will be added to the worker when live execution controls are implemented.

`make image` builds the image separately. In Codex cloud, the build helper mounts the injected proxy CA for package downloads when available. TLS verification remains enabled; the CA is not committed or retained in image layers. Outside this cloud, ordinary system certificate trust is used. A failed certificate check must be corrected through trust configuration, never bypassed.

`make compose-stop` stops the stack without deleting the database volume. Stop a separately launched local API before starting the packaged app because they share port 8000. There is no research worker/scheduler service yet; those arrive with the durable-job tasks.

## Health and configuration

- `GET /api/v1/health`: process liveness; does not imply database readiness.
- `GET /api/v1/readiness`: queries PostgreSQL and compares the actual Alembic revision with the packaged migration head; returns 503 for unavailable/pending dependencies.
- `GET /api/v1/capabilities`: explicitly reports research unavailable and whether both key/model settings are present; configuration presence is not verified authentication.
- FastAPI's generated `/docs` describes implemented routes. There is no public research-submission endpoint yet.

Settings load from the process and optionally `.env` in the working directory. `DATABASE_URL` must use `postgresql+psycopg`. `STATIC_DIR` (or `FRONTEND_DIST`) enables serving a built frontend. API routes that do not exist return JSON 404s instead of the SPA page.

## Meta integration prerequisites

`MODEL_API_KEY` and an explicitly verified `META_MODEL` are required for real model access. The prototype targets the repository's proposed HTTPS endpoint on `api.meta.ai`; actual capabilities and model IDs still need live verification. No model ID is guessed by default, no non-Meta fallback is configured, and no paid calls run from startup, CI, or the web page.

Before an explicitly bounded live experiment: verify current pricing and account limits, supply authentication securely, and verify runtime access. Before enabling public investigations: implement and test durable jobs, atomic spending reservations, quotas, and operational authorization. The prototype's call/token limits are not a substitute for those controls. Read the research package's usage notes before invoking it manually.

Network destinations identified so far: `api.meta.ai`, `dev.meta.ai` for model documentation, `api.openalex.org` for discovery, and `export.arxiv.org`/`arxiv.org` for accessible literature. Saved environment requirements do not establish runtime connectivity or provider authorization.
