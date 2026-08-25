# API Conventions

Base path: `/api/v1`. 60 routes are implemented as of Phase 2.4 (59
RBAC-gated `/api/v1/*` routes + the Shopify webhook) — see
`apps/api/app/api/v1/router.py` for the full route-group list and
`docs/database/schema.md` for the models each group serves. Endpoints
not needed until a later phase (Shiprocket/courier webhooks, automation,
alerts, tasks) remain empty routers with a docstring, as they were in
Phase 0.

## Integration monitoring (Phase 2.1)

Read-only, RBAC-gated (`integrations.read`/`sync_jobs.read`/`webhooks.read`):

- `GET /integrations`, `GET /integrations/{id}`
- `GET /integrations/{id}/health` — last persisted health snapshot
- `POST /integrations/{id}/health-check` (`integrations.test`) — actively
  probes the registered adapter and persists the result. Shopify and
  Shiprocket both have adapters registered (Phase 2.2/2.3), so this
  reports a real Connected/Not Configured/Connection Error outcome for
  either; any other integration code still reports "no adapter registered."
- `GET /integrations/{id}/sync-history`
- `GET /sync-jobs`, `GET /sync-jobs/{id}`
- `GET /webhook-events`, `GET /webhook-events/{id}` — never returns the
  raw `payload` (see `docs/architecture/integrations.md`)
- `POST /sync/{integration_id}/trigger` (`sync_jobs.manage`) — creates a
  `SyncJob` and hands it to Celery; returns `202` immediately, never runs
  a sync inline on the request thread. For Shopify: `customers`,
  `products`, `orders`. For Shiprocket: `ndr` (provider-paginated,
  generic loop) and `tracking` (OMS-shipment-driven, its own dedicated
  task — see `docs/integrations/shiprocket.md`).

## Shiprocket operational actions (Phase 2.3)

Push actions, not sync — each calls Shiprocket synchronously and updates
OMS state only after Shiprocket confirms success (`shipments.update` /
`ndr.update`, the same permissions their read/update endpoints already use):

- `POST /orders/{id}/ship` — creates a Shiprocket order + shipment from
  this OMS order
- `POST /shipments/{id}/shiprocket/assign-awb`
- `POST /shipments/{id}/shiprocket/cancel`
- `POST /shipments/{id}/shiprocket/request-pickup`
- `POST /shipments/{id}/shiprocket/refresh-tracking` — on-demand,
  single-shipment version of the tracking-refresh sync
- `POST /ndr/{id}/reattempt` — requests a delivery reattempt via
  Shiprocket; the NDR's status only changes if Shiprocket confirms

## Reconciliation (Phase 2.4)

`reconciliation.read`/`reconciliation.manage` — new permissions, not a
reuse of an existing one, since reconciliation is a genuinely new
capability (compare-and-report across providers), not close enough to
sync/integration monitoring to share a permission:

- `POST /reconciliation/runs` (`reconciliation.manage`) — creates a
  `RUNNING` `ReconciliationRun` and hands it to Celery; `202` immediately,
  same broker-unavailable-must-not-lie fallback as `/sync/{id}/trigger`
- `GET /reconciliation/runs`, `GET /reconciliation/runs/{id}`
  (`reconciliation.read`)
- `GET /reconciliation/results` (`reconciliation.read`) — filterable by
  `run_id`, `status`, `check_type`, `provider`, `resolved`
- `POST /reconciliation/results/{id}/resolve` (`reconciliation.manage`) —
  marks one result reviewed; reconciliation never resolves itself, and
  never corrects the underlying OMS/provider data

## Response envelope

Every response — success or error — uses the same shape
(`app/schemas/response.py`):

```json
{
  "success": true,
  "data": { "...": "..." },
  "message": "Success",
  "meta": {}
}
```

Paginated list endpoints use `PaginatedResponse`:

```json
{
  "success": true,
  "data": [ "...": "..." ],
  "message": "Success",
  "meta": { "page": 1, "page_size": 20, "total_items": 134, "total_pages": 7 }
}
```

