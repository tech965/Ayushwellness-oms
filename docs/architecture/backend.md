# Backend Architecture

FastAPI application at `apps/api`, Python 3.12, async SQLAlchemy 2.x.

## Layout

```
apps/api/
├── app/
│   ├── api/
│   │   ├── health.py           # /health, /health/ready
│   │   └── v1/
│   │       ├── router.py       # aggregates every route group
│   │       ├── endpoints/      # one file per module (orders.py, ndr.py, ...)
│   │       └── webhooks/       # shopify.py, shiprocket.py, couriers.py (Phase 2)
│   ├── core/                   # config, security, logging, exceptions, rate_limit
│   ├── db/                     # session, declarative base (GUID/AwareDateTime/JSONType
│   │                           #   cross-dialect types), health checks
│   ├── models/                 # SQLAlchemy ORM models — enums.py, mixins.py
│   │                           #   (SyncMetadataMixin), one file per domain
│   ├── schemas/                # Pydantic request/response schemas, one file per domain
│   ├── services/                # business logic, one file per domain
│   ├── repositories/           # SQLAlchemy queries — base.py has the generic
│   │                           #   CRUD + upsert_by_external_id every domain reuses
│   ├── integrations/           # shopify/, shiprocket/, couriers/ adapters (Phase 2)
│   ├── intelligence/            # future AI/ML service boundaries (Phase 4/6)
│   ├── workers/                # Celery app
│   ├── tasks/                  # Celery task definitions (Phase 2+)
│   ├── middleware/             # error handling, request logging, security headers
│   ├── dependencies/           # auth.py (get_current_user/require_permission),
│   │                           #   pagination.py (shared page/sort query params)
│   └── main.py                 # app factory
├── alembic/                    # migrations
└── tests/
```

## Layering rule

```
Route (app/api) → Schema (app/schemas) → Service (app/services)
   → Repository (app/repositories) → SQLAlchemy model (app/models) → PostgreSQL
```

Routes validate input via Pydantic and call exactly one service method.
Business rules (status transition validity, RBAC checks, atomic
multi-record writes, audit logging) live in services, never in route
handlers or repositories. This is enforced by convention and code
review, not by a lint rule — see `docs/development/setup.md` for the
review checklist.

## Synchronization-ready seam (for Phase 2)

Phase 1 builds the OMS-side half of the pipeline every external
integration will plug into:

```
External API/Webhook  →  Integration Adapter  →  Normalizer  →  [ this seam ]
                                                                       ↓
                                          OMS Service → Repository → PostgreSQL
```

Two things make that seam idempotent and ready for Phase 2 without
rework:

- **`SyncMetadataMixin`** (`app/models/mixins.py`) — every model that
  will be externally sourced (Customer, Product, Order, Payment,
  Shipment, NDR, RTO, Return, Refund, ...) carries `source_system`,
  `external_id`, `external_created_at`, `external_updated_at`,
  `last_synced_at`, `sync_version`, `raw_external_payload`.
- **`BaseRepository.upsert_by_external_id()`** (`app/repositories/base.py`)
  — the idempotent create-or-update every adapter will call, keyed by
  `(source_system, external_id)`. It's implemented as select-then-write
  rather than Postgres `ON CONFLICT`, specifically so it's dialect-portable
  and the exact same path is exercised by the SQLite test suite as
  production Postgres.

`ShipmentEvent`/`OrderEvent`/`AuditLog` are append-only by construction
— their repositories (`AppendOnlyRepository`) expose `create`/`list`
only, so "never overwrite history" is a type-level guarantee, not just a
convention.

## Configuration

`app/core/config.py` defines a single `Settings` (Pydantic Settings) object
read from environment variables / `.env`. Every external integration
credential is `str | None` — a missing Shopify or Shiprocket credential
must never prevent the API from starting; the integration simply reports
itself as `DISCONNECTED` (see `docs/integrations/shopify.md`).

## Logging

`app/core/logging.py` configures `structlog` with JSON output in
production and a redaction processor that strips any field named
`password`, `token`, `secret`, `api_key`, etc. before it reaches a log
sink. Every request is logged once (method, path, status, duration,
request ID) by `RequestContextMiddleware`.

## Error handling

`app/middleware/error_handler.py` maps:

- `app.core.exceptions.OMSError` subclasses (domain errors) → their own
  `status_code` / `error_code`
- `RequestValidationError` (Pydantic) → `422 validation_error`
- `StarletteHTTPException` → passthrough with the standard envelope
- Anything else → `500 internal_error` with **no stack trace** in the
  response body (the traceback is logged server-side only)

All error responses use the same envelope as success responses — see
`docs/api/api-conventions.md`.

## Security

- Passwords: Argon2 via `passlib` (`app/core/security.py`)
- Tokens: JWT (HS256), short-lived access tokens + persisted, revocable
  refresh tokens (`RefreshToken` model)
- Authorization: `require_permission(...)` dependency
  (`app/dependencies/auth.py`) — see `docs/security/rbac.md`
- Rate limiting: `slowapi`, Redis-backed, configurable via
  `RATE_LIMIT_ENABLED` / `RATE_LIMIT_DEFAULT`
- Security headers: `SecurityHeadersMiddleware` sets
  `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
  `Permissions-Policy`, `Strict-Transport-Security` on every response
- CORS: explicit origin allowlist via `CORS_ORIGINS`, never `*` with
  credentials

See `docs/security/rbac.md` and `docs/security/audit-logs.md`.

## Cross-dialect database types (`app/db/base.py`)

The test suite runs against in-memory SQLite (`aiosqlite`) while
production runs Postgres, so three custom types keep model behavior
identical across both:

- **`GUID`** — native `postgresql.UUID` on Postgres, a 32-char hex
  `CHAR` column elsewhere.
- **`AwareDateTime`** — wraps `DateTime(timezone=True)`; SQLite silently
  drops tzinfo on round-trip, which broke any comparison against
  `datetime.now(UTC)` (e.g. `RefreshToken.is_active`) until this was
  added.
- **`JSONType`** — plain `JSON`, with a Postgres `JSONB` variant.

Status enums use `sqlalchemy.Enum` directly (`app/models/enums.py:sa_enum()`)
rather than a custom type — it already renders as a native Postgres enum
in production and `VARCHAR` + `CHECK` on SQLite with no extra code,
reusing one Postgres enum type per name across every table/column that
shares it instead of emitting a duplicate `CREATE TYPE`.

## Background jobs

`app/workers/celery_app.py` configures Celery against Redis
(`CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND`, defaulting to
`REDIS_URL`). `task_acks_late=True` and a default retry policy are set at
the app level so every task implemented from Phase 2 onward is retryable
and idempotent by default — see `app/tasks/__init__.py`.

## Why FastAPI's own OpenAPI docs are disabled in production

`docs_url` / `redoc_url` / `openapi_url` are only enabled when
`ENVIRONMENT != "production"` (`app/main.py`) to avoid exposing the full
API surface publicly by default.
