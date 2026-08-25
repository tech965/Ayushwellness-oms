# Database Schema

Status: **IMPLEMENTED** (Phase 1 core OMS; Phase 2.1 integration/sync
foundation; Phase 2.2 Shopify field additions; Phase 2.3 Shiprocket
courier sync fields; Phase 2.4 reconciliation tables). Tables are created
via Alembic migrations
`apps/api/alembic/versions/abbf8b3ee1d0_phase_1_core_oms_schema.py`,
`4d60488e0bdb_phase_2_1_integration_and_sync_.py`,
`54ebf7a087e2_phase_2_2_shopify_sync_fields.py`,
`1b440c092593_phase_2_3_shiprocket_courier_sync_fields.py`, and
`50337406e09a_phase_2_4_reconciliation.py`.
PostgreSQL, SQLAlchemy 2.x, UUID primary keys
(`app/db/base.py:UUIDPrimaryKeyMixin`, backed by a cross-dialect `GUID`
type so the same models also run against SQLite in tests),
`created_at`/`updated_at` on every table with `updated_at`
(`TimestampMixin`, `eager_defaults=True` so async flush doesn't defer
server-generated timestamps).

## Sync metadata (`app/models/mixins.py:SyncMetadataMixin`)

Every model below marked **(synced)** mirrors an external entity and
includes: `source_system`, `external_id`, `external_created_at`,
`external_updated_at`, `last_synced_at`, `sync_version`,
`raw_external_payload` (JSONB), plus a `UniqueConstraint(source_system,
external_id)`. `source_system` is a plain string (`"shopify"`,
`"shiprocket"`, `"manual"`, ...), not a DB enum — new platforms are
added without a migration. `BaseRepository.upsert_by_external_id()`
(`app/repositories/base.py`) is the idempotent create-or-update every
Phase 2 sync adapter will call against these columns; it's dialect-portable
(select-then-insert-or-update, not Postgres `ON CONFLICT`) so the same
path is exercised by the SQLite test suite.

## Auth

- **User** — name, email (unique), phone, password_hash (Argon2),
  is_active, is_superuser
- **RefreshToken** — jti (unique), user_id, expires_at, revoked_at —
  enables server-side revocation on logout
- **Role** — name (ADMIN, OPERATIONS, CUSTOMER_SUPPORT, MARKETING,
  MANAGEMENT), description
- **Permission** — code (`module.action`, e.g. `orders.read`,
  `orders.cancel`), module, action, description
- **UserRole** / **RolePermission** — join tables (many-to-many both ways)

## Customers **(synced)**

- **Customer** — shopify_customer_id (unique), first_name, last_name,
  full_name, email, phone, alternate_phone, is_active, notes
- **CustomerAddress** — customer_id, address_type (enum: shipping /
  billing / other), line1/line2, city, state, country, pin_code,
  landmark, contact_name, contact_phone, is_default

## Products **(synced)**

- **Product** — shopify_product_id (unique), title, description, status
  (enum: active / draft / archived), vendor, product_type, tags
  (comma-joined; Phase 2.2)
- **ProductVariant** — product_id, shopify_variant_id (unique), sku
  (unique), title, price, compare_at_price, inventory_quantity, weight,
  barcode, options (JSONB — e.g. `{"Size": "60ct"}`; Phase 2.2), status

## Orders **(synced)**

- **Order** — order_number (unique), shopify_order_id (unique,
  nullable), customer_id, order_datetime, currency, subtotal,
  discount_amount, tax_amount, shipping_charge, total_amount (all
  `NUMERIC(12,2)`, never float), payment_type (enum: cod / prepaid /
  other), payment_status (enum), status (enum: pending → confirmed →
  processing → packed → shipped → delivered, or cancelled — see
  `app/services/order_service.py:ORDER_STATUS_TRANSITIONS`),
  fulfillment_status, cancellation_status, notes, shipping_address /
  billing_address (JSONB point-in-time snapshots, not a `CustomerAddress`
  FK — an order's address must never change retroactively if the
  customer later edits their saved address; Phase 2.2) — holds **current
  state only**
- **OrderItem (synced, Phase 2.2)** — order_id, product_variant_id,
  sku/product_name snapshot (never changes if the product is later
  renamed/repriced), quantity, unit_price, discount_amount, tax_amount,
  total_amount. Gained `SyncMetadataMixin` in Phase 2.2 specifically so
  each Shopify line item can be upserted by its own external id on
  resync instead of being re-created wholesale.
