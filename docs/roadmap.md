# Roadmap

Status legend: **IMPLEMENTED** — working code exists and is tested. **PLANNED** — architecture/module boundary exists (folders, empty routers, docstrings) but no logic yet. **TODO** — not started, not yet scaffolded.

## Product modules (from the source specification)

1. Executive Dashboard — PLANNED (Phase 3 for KPI trends/charts; a live-count stat shell shipped in Phase 1)
2. Complete Order Management — **IMPLEMENTED** (Phase 1)
3. Shiprocket + Courier Tracking — **IMPLEMENTED** (Phase 2.3 — shipment creation, AWB assignment, tracking refresh, NDR/RTO; Phase 2.4 added a reconciliation engine that audits OMS records against both Shopify and Shiprocket; direct Blue Dart/Delhivery/Ecom Express adapters outside Shiprocket's umbrella remain TODO)
4. Delay Intelligence — PLANNED (Phase 3; `ShipmentDelayStatus` foundation column exists from Phase 1)
5. Courier Intelligence — TODO (Phase 4)
6. Smart Courier Recommendation — TODO (Phase 4)
7. NDR & RTO Management — PLANNED (full workflow automation — auto-escalation, scheduled reattempts — is Phase 3; the data model shipped in Phase 1, and Phase 2.3 added real Shiprocket-sourced NDR/RTO data plus a manual reattempt action)
8. Customer 360° — **IMPLEMENTED** (Phase 1)
9. WhatsApp / Instagram / Meta — TODO (Phase 5 / Phase 6)
10. Automation Engine — PLANNED (Phase 5)

## Phases

### Phase 0 — Foundation — **IMPLEMENTED**

Monorepo structure, Next.js app shell, FastAPI app with structured logging,
centralized error handling, health checks, CORS/security headers, rate
limiting, standard API response envelope, Alembic wiring (no models yet),
Celery app skeleton (no tasks yet), Docker Compose (Postgres + Redis + app
containers), code quality tooling (ruff/black/mypy, eslint/prettier/tsc),
test scaffolding (pytest + httpx, vitest + RTL), and this documentation set.

### Phase 1 — Core OMS — **IMPLEMENTED**

Authentication (JWT + persisted, revocable refresh tokens), RBAC (5
roles, 24 `module.action` permissions), Users, Customers (+ Customer
360 summary), Products (+ variants), Orders (+ OrderItem, append-only
OrderEvent timeline, controlled status-transition state machine),
Payments (created alongside orders, read-only API — no live gateway),
Shipments (+ append-only ShipmentEvent timeline, dedup by external
event ID), Couriers, NDR/RTO foundations (data model + status-update
API + list UI), Returns (+ auto-created Refund on completion), Audit
Logs. Database schema created via Alembic migration; every
externally-sourced model carries `SyncMetadataMixin` and an idempotent
`upsert_by_external_id()` repository method, ready for Phase 2 sync
adapters to call without further schema changes. Frontend: real
login/session flow (no more placeholder), dashboard stat shell,
full CRUD/search/filter/pagination UI for Orders, Customers, Products,
Shipments, NDR, RTO, and Audit Logs, all with loading/error/empty
states. 38 backend tests + 18 frontend tests.

### Phase 2.1 — Integration & Synchronization Foundation — **IMPLEMENTED**

Generic infrastructure every provider adapter will synchronize through:
`Integration`/`SyncJob`/`SyncError`/`WebhookEvent` models (seeded for all
8 known providers as an honest "Not Connected" — no fake status),
`IntegrationAdapter`/`Normalizer` interfaces (no provider logic in the
base classes), a runtime adapter registry (empty until Phase 2 registers
one), a credential-resolution seam (`EnvCredentialProvider`, no secret
ever stored in the DB), a retryable/non-retryable error classifier with
exponential backoff, `SyncService` (start/track/complete a sync job,
update `Integration` health), `WebhookService` (idempotent ingest, with
a deterministic fallback event id for providers with no stable one),
three Celery task modules (sync execution, webhook processing, retry
requeue — all result-ignoring, so a broker outage degrades gracefully
instead of 500ing a request), 10 monitoring API endpoints (RBAC-gated:
`integrations.read/manage/test`, `sync_jobs.read/manage`,
`webhooks.read`), and the `/integrations` + `/integrations/{id}`
frontend (list, health, sync history, webhook event log). Alembic
migration `4d60488e0bdb`. 27 new backend tests (idempotency, retry
limits, RBAC, health service, migration chain, Celery registration) + 4
new frontend tests. No live Shopify/Shiprocket/courier API call is made
anywhere in this phase — every adapter lookup resolves to "none
registered" and fails gracefully.

### Phase 2.2 — Shopify Integration — **IMPLEMENTED**

The first real provider registered into the Phase 2.1 adapter registry.
`ShopifyAdapter` (GraphQL Admin API — REST is legacy for new
integrations as of April 2025; cursor pagination, cost-based rate
limiting, exponential backoff on retryable failures) with a real
`ShopifyClient`, `ShopifyCustomerNormalizer`/`ShopifyProductNormalizer`/
`ShopifyOrderNormalizer`, and a real `POST /api/v1/webhooks/shopify`
endpoint with mandatory HMAC-SHA256 verification over the raw body.
`SyncService.execute_sync`'s Phase 2.1 stub is filled in: it now
actually pages through Shopify data, normalizes, and upserts via
`CustomerService`/`ProductService`/`OrderService` (new
`upsert_synced_*` methods, reusing the existing
`BaseRepository.upsert_by_external_id()` — no parallel Shopify-specific
tables). A small additive migration
(`54ebf7a087e2_phase_2_2_shopify_sync_fields`) adds `OrderItem` sync
tracking (idempotent per-line-item upsert on resync), `Order`
shipping/billing address snapshots, and `Product`/`ProductVariant`
type/tags/barcode/options — all nullable, no existing data affected.
Field-ownership rules for conflict handling (Shopify owns financial/
status fields; the OMS-internal fulfillment workflow status is set once
and never rewound by a resync, except a Shopify-reported cancellation)
are documented in `docs/integrations/shopify.md#field-ownership--conflict-handling-spec-27`.
`/integrations/{id}` gained Test Connection / Sync Customers / Sync
Products / Sync Orders / Full Sync actions. 67 new backend tests
(authentication, health check, API client, pagination, rate-limit/retry,
normalization for all three entities + payment mapping, idempotent
upsert + duplicate prevention for all three entities, partial sync
failure, `SyncJob` lifecycle, incremental sync, webhook HMAC validation,
duplicate webhook handling, webhook processing, RBAC, credential
protection) — all against mocked Shopify responses; no real Shopify
account was available, and none was invented.

### Phase 2.3 — Shiprocket Integration — **IMPLEMENTED**

The logistics counterpart to Phase 2.2's commerce integration —
completes the Shopify (commerce) → OMS → Shiprocket (logistics) chain
the source spec calls for. `ShiprocketAdapter` (REST, email/password
login with cached/auto-refreshed bearer token) with a real
`ShiprocketClient`, `ShiprocketTrackingNormalizer`/`ShiprocketNDRNormalizer`/
`ShiprocketOrderPushNormalizer`, and `ShiprocketOperationsService` — the
full push workflow (`POST /orders/{id}/ship` → create Shiprocket order +
shipment → assign AWB → request pickup → refresh tracking), all via new
`upsert_synced_*` methods on the existing `ShipmentService`/`CourierService`/
`NDRService`/`RTOService` (no parallel Shiprocket-specific tables).
Because Shiprocket's API shape differs from Shopify's (no single
"list everything, paginate by cursor" feed for tracking), two sync
strategies coexist on the same `SyncJob` primitives: NDR (a genuine
provider-paginated list) reuses Phase 2.2's generic loop unchanged;
tracking is OMS-shipment-driven (queries the OMS's own AWBs) via a
dedicated Celery task; RTO has no independent Shiprocket endpoint and is
instead derived as a side effect of tracking refresh. No webhook/callback
contract was implemented — none could be confirmed without a live
account, and the instruction was explicit not to invent one; polling
(the tracking-refresh task) is the sync strategy instead. A small
additive migration adds `SyncMetadataMixin` to `Courier` (idempotent
upsert by Shiprocket's `courier_company_id`). `/orders/{id}` gained a
"Ship via Shiprocket" action; `/shipments/{id}` gained Assign AWB /
Request Pickup / Refresh Tracking / Cancel actions; `/ndr` gained a
Reattempt action; `/integrations/{id}` gained Shiprocket-specific Sync
Tracking / Sync NDR actions. 64 new backend tests (authentication, token
caching/re-login, health check, NDR pagination, status/payment-method
mapping, order-payload mapping, shipment creation, AWB assignment +
courier upsert, duplicate prevention across shipment/AWB/courier/NDR/
tracking-event, partial sync failure, `SyncJob` lifecycle, RTO
derivation, cancellation, pickup, on-demand tracking refresh, NDR
reattempt with "state unchanged on Shiprocket failure", RBAC, credential
protection, audit logging, and the full order → shipment → AWB →
tracking flow) — all against mocked Shiprocket responses; no real
Shiprocket account was available, and none was invented.

### Phase 2.4 — E2E Integration & Reconciliation — **IMPLEMENTED**

Not a new provider — this phase connects and validates the existing
Shopify + OMS + Shiprocket + Sync Jobs + NDR/RTO + Audit Logs chain
end-to-end, and adds the one genuinely new component: a
**reconciliation engine**. `ReconciliationService`
(`app/services/reconciliation_service.py`) runs 11 checks (Shopify
order/product/customer diffs, OMS-side structural gaps, AWB/courier/
tracking-status/RTO mismatches, NDR mismatches) comparing OMS records
against live Shopify/Shiprocket data via the *existing* adapters —
reconciliation logic lives in its own service, never inside an adapter,
and it never writes to a business table, only to new `ReconciliationRun`/
`ReconciliationResult` rows (spec: report mismatches, never
auto-correct). Each provider-calling check is bounded to 25 sampled
records per run and degrades to "skipped" (never fabricated) when a
provider isn't registered or configured, matching the same honesty rule
`health_check()` already follows. Triggered via
`POST /api/v1/reconciliation/runs` → `ReconciliationRun` → Celery →
`ReconciliationService.run_checks` (never inline on the request thread);
results are reviewed and marked resolved via the new `/reconciliation`
frontend page (`reconciliation.read`/`reconciliation.manage` — new
permissions, since this is a distinct capability from sync/integration
monitoring). A small additive migration adds the two reconciliation
tables. New E2E lifecycle tests chain Shopify sync → Shiprocket shipment
creation → AWB assignment → tracking refresh → delivered through the
real services in one continuous test, with explicit assertions that
Shopify-owned order fields and Shiprocket-owned shipment fields survive
every step untouched by the other provider (data-ownership boundary,
spec §10) — the per-provider unit tests already existed from Phase
2.2/2.3; this phase's tests specifically prove the *chain* holds
together. 14 new reconciliation tests + 2 new E2E lifecycle tests (213
backend tests total, zero regressions). While building the E2E tests, a
real test-isolation bug was found and fixed: `app.workers.celery_app`
registers real (but unconfigured) adapter instances at Python import
time (intentional for the actual Celery worker process), and a
test file that destructively cleared the adapter registry without
restoring it could silently affect unrelated tests running later in the
same process — fixed by adding `snapshot_adapters`/`restore_adapters` to
`app.integrations.registry` and using them instead of a one-way clear.

### Phase 2.5+ — Remaining Provider Integrations — TODO

Direct Blue Dart/Delhivery/Ecom Express courier adapters (outside
Shiprocket's umbrella, for merchants who ship through them directly).
Registers concrete `IntegrationAdapter`s into the same Phase 2.1 registry
Shopify/Shiprocket use — no schema changes expected beyond what each
provider's actual field set requires.

### Phase 3 — Operations — TODO

Dashboard KPIs/trend charts (beyond Phase 1's live counts), delay
detection service (populates `Shipment.delay_status` beyond its Phase 1
`unknown` default), full NDR/RTO workflow automation (reattempt
scheduling, escalation), alerts, tasks, basic analytics.

### Phase 4 — Intelligence Foundation — TODO

Courier performance metrics, city/PIN-code analytics, courier scoring,
weighted courier recommendation (configurable weights, no ML).

### Phase 5 — Automation — TODO

Automation rule engine (WHEN/IF/THEN), notifications, WhatsApp integration,
operational workflow actions.

### Phase 6 — AI / ML — TODO

Delivery prediction, RTO prediction, delay prediction, AI operations
assistant, root-cause analysis, automated courier selection. These services
read normalized OMS data only and never touch the database directly (see
`docs/architecture/overview.md`).

## Explicitly not implemented in Phase 1 (by design)

- No live Shopify/Shiprocket/courier API calls — adapters remain
  directory stubs until Phase 2. Customers/Products/Orders/Shipments/
  NDR/RTO are all creatable/updatable manually via the API in the
  meantime (`source_system="manual"`), for development, admin, and
  testing.
- No payment gateway integration — `Payment`/`Refund` are read-only via
  the API; created internally by `OrderService`/`ReturnService`.
- No delay-detection logic — `Shipment.delay_status` exists as a
  column with an `unknown` default; the service that actually computes
  it is Phase 3.
- No fake/mock data anywhere — every list page that isn't live yet
  still shows an honest "planned for Phase X" empty state
  (`PhasePlaceholder`) rather than sample rows.

## Explicitly not implemented in Phase 2.2 (by design)

- No OAuth app-installation flow — this integration targets one
  operator-controlled store via a custom-app access token, not a public
  app installed by many merchants.
- No write-back to Shopify — every scope requested is read-only; the
  OMS never creates/updates/cancels anything in Shopify.
- No image sync — `Product` has no image column; Shopify image
  references are preserved in `raw_external_payload` only. Adding a
  first-class image field (or gallery table) is deferred.
- No stale line-item deletion on resync — see
  `docs/integrations/shopify.md#idempotency`.
- No scheduled/automatic sync — `app.tasks.sync_tasks.run_scheduled_sync_task`
  stays a documented no-op (Phase 2.1 decision, unchanged); every sync in
  Phase 2.2 is manually triggered from `/integrations/{id}`.

## Explicitly not implemented in Phase 2.3 (by design)

- No Shiprocket webhook/callback endpoint — no reliable contract could
  be confirmed without a live account, and inventing one was explicitly
  disallowed. `app/api/v1/webhooks/shiprocket.py` stays an empty router;
  polling (the tracking-refresh task) is the sync strategy instead.
- No scheduled/automatic tracking or NDR sync — every sync is manually
  triggered from `/integrations/{id}` (same Phase 2.1 decision as Shopify).
- No independent RTO sync endpoint — RTO records are derived from
  tracking events only (see `docs/integrations/shiprocket.md`).
- No package-dimension data model — `POST /orders/{id}/ship` accepts
  optional dimension overrides, defaulting to a placeholder small-parcel
  size; the OMS has no per-order/per-product package-dimension field yet.
- No Blue Dart/Delhivery/Ecom Express adapters outside Shiprocket's
  umbrella — deferred to a later phase.
- No real Shiprocket account was available to verify against — every
  field name in `normalizer.py`/`adapter.py` should be re-checked
  against a live account's actual responses before production use.

## Explicitly not implemented in Phase 2.4 (by design)

- No automatic corrective action — reconciliation only ever reports a
  mismatch (`ReconciliationResult`); nothing is auto-corrected, and a
  human explicitly marks a result reviewed via the resolve action. This
  is an explicit spec requirement, not a gap.
- No scheduled/periodic reconciliation — every run is manually triggered
  via `POST /reconciliation/runs`; a Celery Beat schedule is a natural
  later addition but wasn't asked for.
- No full-catalog reconciliation — every provider-calling check samples
  at most 25 records per run (recent/relevant, not the entire dataset),
  to avoid loading an unbounded dataset into memory or hammering a
  rate-limited provider. Re-running catches more of the catalog over
  time; there's no single-run "reconcile everything" mode.
- No independent Shiprocket "list every shipment"/RTO endpoint exists
  (same finding as Phase 2.3), so checks 6 (Shiprocket shipment missing
  in OMS) and 11 (RTO mismatch) are necessarily best-effort — derived
  from the audit trail and from tracking responses respectively, not
  from a dedicated provider feed.
- No real Shopify/Shiprocket account was available to verify
  reconciliation against — every check is tested against mocked adapter
  responses; report explicitly states "REAL CONNECTION: NOT VERIFIED".

## Explicitly not implemented in Phase 2.1 (by design)

- No provider adapter is registered — `app.integrations.registry` is
  empty, so every health check/sync always reports "no adapter
  registered" and every webhook event is marked `ignored`. No network
  call to Shopify, Shiprocket, Blue Dart, or any other provider happens
  anywhere in this phase's code.
- The provider webhook routes (`POST /webhooks/{shopify,shiprocket,couriers}/*`)
  remain empty routers — signature verification needs a real adapter to
  verify against. The generic idempotency layer they'll call
  (`WebhookService.ingest`) already exists and is tested directly.
- No OAuth/credential-entry UI — `Integration.configuration` never holds
  a secret, and there's no form to add one yet; credentials are
  environment-variable-only (`EnvCredentialProvider`) until a real
  secret manager is wired in.
- No Celery worker/beat process is started by this phase — the task
  modules are registered and unit-tested directly (calling the
  underlying async function, not through a live broker); running
  `celery -A app.workers.celery_app worker` is an operational step for
  whichever environment actually needs background execution.
