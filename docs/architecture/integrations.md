# Integration Architecture

Status: the generic infrastructure below is **IMPLEMENTED** and tested
as of Phase 2.1 (`Integration`/`SyncJob`/`SyncError`/`WebhookEvent`
models, `SyncService`, `WebhookService`, the
`IntegrationAdapter`/`Normalizer` interfaces, the credential/retry
abstractions, the Celery task skeletons, and the monitoring API/UI).
Phase 2.2 connected the first real provider — **Shopify is
IMPLEMENTED** (`apps/api/app/integrations/shopify/`; see
`docs/integrations/shopify.md` for its architecture, GraphQL field
mappings, and field-ownership rules in full). Phase 2.3 connected the
second — **Shiprocket is IMPLEMENTED** (`apps/api/app/integrations/shiprocket/`;
see `docs/integrations/shiprocket.md`) — the OMS Order → Shipment → AWB →
tracking → NDR/RTO logistics side, completing the
Shopify-owns-commerce / Shiprocket-owns-logistics split the source spec
calls for. Phase 2.4 added **reconciliation** — a read-only auditor that
compares OMS records against both providers and reports mismatches
without ever correcting them (`apps/api/app/services/reconciliation_service.py`;
see [Reconciliation](#reconciliation) below). The direct courier adapters
(`app/integrations/couriers/`) still contain only directory stubs — a
later phase, if a courier is ever integrated outside Shiprocket's
umbrella.

## The pattern

Every external system gets its own subpackage under
`app/integrations/<provider>/` with six pieces:

| Piece | Responsibility |
|---|---|
| `Client` | Thin HTTP wrapper: auth headers, retries (`tenacity`), timeouts. Knows nothing about OMS models. |
| `Config` | Reads `<PROVIDER>_*` env vars via `app.core.config.settings`. Never hardcoded. |
| `Adapter` | Implements `app.integrations.base.IntegrationAdapter` — the shared interface the OMS core calls. Registered into `app.integrations.registry` at startup so `SyncService`/`IntegrationService` can find it by `Integration.code`. |
| `WebhookHandler` | Verifies the provider's signature, then calls `app.services.webhook_service.WebhookService.ingest()` for the generic idempotency check before any provider-specific processing. |
| `SyncService` (Phase 1 already has this name for the generic one — a provider adapter's pull logic lives in its own `Adapter.fetch`/`fetch_incremental`, invoked by `app.services.sync_service.SyncService.execute_sync` via a Celery task in `app/tasks/`) | Pull-based sync, idempotent by external ID. |
| `Normalizer` | Subclasses `app.integrations.normalizer.{Customer,Product,Order,Shipment}Normalizer` — maps provider-specific field names/status strings to OMS-internal enums. |

## Generic infrastructure (Phase 2.1)

- `app/models/integration.py` — `Integration`, `SyncJob`, `SyncError`,
  `WebhookEvent` (see `docs/database/schema.md#integrations--sync-phase-21`).
- `app/integrations/base.py` — `IntegrationAdapter` ABC (`authenticate`,
  `health_check`, `fetch`, `fetch_incremental`, `process_webhook`,
  `normalize`) and `HealthCheckResult`.
- `app/integrations/normalizer.py` — `Normalizer` ABC and per-entity
  subclasses.
- `app/integrations/registry.py` — runtime `code -> IntegrationAdapter`
  map; empty until Phase 2 calls `register_adapter(...)`.
- `app/integrations/retry.py` — `RetryPolicy`, `should_retry`,
  `compute_backoff_seconds`; retryable vs. non-retryable error-type sets
  shared by every provider.
- `app/services/sync_service.py:SyncService` — starts/tracks/completes a
  `SyncJob`, updates `Integration` health fields. With no adapter
  registered, `execute_sync` records one `SyncError`
  (`error_type="integration_error"`) and completes the job `FAILED` —
  proving the pipeline without a live call.
- `app/services/webhook_service.py:WebhookService` — idempotent
  `ingest()` (see below).
- `app/tasks/{sync_tasks,webhook_processing,retry_processing}.py` —
  Celery tasks registered on `app.workers.celery_app.celery_app`; every
  task persists its own outcome to a DB row (never relies on Celery's
  result backend — `task_ignore_result=True`), so a Redis outage never
  turns an already-created `SyncJob`/`WebhookEvent` into a lost write.

## Credentials {#credentials}

No credential is stored in the database, in plain text or otherwise —
`Integration.configuration` holds only non-secret metadata (store
domain, sync cadence, ...). `app/integrations/credentials.py` resolves a
named credential at call time through a `CredentialProvider`; the
default `EnvCredentialProvider` reads `{CODE}_{KEY}` environment
variables (e.g. `SHOPIFY_ACCESS_TOKEN`). Swapping in a real secret
manager for production is a matter of implementing `CredentialProvider`
and changing `get_credential_provider()` — no adapter code changes.

## Idempotency contract

Both webhook delivery and pull-based sync must be safe to run twice with
the same input:

- Webhooks: `WebhookEvent` has a unique constraint on
  `(integration_id, external_event_id)`; `WebhookService.ingest()`
  checks-then-inserts before doing anything else, returning
  `(event, created=False)` on a duplicate — the caller treats that as a
  200 no-op, never a duplicate `Order`/`ShipmentEvent`. For providers
  without a stable event id, `compute_fallback_event_id()` hashes
  `(integration, event_type, payload)` deterministically so a retried
  delivery of an identical payload still collides on the same row.
- Sync jobs: upsert by external ID (`shopify_order_id`,
  `shiprocket_shipment_id`, AWB, ...) via
  `BaseRepository.upsert_by_external_id()`, never insert-only.

## Status normalization

Raw courier/Shopify status strings vary wildly (`"IN_TRANSIT"`,
`"Shipment In Transit"`, `"Manifested"`, `"Picked Up"`, ...). Each
`Normalizer` maps these into a small, fixed set of OMS-internal statuses
used everywhere else in the codebase (services, UI, analytics). The raw
payload is still stored (on `ShipmentEvent.raw_payload` /
`WebhookEvent.payload`) for audit and debugging, but nothing outside
the integration package should ever branch on a provider-specific
string. `WebhookEventResponse` deliberately excludes `payload` from the
monitoring API by default (spec: don't expose raw payload data on a
monitoring endpoint).

## Why the OMS core must not import a provider SDK

`app/services/*` and `app/models/*` depend only on the normalized enums
and the `CourierAdapter`-style interfaces — never on
`app.integrations.shopify` or `app.integrations.shiprocket` types
directly. This is what makes it possible to add Delhivery or Ecom Express
later without touching order/shipment business logic, and to unit-test
services with a fake adapter instead of hitting a real API.

## Webhook routes

```
POST /api/v1/webhooks/shopify              — IMPLEMENTED (Phase 2.2)
POST /api/v1/webhooks/shiprocket/tracking   — not implemented — see below
POST /api/v1/webhooks/shiprocket/ndr        — not implemented — see below
POST /api/v1/webhooks/couriers/{courier_code} — planned
```

Shopify uses a single endpoint dispatching on the `X-Shopify-Topic`
header rather than one route per resource — see
`docs/integrations/shopify.md#data-flow`. Shiprocket's webhook routes
remain empty routers deliberately, not provisionally: no reliable
webhook/callback contract for Shiprocket could be confirmed without a
live account, and the instruction that added Shiprocket support was
explicit — do not invent one. Shiprocket sync instead polls (a
dedicated tracking-refresh task; see
`docs/integrations/shiprocket.md#webhooks--none-implemented-spec-1920`).
The direct-courier routes remain reserved for a later phase.

## Monitoring routes (implemented, Phase 2.1)

```
GET  /api/v1/integrations
GET  /api/v1/integrations/{id}
GET  /api/v1/integrations/{id}/health
POST /api/v1/integrations/{id}/health-check
GET  /api/v1/integrations/{id}/sync-history
GET  /api/v1/sync-jobs
GET  /api/v1/sync-jobs/{id}
GET  /api/v1/webhook-events
GET  /api/v1/webhook-events/{id}
POST /api/v1/sync/{integration_id}/trigger
```

See `docs/api/api-conventions.md#integration-monitoring-phase-21` for
permissions and response shapes, and `/integrations` +
`/integrations/{id}` in the frontend for the monitoring UI (integration
list, health, sync history, and webhook event log per integration).

## Shiprocket operational routes (implemented, Phase 2.3)

Push actions — not sync/pull, so they live outside `/sync/*`:

```
POST /api/v1/orders/{id}/ship
POST /api/v1/shipments/{id}/shiprocket/assign-awb
POST /api/v1/shipments/{id}/shiprocket/cancel
POST /api/v1/shipments/{id}/shiprocket/request-pickup
POST /api/v1/shipments/{id}/shiprocket/refresh-tracking
POST /api/v1/ndr/{id}/reattempt
```

See `docs/integrations/shiprocket.md#data-flow` for what each calls and
`docs/api/api-conventions.md` for permissions.

## Reconciliation {#reconciliation}

Status: **IMPLEMENTED** (Phase 2.4). `ReconciliationService`
(`apps/api/app/services/reconciliation_service.py`) is the one place
reconciliation logic lives — it never lives inside an adapter, and it
never writes to a business table (Order/Customer/Product/Shipment/NDR/RTO
are never mutated by a reconciliation run; only `ReconciliationRun`/
`ReconciliationResult` rows are written). Architecture:

```
Provider (existing adapter's fetch()/get_tracking()/normalize())
   -> Compare against the matching OMS row
   -> ReconciliationResult (RECONCILED / MISMATCH / MISSING / ERROR)
   -> Persist (never auto-correct)
```

A run executes 11 checks (spec-numbered below), each independently
try/excepted so one failing check never aborts the run — the same "one
bad record doesn't fail the job" rule `SyncService` already follows:

1. Shopify order missing in OMS — samples a page of live Shopify orders,
   checks each against `OrderRepository.get_by_source_external_id`.
2. OMS order missing its `shopify_order_id` — pure DB, no provider call.
3. Shopify product differs from OMS (title/status/vendor).
4. Shopify customer differs from OMS (email/first_name/last_name).
5. OMS shipment missing its `shiprocket_shipment_id` — pure DB.
6. Shiprocket shipment missing in OMS — no bulk "list every Shiprocket
   shipment" endpoint was ever confirmed (see
   `docs/integrations/shiprocket.md`), so this is a best-effort
   self-consistency check via the audit trail: every
   `"shipment.created_via_shiprocket"` audit entry must resolve to a
   current `Shipment` row.
7. AWB mismatch, 8. courier mismatch, 9. tracking status mismatch — one
   combined pass per sampled shipment (one `get_tracking` call each,
   comparing the response against the OMS row).
10. NDR mismatch — compares a live NDR page against the OMS `NDR` row
    resolved by AWB.
11. RTO mismatch — RTO has no independent Shiprocket endpoint either, so
    it's derived from the same tracking response used for checks 7–9,
    exactly mirroring how `app.integrations.shiprocket.sync.refresh_tracking`
    derives it during a real sync.

Every provider-calling check is bounded to 25 sampled records per run
(`_SAMPLE_LIMIT`) — a working-sample audit, not a full-catalog diff, so a
run never loads an unbounded dataset into memory or hammers a
rate-limited provider (spec §27). A provider that isn't registered, or
is registered but not configured, makes its checks report as "skipped"
in `ReconciliationRun.run_metadata` — never fabricated, the same honesty
rule `health_check()` follows.

A run is triggered via `POST /api/v1/reconciliation/runs` (permission
`reconciliation.manage`), which only creates the `RUNNING`
`ReconciliationRun` row and hands it to Celery
(`app.tasks.reconciliation_tasks.run_reconciliation_task`) — never a
long-running provider-calling loop on the request thread. If the broker
is unreachable, the row still persists and the response says so
explicitly rather than claiming success (mirrors
`app.api.v1.endpoints.sync.trigger_sync`'s fallback). Results are read
via `GET /api/v1/reconciliation/runs`, `GET .../runs/{id}`, and
`GET /api/v1/reconciliation/results` (filterable by run/status/check_type/
provider/resolved; permission `reconciliation.read`); a human marks a
result reviewed via `POST /api/v1/reconciliation/results/{id}/resolve`
(permission `reconciliation.manage`) — reconciliation never resolves
itself. See `/reconciliation` in the frontend for the operator view.

See also: `docs/integrations/shopify.md`, `docs/integrations/shiprocket.md`,
`docs/integrations/couriers.md`.
