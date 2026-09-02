# Shiprocket Integration

Status: **IMPLEMENTED** (Phase 2.3). Real REST API client, adapter,
normalizers, and the OMS Order → Shiprocket shipment → AWB → tracking →
NDR/RTO workflow — built on the same Phase 2.1 infrastructure Shopify
uses (`Integration`/`SyncJob`/`SyncError`, `SyncService`, retry policy).
No live provider was called during implementation — no real Shiprocket
account/credentials were available (see "Verification" below); every
path was exercised against mocked REST responses.

## Authentication

Shiprocket's External API uses email/password login, not OAuth:
`POST {SHIPROCKET_API_URL}/auth/login` with `{"email", "password"}`
returns a bearer token documented as valid for 240 hours (10 days).
`ShiprocketClient` caches the token in memory, refreshes it an hour
before the documented expiry, and forces one re-login + retry on any
401 (a token can go stale before the tracked expiry, e.g. if revoked).
No OAuth flow, no app-installation flow — this integration targets one
operator-controlled Shiprocket account.

## Why pull-sync and push-actions are architecturally different from Shopify

Shopify's GraphQL API gives the OMS a "list every record, paginate by
cursor" feed for every entity — a clean fit for Phase 2.1's generic
`SyncService.execute_sync` loop. Shiprocket doesn't work that way for
everything:

- **NDR** — Shiprocket exposes a genuine paginated NDR list
  (`GET /ndr/all`), so it runs through the *same* generic
  `SyncService.execute_sync` loop Shopify's customers/products/orders
  use, completely unchanged.
- **Tracking** — no "list every shipment that changed since X" feed was
  found in available documentation. The OMS already knows which AWBs it
  holds, so refreshing tracking is **OMS-shipment-driven**: it queries
  the OMS's own `Shipment` rows (has an AWB, not in a terminal state)
  and calls `GET /courier/track/awb/{awb}` per shipment. This runs
  through a dedicated orchestration
  (`app.integrations.shiprocket.sync.refresh_tracking`) that composes
  `SyncService`'s lifecycle primitives (`record_progress`/`record_error`)
  directly rather than the generic provider-paginated loop — the same
  pattern `app.tasks.retry_processing` already used before this phase.
- **RTO** — no dedicated RTO-list endpoint could be confirmed either.
  RTO records are instead *derived* from tracking events: when a
  tracking event's mapped status is `RTO_INITIATED`/`RTO_DELIVERED`,
  `refresh_tracking` creates/updates the matching `RTO` row as a side
  effect. This avoids inventing an unverified endpoint while still
  satisfying the requirement (spec §18).
- **Shipment creation, AWB assignment, cancellation, pickup, NDR
  reattempt** — none of these are sync/pull operations; they're
  operator-triggered pushes (spec §26), implemented in
  `ShiprocketOperationsService` and exposed as real endpoints (see
  below). OMS state is only updated *after* Shiprocket confirms success.

## Components (`apps/api/app/integrations/shiprocket/`)