Errors:

```json
{
  "success": false,
  "error": { "code": "not_found", "message": "Order not found.", "details": {} },
  "meta": {}
}
```

`error.code` is a stable machine-readable string (see
`app/core/exceptions.py` for the full list: `not_found`,
`validation_error`, `conflict`, `authentication_error`,
`authorization_error`, `integration_error`,
`webhook_verification_error`, `rate_limit_exceeded`, `internal_error`,
`http_error`). Frontend code should switch on `error.code`, not parse
`error.message`.

## Authentication

Every route except `POST /auth/login` and `POST /auth/refresh` requires
`Authorization: Bearer <access_token>`. See `docs/api/authentication.md`.
Mutating routes additionally require a specific permission — see
`docs/security/rbac.md`. A missing/invalid token returns `401
authentication_error`; a valid token lacking the required permission
returns `403 authorization_error`.

## Pagination, sorting, filtering

Shared query dependencies
(`app/dependencies/pagination.py` + `app/schemas/common.py`):

- `page` (default 1), `page_size` (default 20, max 200)
- `sort_by`, `sort_order` (`asc` | `desc`, default `desc`)
- Module-specific filters are added per endpoint as query params, e.g.:
  - `GET /orders?status=shipped&payment_status=paid&q=...&date_from=...&date_to=...`
  - `GET /shipments?status=in_transit&courier_id=...&order_id=...`
  - `GET /payments?order_id=...`, `GET /returns?order_id=...`,
    `GET /refunds?order_id=...`, `GET /shipments?order_id=...` — every
    order-scoped read the order detail page needs is a query param, not
    a separate nested route
  - `GET /customers/{id}/orders` — the one exception, since it's
    explicitly spec'd as a sub-resource

## Status codes

- `200` — success (GET, successful PATCH/PUT)
- `201` — resource created
- `401` — missing/invalid/expired token
- `403` — authenticated but not authorized (RBAC)
- `404` — resource not found
- `409` — conflict (e.g. duplicate order_number, invalid status
  transition)
- `422` — request validation failed
- `429` — rate limited
- `502` — upstream integration failure (Phase 2+)
- `500` — unexpected error (no stack trace in the body — see
  `docs/architecture/backend.md#error-handling`)

## Read-only endpoints (by design, not oversight)

`GET /payments`, `GET /payments/{id}`, `GET /refunds`, `GET
/refunds/{id}` have no `POST`/`PATCH` — Phase 1 has no live payment
gateway. `Payment` rows are created by `OrderService.create_order()`;
`Refund` rows are created by `ReturnService` when a `Return` reaches
`COMPLETED`. Likewise `GET /ndr`, `GET /rto` have no `POST` — those
records are populated by Phase 2 sync adapters (or, today, directly at
the repository layer in tests) via
`BaseRepository.upsert_by_external_id()`; only their `PATCH` (status
update) is a Phase 1 user action.

## Webhooks

`POST /api/v1/webhooks/shopify` — **IMPLEMENTED** (Phase 2.2). Verifies
`X-Shopify-Hmac-Sha256` against the raw body before doing anything else;
an invalid/missing signature is rejected `401` and never reaches
`WebhookService`. Topic comes from `X-Shopify-Topic`; idempotency key is
`X-Shopify-Webhook-Id` (falls back to a deterministic payload hash if a
provider omits it). Always acks `200` once the signature and JSON body
are valid, including for a duplicate delivery — see
`docs/integrations/shopify.md#data-flow`.

`/api/v1/webhooks/{shiprocket,couriers}/*` remain empty routers —
provider signature verification needs a real adapter to verify against,
which is a later phase for those two. The generic idempotency layer they
share with Shopify (`WebhookEvent`, `WebhookService.ingest`) already
exists as of Phase 2.1 — see
`docs/architecture/integrations.md#idempotency-contract`.

## Versioning

The `/api/v1` prefix is the only versioning mechanism for now. A breaking
change to a shipped endpoint would be introduced as `/api/v2/...` rather
than mutating the v1 contract in place.