- **OrderEvent** — order_id, event_type, status, description, source,
  actor_user_id, event_metadata (JSONB), created_at — **append-only**,
  no `updated_at`, no update/delete repository method exists. This is
  the order timeline; `Order.status` is only the current-state summary.

## Payments **(synced)**

- **Payment** — order_id, payment_type, status (enum: pending /
  authorized / paid / failed / refunded / partially_refunded), amount,
  currency, provider, external_transaction_id, paid_at,
  payment_metadata (JSONB) — created by `OrderService.create_order()`;
  no live gateway in Phase 1, so the API is read-only
- **PaymentTransaction** — payment_id, gateway, gateway_transaction_id,
  status, amount, created_at

## Shipments **(synced)**

- **Shipment** — order_id, shiprocket_shipment_id (unique), awb
  (unique), courier_id, current_status (enum, 9 values from pending
  through delivered/rto/cancelled), delay_status (enum: on_time /
  at_risk / delayed / unknown — Phase 1 foundation only, real delay
  detection is Phase 3/4), ndr_status, rto_status, pickup_date,
  expected_delivery_date, actual_delivery_date, current_location,
  last_tracking_update_at — **current state only**
- **ShipmentEvent** — shipment_id, external_event_id, status, location,
  event_timestamp, description, courier_name, source, raw_payload
  (JSONB), created_at — **append-only**. Dedup: unique
  `(shipment_id, external_event_id)` when the courier provides a stable
  ID; `ShipmentService.add_tracking_event()` falls back to a
  `(status, event_timestamp)` check when it doesn't.
- **Courier** — name, code (unique — required, but has no Shiprocket
  equivalent; a synced courier gets a name-derived slug, de-duplicated
  against existing codes), is_active, courier_metadata (JSONB). Gained
  `SyncMetadataMixin` in Phase 2.3 — synced from Shiprocket's AWB-assignment
  response via `CourierService.upsert_synced_courier(source_system="shiprocket",
  external_id=courier_company_id)`; database records only until then (Phase 1).

## NDR / RTO **(synced)**

- **NDR** — shipment_id, order_id, courier_id, reason,
  normalized_reason, external_reason (courier-specific text is never
  assumed universal), attempt_number, status (enum: open →
  customer_contacted / reattempt_scheduled → resolved, or
  rto_initiated), customer_response, reattempt_status, reattempt_date,
  notes. Populated from Shiprocket's NDR list as of Phase 2.3
  (`NDRService.upsert_synced_ndr` resolves the owning `Shipment` by AWB).
- **RTO** — shipment_id, order_id, courier_id, reason,
  normalized_reason, external_reason, status (enum: initiated →
  in_transit → received, or cancelled), initiated_at, completed_at,
  notes, rto_metadata (JSONB). As of Phase 2.3, derived automatically as
  a side effect of Shiprocket tracking refresh (no separate RTO-list
  endpoint could be confirmed — see `docs/integrations/shiprocket.md`),
  not synced independently.

## Returns / Refunds **(synced)**

- **Return** — order_id, order_item_id, customer_id, reason, status
  (enum: requested → approved → in_transit → received → completed, or
  rejected/cancelled), quantity, requested_at/approved_at/received_at/completed_at,
  notes
- **Refund** — order_id, payment_id, return_id (nullable — refunds can
  exist without a return, e.g. cancellations), amount, reason, status
  (enum: pending / processing / completed / failed / cancelled),
  initiated_at, completed_at, refund_metadata (JSONB). Phase 1's only
  creation path: `ReturnService` creates one when a `Return` reaches
  `COMPLETED` — no live payment gateway, so the API is read-only.

## Integrations / Sync (Phase 2.1)

Not `SyncMetadataMixin`-based — these tables track the sync process
itself, not a synced business entity. Created via Alembic migration
`4d60488e0bdb_phase_2_1_integration_and_sync_.py`. No table
here ever stores a credential — see
`docs/architecture/integrations.md#credentials`.

- **Integration** — name, code (unique — `shopify`, `shiprocket`,
  `blue_dart`, `delhivery`, `ecom_express`, `whatsapp`, `meta`,
  `instagram`), type (enum: ecommerce/courier/messaging/social), status
  (enum: connected/disconnected/error/syncing/disabled), enabled,
  configuration (JSONB, non-secret metadata only), last_sync_at,
  last_successful_sync_at, last_failure_at, last_failure_message. Seeded
  for every known provider as `disconnected`/`enabled=False` — an honest
  "Not Connected" until a real adapter is registered and configured
  (Shopify, as of Phase 2.2 — see `docs/integrations/shopify.md`).
