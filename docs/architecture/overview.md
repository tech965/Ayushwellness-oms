# Architecture Overview

## What this system is

AyushWellness OMS is an **Operations Intelligence Platform**, not a simple
order tracker. The end goal (see `docs/roadmap.md` for phasing) is for the
system to answer, for the operations team:

1. What happened?
2. What is going wrong?
3. Why is it happening?
4. Which courier performs best for a given location?
5. What action should the team take?

## Layered data flow

```
EXTERNAL DATA SOURCES (Shopify, Shiprocket, couriers, WhatsApp, Meta)
            │
            ▼
   INTEGRATION LAYER        (apps/api/app/integrations/*)
            │
            ▼
   NORMALIZATION LAYER      (per-integration Normalizer classes)
            │
            ▼
       OMS CORE             (apps/api/app/services, repositories, models)
            │
            ▼
       PostgreSQL
            │
   ┌────────┼────────┐
   ▼        ▼        ▼
Dashboard Analytics Intelligence
   │        │        │
   └────────┼────────┘
            ▼
   Automation / Actions      (apps/api/app/tasks — Phase 5)
            ▼
   Future AI / ML Layer      (apps/api/app/intelligence — Phase 6)
```

## Core principle: the OMS core never depends on an external API shape

Shopify, Shiprocket, and each courier have different field names and status
vocabularies. Nothing outside `app/integrations/<provider>/` may reference
a Shopify or Shiprocket field name directly. Every integration exposes:

- **Client** — thin HTTP wrapper around the provider's API/webhooks.
- **Config** — provider credentials read from environment variables only.
- **Adapter** — implements a shared interface (e.g. the future
  `CourierAdapter`) so OMS services can call couriers interchangeably.
- **WebhookHandler** — verifies signatures, persists a `WebhookEvent` for
  idempotency, and hands normalized data to a service.
- **SyncService** — pull-based synchronization (backed by a Celery task).
- **Normalizer** — maps provider-specific values to OMS-internal enums
  (see `docs/integrations/*.md`), while the raw payload is still stored
  for audit/debugging.

This is why adding Blue Dart, Delhivery, or Ecom Express later should never
require changing `app/services/shipments.py` or any OMS-core code.

## Request flow inside the API

```
HTTP request
   → API route (app/api/v1/endpoints/*)   — thin, no business logic
   → Schema validation (Pydantic, app/schemas/*)
   → Service (app/services/*)             — business rules live here
   → Repository (app/repositories/*)      — SQLAlchemy queries only
   → PostgreSQL
```

Routes never talk to the database directly, and services never construct
raw SQL — this keeps business rules unit-testable without a database and
keeps persistence logic swappable.

## Why history is append-only

`ShipmentEvent` rows are never updated or deleted — every courier tracking
update inserts a new row. The `Shipment` table holds only the *current*
state (a denormalized projection for fast reads); the event table is the
source of truth for the full timeline. The same append-only principle
applies to `OrderEvent` and `AuditLog`. See
`docs/database/schema.md#shipmentevent` for the column list.

## Idempotency

Every webhook and sync job is idempotent by construction: inbound events
are keyed by the external system's event ID and recorded in
`WebhookEvent` before any side effect runs. A duplicate Shopify order
webhook or a replayed Shiprocket tracking update must never create a
duplicate `Order` or `ShipmentEvent`. This is covered by
`docs/development/setup.md#testing` (webhook idempotency tests are part of
the Phase 1 test plan).

## Future AI/ML layer

`app/intelligence/` defines service boundaries
(`CourierRecommendationService`, `DeliveryPredictionService`,
`RTOPredictionService`, `DelayPredictionService`,
`OperationsAssistantService`, `RootCauseAnalysisService`) that are empty in
Phase 0. When implemented (Phase 4/6), these services will **read**
normalized OMS data through the existing repository/service layer and
**never** write to the database directly or bypass RBAC — they call the
same services a human operator would.

## Related documents

- `docs/architecture/backend.md` — FastAPI module layout in detail.
- `docs/architecture/frontend.md` — Next.js app structure.
- `docs/architecture/integrations.md` — integration layer conventions.
- `docs/database/schema.md` — full entity design.
- `docs/security/rbac.md` — roles and permission model.