| File | Responsibility |
|---|---|
| `config.py` | `ShiprocketConfig.from_settings()` — reads `SHIPROCKET_EMAIL`/`SHIPROCKET_PASSWORD`/`SHIPROCKET_API_URL`/`SHIPROCKET_PICKUP_LOCATION`; `None` (not an error) when unconfigured. |
| `client.py` | `ShiprocketClient` — the only thing that calls Shiprocket. Login/token caching, one `request(method, path, json=, params=)` method; classifies every failure via `errors.py`, retries transient ones with `app.integrations.retry`'s exponential backoff. |
| `errors.py` | Maps `httpx` exceptions to the `error_type` vocabulary `app.integrations.retry` already classifies as retryable/non-retryable. |
| `normalizer.py` | `ShiprocketTrackingNormalizer`, `ShiprocketNDRNormalizer` (pull), `ShiprocketOrderPushNormalizer` (push — OMS `Order` → Shiprocket's adhoc-order-create payload), plus the documented status/payment-method mapping tables. |
| `adapter.py` | `ShiprocketAdapter(IntegrationAdapter)` — interface methods (`authenticate`, `health_check`, `fetch`/`fetch_incremental` for `"ndr"` only, `normalize`, `process_webhook` — a documented no-op) plus concrete push/pull capabilities beyond the interface: `create_order`, `assign_awb`, `cancel_shipment`, `request_pickup`, `get_tracking`, `ndr_reattempt`. |
| `sync.py` | `refresh_tracking()` — the OMS-shipment-driven tracking orchestration described above. |
| `__init__.py` | `register()` — adds a `ShiprocketAdapter` to `app.integrations.registry` regardless of configuration, mirroring `app.integrations.shopify`. |

`ShiprocketOperationsService` (`apps/api/app/services/shiprocket_service.py`)
owns the push workflow and is explicitly Shiprocket-aware (the same way
`app/api/v1/webhooks/shopify.py` is explicitly Shopify-aware), but every
OMS write goes through existing services/repositories
(`ShipmentService.upsert_synced_shipment`, `CourierService.upsert_synced_courier`,
`NDRService.upsert_synced_ndr`, `RTOService.upsert_synced_rto`) — never a
raw session mutation.

## Data flow

**Push — OMS Order → Shiprocket shipment:**

```
POST /api/v1/orders/{id}/ship
  -> ShiprocketOperationsService.create_shipment_for_order
     -> ShiprocketOrderPushNormalizer.build_payload(order, pickup_location=...)
     -> ShiprocketAdapter.create_order -> POST /orders/create/adhoc
     -> ShipmentService.upsert_synced_shipment(source_system="shiprocket", external_id=shipment_id)
     -> AuditLog "shipment.created_via_shiprocket"

POST /api/v1/shipments/{id}/shiprocket/assign-awb
  -> ShiprocketAdapter.assign_awb -> POST /courier/assign/awb
  -> CourierService.upsert_synced_courier (idempotent by courier_company_id)
  -> Shipment.awb / Shipment.courier_id updated
  -> AuditLog "shipment.awb_assigned"

POST /api/v1/shipments/{id}/shiprocket/{cancel,request-pickup,refresh-tracking}
  -> similarly: adapter call first, OMS state updated only on success, AuditLog written
```

**Pull — NDR (generic loop, identical to Shopify's):**

```
FastAPI creates a SyncJob (QUEUED)
  -> Celery (app.tasks.sync_tasks.execute_sync_task)
    -> SyncService.execute_sync
      -> ShiprocketAdapter.fetch("ndr", cursor=...) -> GET /ndr/all
      -> ShiprocketAdapter.normalize
      -> app.integrations.entity_sync.ENTITY_UPSERT_HANDLERS["ndr"]
         (NDRService.upsert_synced_ndr — resolves the owning Shipment by AWB;
          spec §16: never invents an NDR for an AWB the OMS doesn't know)
```

**Pull — Tracking (dedicated task, OMS-shipment-driven):**

```
POST /api/v1/sync/{integration_id}/trigger  (entity_type="tracking")
  -> SyncService.start_sync (creates the SyncJob)
  -> Celery app.tasks.shiprocket_sync.refresh_tracking_task
    -> app.integrations.shiprocket.sync.refresh_tracking
       -> for each OMS Shipment with an AWB, not in a terminal state:
          ShiprocketAdapter.get_tracking(awb) -> GET /courier/track/awb/{awb}
          -> ShipmentService.add_tracking_event (idempotent — reused unchanged
             from Phase 1: dedups by external_event_id, else
             (status, event_timestamp))
          -> if the mapped status is RTO_INITIATED/RTO_DELIVERED:
             RTOService.upsert_synced_rto (derived, not a separate sync)
```

## Status mapping

Raw Shiprocket tracking status strings vary and are not fully documented
without a live account — `app.integrations.shiprocket.normalizer._SHIPMENT_STATUS_MAP`
covers the commonly-cited values (`NEW`, `PICKED UP`, `IN TRANSIT`,
`OUT FOR DELIVERY`, `DELIVERED`, `UNDELIVERED`, `CANCELLED`,
`RTO INITIATED`, `RTO DELIVERED`, ...), matched case/whitespace-insensitively.
**An unmapped status never crashes the sync and is never guessed** — the
raw text is still recorded on `ShipmentEvent.status` (a free-text
column), but `Shipment.current_status` is only updated when the status
maps to a known `ShipmentStatus` value (spec §14).

## Payment method mapping

OMS `PaymentType` → Shiprocket's `payment_method` field: `COD` → `"COD"`,
`PREPAID`/`OTHER` → `"Prepaid"` — the two values commonly documented as
accepted by the adhoc-order-create endpoint. Re-verify against a live
account before first real use.

## Data ownership (spec §36)

- **Shopify** owns commerce/order-source data: customer/product
  identity, order financials, `payment_status`/`fulfillment_status`.
- **Shiprocket** owns logistics data: `Shipment.awb`/`courier_id`/
  `current_status`/`delay_status`, `ShipmentEvent`, `NDR`, `RTO`.
- **OMS** owns internal operational workflow (`Order.status`'s
  pack/ship state machine), audit logs, and cross-provider
  orchestration (linking a Shopify-sourced `Order` to a
  Shiprocket-sourced `Shipment` via `Shipment.order_id`).

Neither provider overwrites the other's fields — Shiprocket sync/push
paths never touch `Order.payment_status`/customer/product fields, and
nothing in the Shopify sync path touches `Shipment`/`NDR`/`RTO`.

## Idempotency

- Shipment creation/AWB assignment: `Shipment`/`Courier` upserted by
  `(source_system="shiprocket", external_id=...)` — the generic Phase 1
  mechanism, reused unchanged. Calling "Ship via Shiprocket" or "Assign
  AWB" twice for the same order/shipment never creates a duplicate row.
- Tracking events: `ShipmentEvent`'s existing dedup (unchanged from
  Phase 1) — by `external_event_id` when Shiprocket provides one, else
  `(status, event_timestamp)`.
- NDR: `NDR` upserted by `(source_system="shiprocket", external_id=...)`.

## Retry & rate limiting

HTTP `429`/`5xx`/timeout/network errors are classified retryable and
retried via `app.integrations.retry`'s exponential backoff
(`RetryPolicy(max_retries=5, base_delay_seconds=60, ...)` by default).
`401`/`403`/`422` are non-retryable — a 401 instead triggers exactly one
forced re-login + retry (not the generic backoff loop), since the fix
for "token expired" is a fresh login, not a delay.

## Webhooks — tracking status updates (spec §19/§20)

Status: **IMPLEMENTED**, payload shape **UNVERIFIED against a live
delivery**.

    POST /api/v1/webhooks/shiprocket/tracking
    POST /api/v1/webhooks/shipment-updates/tracking   (alias -- use this one in Shiprocket's dashboard)

Both paths reach the exact same handler (`app.api.v1.webhooks.shiprocket`'s
router is mounted twice in `router.py`, under two different prefixes) —
**use the second one** when configuring Shiprocket. Confirmed live:
Shiprocket's own "Webhooks" dashboard page (Settings > API > Webhook)
rejects a URL containing the word "shiprocket" with *"Please refrain from
using keywords like shiprocket, kartrocket, sr, or kr in the webhook
url"* — which the first path violates. The first path is left registered
(nothing depends on removing it) purely so nothing that already links to
it breaks.

Shiprocket's tracking-webhook payload schema and secret-transport
mechanism are not published in any documentation this integration's
research could confirm (see the "Webhook research" note below) — unlike
the Shopify webhook, which verifies a documented, confirmed
`X-Shopify-Hmac-Sha256` HMAC. Rather than inventing a payload shape,
this endpoint is built to be **tolerant of every commonly-cited
variant**, and every field it reads is marked in code as unverified
until confirmed against a real delivery:

- **Parsing** (`app.integrations.shiprocket.normalizer`):
  `extract_webhook_shipment_identifiers` reads `awb`/`awb_code`,
  `shipment_id`/`sr_shipment_id`, `order_id`/`sr_order_id`,
  `channel_order_id`/`channel_order_number`/`reference_number`, and
  `courier_name`/`courier` — trying each alias in order, never guessing
  a value that isn't present. `ShiprocketWebhookTrackingNormalizer`
  reads the status/timestamp/location fields the same way. If the body
  instead carries a nested `shipment_track_activities`/`scans` list (the
  same shape `GET .../track/awb/{awb}` returns), the existing
  `TRACKING_NORMALIZER`/`extract_tracking_events` handle it unchanged —
  full reuse, not a second parser.
- **Security** (`app.integrations.shiprocket.webhooks`):
  `SHIPROCKET_WEBHOOK_SECRET` (already present in `app.core.config`,
  previously unused) is checked against **either** an `X-Api-Key`
  request header **or** a `token`/`secret`/`webhook_secret`/
  `webhook_token` field inside the JSON body — the two transports most
  consistently described for the single "Webhook Secret" field
  Shiprocket's dashboard (Settings > API > Webhook) exposes. An
  unconfigured secret always rejects (never "skip verification"),
  exactly like the Shopify webhook's HMAC check.
- **Matching** (`app.services.shiprocket_webhook_service.
  ShiprocketWebhookService`): AWB -> Shiprocket shipment id (by
  `(source_system="shiprocket", external_id=...)`, the same identity
  every Shiprocket `Shipment` row is already keyed by) -> Shiprocket
  order id (via the same live `GET /orders/show/{id}` fallback
  `app.integrations.entity_sync._upsert_shipment` already uses for the
  pull-sync path — reused, not reimplemented) -> channel/Shopify order
  number. Never falls back to name/phone/address. An order that resolves
  with zero existing `Shipment` rows gets one created via the same
  `upsert_synced_shipment` idempotent create-or-update the pull-sync path
  uses; an order with *more than one* existing shipment is treated as
  unmatched rather than guessed.
- **Applying the update**: `app.integrations.shiprocket.sync.
  apply_tracking_event` — extracted from `refresh_tracking` specifically
  so the poll and webhook paths can never silently diverge — appends a
  `ShipmentEvent`, advances `Shipment.current_status` via the existing
  `_SHIPMENT_STATUS_MAP`, and derives an `RTO` row exactly like a poll
  refresh would.
- **Idempotency**: routed through the same generic
  `WebhookService.ingest()` Shopify's webhook uses. No stable Shiprocket
  webhook event id could be confirmed, so `external_event_id` is always
  `None`, which makes `WebhookService` fall back to
  `compute_fallback_event_id` — a deterministic hash of
  (integration, event_type, payload). A byte-identical retry collides on
  the same hash and is a no-op; a genuinely different status update for
  the same shipment hashes differently and is processed. Within one
  webhook delivery, per-event application also falls back to
  `ShipmentEvent`'s existing `(status, event_timestamp)` dedup when no
  event id is present (see `app.models.shipment`'s module docstring) —
  the same mechanism the poll path already relies on.
- **Unmatched events**: never fabricated. The `WebhookEvent` row is
  marked `IGNORED` with a sanitized `match_strategy` reason (e.g.
  `no_matching_shipment:unmatched`,
  `no_matching_shipment:ambiguous_multiple_shipments_for_order`) for
  reconciliation via the raw `payload` already stored on that row
  (`GET /api/v1/webhook-events/{id}` for anyone with DB access; the API
  itself never returns the raw payload — spec §18). The endpoint still
  acks 200 so Shiprocket doesn't retry an event it already understood.
- **Errors**: malformed/non-object JSON -> 400. Invalid/missing token ->
  401 (checked before the body is even parsed for a body-embedded token,
  so an unauthenticated caller never learns whether their JSON was
  well-formed). A genuine processing failure (database error) rolls back,
  marks the `WebhookEvent` `FAILED`, and returns 502/500 so Shiprocket's
  own retry behaviour can recover it — **never fabricated as a 200**.

### Webhook research

Extensive documentation research (Shiprocket's own `apidocs.shiprocket.in`
and `support.shiprocket.in`, third-party integration guides, public
Postman workspaces) found that Shiprocket's dashboard exposes a
"Shipment Webhook Settings" configuration (Settings > API > Webhook —
URL + Secret) but **no source could be found publishing the exact JSON
payload schema or confirming the secret's transport mechanism**. Nothing
above was fabricated to fill that gap — every field name is a
commonly-cited convention, explicitly marked UNVERIFIED in the relevant
module's docstring, and the parser degrades to "field not found" (never
a crash or an invented value) for any alias that turns out wrong.
**Before this is trusted in production, a real Shiprocket webhook
delivery must be captured and compared against the field lists above**
— see "Post-deployment verification steps" in the PR/handoff notes for
exactly how to do that safely (log the raw payload once, temporarily,
never store it beyond that).

### Why the scheduled sync stays

This webhook is an **additional real-time ingestion path**, not a
replacement. `refresh_tracking`/the scheduled Celery beat sync
(`app.tasks.shiprocket_sync`) still run unchanged — the cursor-based
crawl, the 8-minute per-run time budget, the stale-job reaper, and the
confirmed-unmatched `SyncError` cache are all untouched by this work.
If Shiprocket's webhook payload turns out to use field names none of the
aliases above cover, or a delivery is ever dropped in transit, the next
scheduled poll still catches it up — exactly the fallback/reconciliation
role the business requirement asked this to keep.

## Credentials {#credentials}

Set in `.env` (never committed, never returned by any API response):

```
SHIPROCKET_EMAIL=ops@example.com
SHIPROCKET_PASSWORD=...
SHIPROCKET_API_URL=https://apiv2.shiprocket.in/v1/external   # default
SHIPROCKET_PICKUP_LOCATION=Main Warehouse   # required only to create shipments
```

Until `SHIPROCKET_EMAIL`/`SHIPROCKET_PASSWORD` are set, every health
check reports `"Not Configured"` rather than failing the API or
attempting any network call.

## Known limitations

- **No package-dimension data in the OMS.** `POST /orders/{id}/ship`
  accepts optional `length_cm`/`breadth_cm`/`height_cm`/`weight_kg`
  overrides, defaulting to a small-parcel placeholder (10×10×10cm,
  0.5kg) — the OMS has no per-order or per-product package-dimension
  field yet. A future phase could derive these from `ProductVariant.weight`.
- **Stale line-item removal is not handled** (shared with the Shopify
  order-sync limitation) — not specific to Shiprocket, noted here since
  it affects any order this integration ships.
- **RTO has no independent sync path** — it's derived from tracking
  events only; a store with RTOs that never pass through a tracked AWB
  refresh (unlikely, but theoretically possible) wouldn't get an RTO
  record until the next tracking refresh surfaces it.

## Verification

Built and tested entirely against **mocked** Shiprocket REST responses
(`httpx.MockTransport`-free — a lightweight stub client, since
Shiprocket's REST shape didn't need the `MockTransport` machinery
Shopify's GraphQL client used) — no real Shiprocket account or
credentials were available in the environment this was built in, and
none were invented. 64 tests across
`apps/api/tests/test_shiprocket_{client,adapter,normalizer,sync,operations}.py`
cover authentication, token caching/re-login, health check, NDR
pagination, status/payment-method mapping, order-payload mapping,
shipment creation, AWB assignment + courier upsert, duplicate
prevention (shipment/AWB/courier/NDR/tracking-event), partial sync
failure, `SyncJob` lifecycle, RTO derivation, cancellation, pickup,
on-demand tracking refresh, NDR reattempt (including "state unchanged
when Shiprocket rejects the request"), RBAC, credential protection,
audit logging, and the full order → shipment → AWB → tracking flow.
Before pointing this at a real account for the first time: run `Test
Connection` from `/integrations/{id}`, and re-verify the REST field
names in `normalizer.py`/`adapter.py` against that account's actual
responses.
