# AyushWellness OMS — Operations Intelligence Platform

A production-oriented Order Management System that centralizes Shopify,
Shiprocket, Blue Dart and other couriers, customers, orders, shipments,
NDR/RTO, returns/refunds, and (in later phases) WhatsApp/Instagram/Meta,
analytics, automation, and AI-driven courier/delay/RTO intelligence.

Built in phases — see [`docs/roadmap.md`](docs/roadmap.md) for what's
implemented vs. planned. **Phase 0 (foundation), Phase 1 (core OMS),
Phase 2.1 (integration & sync foundation), Phase 2.2 (Shopify
integration), Phase 2.3 (Shiprocket integration), and Phase 2.4 (E2E
integration & reconciliation) are all implemented:** authentication,
RBAC, customers, products, orders, payments, shipments, NDR/RTO,
returns/refunds, and audit logs all have real database models, a real
API, and a real frontend; on top of that, the reusable
integration/sync/webhook infrastructure (`Integration`, `SyncJob`,
`SyncError`, `WebhookEvent`, retry/idempotency, Celery tasks, and an
`/integrations` monitoring UI) has two real providers plugged in —
Shopify (GraphQL Admin API, customer/product/order sync, and a real
webhook endpoint with HMAC verification; see
[`docs/integrations/shopify.md`](docs/integrations/shopify.md)) for
commerce, and Shiprocket (REST API, shipment creation, AWB assignment,
tracking, NDR/RTO; see
[`docs/integrations/shiprocket.md`](docs/integrations/shiprocket.md))
for logistics. Phase 2.4 chains the two together end-to-end and adds a
**reconciliation engine** (`/reconciliation`) that audits OMS records
against both providers and reports mismatches — it never auto-corrects
them; see
[`docs/architecture/integrations.md#reconciliation`](docs/architecture/integrations.md#reconciliation).
**No live direct-courier (Blue Dart/Delhivery/Ecom Express outside
Shiprocket) API call happens anywhere yet — that's a later phase.**

## Stack

- **Frontend**: Next.js 16 (App Router), TypeScript, Tailwind CSS v4,
  shadcn/ui, TanStack Query, React Hook Form + Zod, Recharts
- **Backend**: FastAPI, Python 3.12, SQLAlchemy 2.x (async), Alembic,
  PostgreSQL, Redis, Celery
- **Infra**: Docker Compose

## Project structure

```
apps/
  web/          Next.js frontend
  api/          FastAPI backend
packages/       Shared code (placeholders — see packages/*/README.md)
docs/           Architecture, database, API, integrations, security, roadmap
docker/         Dockerfiles (api, worker, web)
scripts/        Dev/test/lint/format/typecheck/build/db scripts
docker-compose.yml
.env.example
```

## Quick start

```bash
cp .env.example .env
cp apps/web/.env.example apps/web/.env.local
bash scripts/setup.sh
docker compose up postgres redis -d
bash scripts/db-upgrade.sh        # apply the Phase 1 schema
python apps/api/scripts/seed.py   # roles, permissions, dev admin, couriers
bash scripts/api-dev.sh    # http://localhost:8000
bash scripts/web-dev.sh    # http://localhost:3000
```

The seed script prints the dev admin email; the password comes from
`ADMIN_SEED_PASSWORD` (or its documented dev-only default — see the
script) and must never be reused as a production credential.

Full setup, environment variable reference, and check commands:
[`docs/development/setup.md`](docs/development/setup.md),
[`docs/development/environment.md`](docs/development/environment.md).

## Documentation

- [`docs/roadmap.md`](docs/roadmap.md) — phases, what's implemented/planned/todo
- [`docs/architecture/overview.md`](docs/architecture/overview.md) — system design
- [`docs/architecture/backend.md`](docs/architecture/backend.md) · [`frontend.md`](docs/architecture/frontend.md) · [`integrations.md`](docs/architecture/integrations.md)
- [`docs/database/schema.md`](docs/database/schema.md) — entity design
- [`docs/api/api-conventions.md`](docs/api/api-conventions.md) · [`authentication.md`](docs/api/authentication.md)
- [`docs/integrations/shopify.md`](docs/integrations/shopify.md) · [`shiprocket.md`](docs/integrations/shiprocket.md) · [`couriers.md`](docs/integrations/couriers.md)
- [`docs/security/rbac.md`](docs/security/rbac.md) · [`audit-logs.md`](docs/security/audit-logs.md)
