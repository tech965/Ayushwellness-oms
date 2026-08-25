# Local Development Setup

## Prerequisites

- Node.js 20+ and npm
- Python 3.12+
- Docker Desktop (for Postgres/Redis; optional if you run them natively)

## First-time setup

```bash
cp .env.example .env
cp apps/web/.env.example apps/web/.env.local
bash scripts/setup.sh   # creates apps/api/.venv, installs backend + frontend deps
```

On Windows, `scripts/*.sh` run fine under Git Bash. PowerShell-native
equivalents are one-liners:

```powershell
py -3.12 -m venv apps/api/.venv
apps\api\.venv\Scripts\python.exe -m pip install -e "apps/api[dev]"
npm --prefix apps/web install
```

## Start Postgres + Redis

```bash
docker compose up postgres redis -d
```

## Run the apps

```bash
bash scripts/api-dev.sh    # FastAPI on http://localhost:8000 (--reload)
bash scripts/web-dev.sh    # Next.js on http://localhost:3000
```

Or the full stack via Docker (also builds `api`, `worker`, `web`):

```bash
docker compose up --build
```

## Database migrations

```bash
bash scripts/db-migrate.sh "add orders table"   # generate a new revision
bash scripts/db-upgrade.sh                       # apply pending revisions
bash scripts/db-seed.sh                          # run the dev seed script
```

No migrations exist yet — `apps/api/alembic/` is wired to `Settings` and
`app.db.base.Base`, but the first revision is created in Phase 1 once
models exist.

## Checks

```bash
bash scripts/lint.sh        # ruff + eslint
bash scripts/format.sh      # black + prettier (writes changes)
bash scripts/typecheck.sh   # mypy + tsc --noEmit
bash scripts/test.sh        # pytest + vitest
bash scripts/build.sh       # backend import smoke test + next build
```

Each of these can also be run per-app directly — see
`apps/api/pyproject.toml` (`[tool.ruff]`, `[tool.black]`, `[tool.mypy]`,
`[tool.pytest.ini_options]`) and `apps/web/package.json` (`scripts`).

## Health checks

- `GET http://localhost:8000/health` — liveness
- `GET http://localhost:8000/health/ready` — readiness (checks Postgres + Redis)

## Testing

Backend: pytest + pytest-asyncio + httpx `AsyncClient` against the FastAPI
app in-process (`apps/api/tests/conftest.py`). Frontend: Vitest + React
Testing Library (`apps/web/vitest.config.ts`).

The Phase 1 test plan (see `docs/roadmap.md`) adds coverage for
authentication, RBAC, order/customer/shipment creation, shipment event
timeline reconstruction, webhook idempotency (duplicate delivery must not
duplicate records), delay detection, NDR/RTO workflows, and audit logging
— the test patterns established in `apps/api/tests/test_health.py` and
`test_error_handling.py` (fixtures, envelope assertions) carry forward
unchanged.