- **SyncJob** — integration_id, sync_type (enum: full/incremental/webhook),
  entity_type, status (enum: queued/running/completed/partial/failed/cancelled),
  started_at, completed_at, records_received/created/updated/skipped/failed,
  error_count, job_metadata (JSONB). One row per sync execution; owned
  end-to-end by `app/services/sync_service.py:SyncService`.
- **SyncError** — sync_job_id, integration_id, entity_type, external_id,
  error_type, error_message, payload_reference (a pointer, e.g. a
  `WebhookEvent` id — never the raw payload itself), retry_count,
  resolved, resolved_at.
- **WebhookEvent** — integration_id, event_type, external_event_id,
  external_resource_id, received_at, processed_at, status (enum:
  received/processing/processed/failed/ignored), retry_count, payload
  (JSONB), error_message. `UniqueConstraint(integration_id,
  external_event_id)` is the idempotency guarantee — `external_event_id`
  is always populated, falling back to a deterministic hash
  (`app/services/webhook_service.py:compute_fallback_event_id`) for
  providers without a stable event id, so a retried delivery of an
  identical payload still collides on the same row instead of creating a
  duplicate.

## Reconciliation (Phase 2.4)

Not `SyncMetadataMixin`-based, same reasoning as Integrations/Sync above
— these track the reconciliation *process*, never a synced business
entity, and `ReconciliationService` never writes to a business table (no
Order/Shipment/NDR/RTO row is ever mutated by a reconciliation run).
Created via Alembic migration `50337406e09a_phase_2_4_reconciliation.py`.

- **ReconciliationRun** — triggered_by_user_id, status (enum:
  running/completed/failed), started_at, completed_at, total_checked,
  reconciled_count, mismatch_count, missing_count, error_count,
  run_metadata (JSONB — which checks ran vs. were skipped because a
  provider wasn't configured, never fabricated). One row per triggered
  run; owned end-to-end by `app/services/reconciliation_service.py:ReconciliationService`.
- **ReconciliationResult** — run_id, check_type, provider, entity_type,
  internal_id, external_id, expected_value (JSONB), actual_value (JSONB),
  status (enum: reconciled/mismatch/missing/error), message, resolved,
  resolved_at, resolved_by_user_id. `UniqueConstraint(run_id, check_type,
  internal_id, external_id)` guards against a single check double-reporting
  the same entity within one run. Each run's results are a fresh
  snapshot — old runs' results remain as history, they're never
  overwritten by a later run (spec: "store enough information to
  understand ... timestamp ... resolution status").

## Audit

- **AuditLog** — OMS-owned, never externally sourced. user_id, action
  (e.g. `order.status_changed`), entity_type, entity_id, previous_value
  (JSONB), new_value (JSONB), ip_address, user_agent, audit_metadata
  (JSONB), created_at — **append-only**, no `updated_at`. Written
  explicitly by `app/services/audit_service.py:AuditService.record()` at
  the mutation points spec'd in `docs/security/audit-logs.md` (order
  create/update/status-change, shipment update, NDR/RTO status change,
  customer update, return/refund creation) — not wrapped around every
  service call indiscriminately. Never stores passwords, tokens, or
  secrets.

## Conventions

- UUID primary keys everywhere (`UUIDPrimaryKeyMixin`, cross-dialect
  `GUID` type)
- `created_at` / `updated_at` on every table that isn't append-only-only
  (`TimestampMixin`); append-only history tables (`OrderEvent`,
  `ShipmentEvent`, `AuditLog`) have `created_at` only — there's nothing
  to update
- Foreign keys are always indexed
- Status columns use `sqlalchemy.Enum` (native Postgres enum type in
  production, `VARCHAR` + `CHECK` on SQLite in tests — see
  `app/models/enums.py:sa_enum()`, which reuses one Postgres enum type
  per name across every table/column that shares it, rather than
  emitting a duplicate `CREATE TYPE`), never free-text strings
- Every table that mirrors an external entity stores that entity's ID
  with a unique constraint (`shopify_order_id`, `awb`, ...) *and* the
  generic `(source_system, external_id)` pair via `SyncMetadataMixin`,
  to make sync idempotent at both the domain-specific and generic level
- Money fields are always `NUMERIC(12,2)`, never float
