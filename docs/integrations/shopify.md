# Shopify Integration

Status: **IMPLEMENTED** (Phase 2.2). Real GraphQL Admin API client,
adapter, normalizers, and a real webhook endpoint with HMAC verification
— all built on the generic Phase 2.1 infrastructure
(`Integration`/`SyncJob`/`SyncError`/`WebhookEvent`, `SyncService`,
`WebhookService`, retry/idempotency). No live provider was called during
implementation — no real Shopify account/credentials were available (see
"Verification" below); every path was exercised against mocked GraphQL
responses.

## API choice: GraphQL Admin API, not REST

Shopify requires new integrations to use the **GraphQL Admin API** —
REST became legacy for new apps starting April 2025. This integration
uses GraphQL exclusively: one endpoint
(`https://{shop}/admin/api/{version}/graphql.json`), cursor-based
pagination (`pageInfo.hasNextPage`/`endCursor`), and cost-based rate
limiting (`extensions.cost.throttleStatus`), rather than REST's
leaky-bucket `X-Shopify-Shop-Api-Call-Limit` header and page-based
pagination.

- **API version**: `SHOPIFY_API_VERSION` (default `"2026-01"`, the
  stable release at implementation time). Shopify ships a new version
  quarterly — re-verify field/enum names against a live shop's schema
  introspection before bumping, since a version bump can rename or
  retire fields.
- **Authentication**: a per-shop Admin API access token in the
  `X-Shopify-Access-Token` header (custom/private app model — no OAuth
  flow implemented here, since this integration targets one store the
  operator controls, not a public app installed by many merchants).
- **Scopes required** on the access token: `read_customers`,
  `read_products`, `read_orders` (read-only — this integration never
  writes back to Shopify).

## Components (`apps/api/app/integrations/shopify/`)

| File | Responsibility |
|---|---|
| `config.py` | `ShopifyConfig.from_settings()` — reads `SHOPIFY_STORE_DOMAIN`/`SHOPIFY_ACCESS_TOKEN`/`SHOPIFY_API_VERSION`; returns `None` (not an error) when unconfigured. |
| `client.py` | `ShopifyClient` — the only thing that calls Shopify. One `execute(query, variables)` method; classifies every failure via `errors.py`, retries transient ones (timeout/network/429/5xx/GraphQL `THROTTLED`) with `app.integrations.retry`'s exponential backoff. |
| `errors.py` | Maps `httpx` exceptions and GraphQL `errors[].extensions.code` to the `error_type` vocabulary `app.integrations.retry` already classifies as retryable/non-retryable. |
| `queries.py` | GraphQL query documents for `shop` (ping), `customers`, `products` (+ variants), `orders` (+ line items) — see "Field mappings" below. |
| `normalizer.py` | `ShopifyCustomerNormalizer`/`ShopifyProductNormalizer`/`ShopifyOrderNormalizer` — pure functions, no I/O. |
| `adapter.py` | `ShopifyAdapter(IntegrationAdapter)` — `authenticate`, `health_check`, `fetch`/`fetch_incremental` (one page each, cursor-paginated), `normalize`, `process_webhook`. Never imports an OMS service — see "Why the OMS core must not import a provider SDK" in `docs/architecture/integrations.md`, which holds in both directions here. |
| `webhooks.py` | `verify_webhook_hmac()` — HMAC-SHA256 over the raw body, base64, `hmac.compare_digest`. |
| `__init__.py` | `register()` — adds a `ShopifyAdapter` to `app.integrations.registry` regardless of whether credentials are configured, so health checks report "Not Configured" rather than "no adapter registered." Called from `app.integrations.bootstrap.register_all_adapters()`, itself called from both `app.main`'s lifespan and `app.workers.celery_app` module import (the registry is in-memory and per-process). |

## Data flow

**Pull sync** (`POST /api/v1/sync/{integration_id}/trigger` or a future
scheduled task):

```
FastAPI creates a SyncJob (QUEUED)
  -> Celery (app.tasks.sync_tasks.execute_sync_task)
    -> SyncService.execute_sync
      -> ShopifyAdapter.fetch / fetch_incremental (one page)
      -> ShopifyAdapter.normalize
      -> app.integrations.entity_sync.ENTITY_UPSERT_HANDLERS[entity_type]
         (CustomerService.upsert_synced_customer /
          ProductService.upsert_synced_product /
          OrderService.upsert_synced_order)
      -> repeat until pageInfo.hasNextPage is false
    -> SyncJob COMPLETED / PARTIAL / FAILED
```

`SyncService.execute_sync` is unchanged from Phase 2.1 in shape — Phase
2.2 fills in what was previously a stub, using the same
`ENTITY_UPSERT_HANDLERS` dispatch table the webhook path uses below, so
a pull sync and a webhook converge on the identical OMS service call for
a given entity type.

**Webhook** (`POST /api/v1/webhooks/shopify`):

```
Shopify POSTs (topic in X-Shopify-Topic)
  -> verify_webhook_hmac(raw body, X-Shopify-Hmac-Sha256, SHOPIFY_WEBHOOK_SECRET)
     -> 401 and DROP if invalid — never processed
  -> WebhookService.ingest() — idempotent on
     (integration_id, X-Shopify-Webhook-Id)
  -> 200 ack immediately (< 5s, per Shopify's requirement)
  -> app.tasks.webhook_processing.process_webhook_event_task.delay(event.id)
     -> ShopifyAdapter.process_webhook(topic, payload) -> {entity_type, normalized}
     -> ENTITY_UPSERT_HANDLERS[entity_type](session, normalized)
     -> WebhookEvent marked PROCESSED / FAILED
```

Subscribed topics: `orders/create`, `orders/updated`, `orders/cancelled`,
`customers/create`, `customers/update`, `products/create`,
`products/update`. `entity_type` is derived from the topic
(`topic.split("/")[0]`) — a convention specific to Shopify's topic
naming, applied in the task layer, not baked into the generic
`WebhookService`.

## Idempotency

Both paths converge on the same guarantee: **never a duplicate
Customer/Product/Order for the same Shopify record.**

- Pull sync: `CustomerRepository`/`ProductRepository`/`OrderRepository`.
  `upsert_by_external_id(source_system="shopify", external_id=...)` —
  the generic Phase 1 mechanism, unchanged.
- Webhook: `WebhookEvent` has a unique constraint on
  `(integration_id, external_event_id)`. Shopify's `X-Shopify-Webhook-Id`
  header is used as `external_event_id` when present; if a provider ever
  omits it, `WebhookService.compute_fallback_event_id` hashes
  `(integration, topic, payload)` deterministically instead.
- Line items: `OrderItem` gained `SyncMetadataMixin` in the Phase 2.2
  migration specifically so each Shopify line item can be upserted by
  its own external id rather than re-created wholesale on every resync.

**Known limitation**: a line item *removed* from an order in Shopify
between two syncs is not deleted from the OMS — only items present in
the latest payload are upserted. Full reconciliation (diff + delete
stale items) is deferred to a later pass.

## Field mappings

Customer, Product/ProductVariant, and Order/OrderItem field-by-field
mappings live as code comments next to the mapping itself
(`app/integrations/shopify/normalizer.py`) rather than duplicated here,
so they can't drift out of sync with the implementation. The two
mappings with real information loss (documented at their definition
site):

- **Payment status** (`displayFinancialStatus` -> OMS `PaymentStatus`):
  `PARTIALLY_PAID` and `PAID` both map to `PAID` (OMS has no
  partial-payment state); `VOIDED`/`EXPIRED` both map to `FAILED`. The
  exact Shopify string always survives in `raw_external_payload` for
  reconciliation.
- **Payment type**: inferred from `paymentGatewayNames` — a gateway name
  containing "cod"/"cash on delivery" (case-insensitive) maps to `COD`,
  otherwise `PREPAID`, empty list maps to `OTHER`. Shopify has no
  first-class COD-vs-online-payment field.

## Field ownership / conflict handling (spec §27)

Shopify is the source of truth for every field the normalizer produces
(financial figures, `payment_status`, `fulfillment_status`, addresses,
customer/product identity fields) — a resync always overwrites these.
**`Order.status`** — the OMS-internal pack/ship *operational* workflow
enum (`ORDER_STATUS_TRANSITIONS` in `app/services/order_service.py`) —
is different: it's OMS-owned, set once at creation (`CONFIRMED` if the
order syncs in already paid, else `PENDING`), and a resync never rewinds
or fast-forwards it. The one exception: if Shopify reports the order
cancelled (`cancelledAt` set), the OMS transitions to `CANCELLED` *if*
that's currently a valid transition from the order's current state —
Shopify is authoritative for "did the merchant cancel this," but the
OMS's own state machine still gates whether the transition is legal
right now (e.g. an already-`DELIVERED` order is not force-cancelled).

This same ownership split is why couriers/Shiprocket data (a later
phase) is never touched here: shipment/NDR/RTO fields belong to that
integration, not Shopify's.

## Credentials {#credentials}

Set in `.env` (never committed, never returned by any API response —
see `app.integrations.credentials`):

```
SHOPIFY_STORE_DOMAIN=your-shop.myshopify.com
SHOPIFY_ACCESS_TOKEN=shpat_...
SHOPIFY_WEBHOOK_SECRET=...
SHOPIFY_API_VERSION=2026-01   # optional, defaults to 2026-01
```

Until `SHOPIFY_STORE_DOMAIN`/`SHOPIFY_ACCESS_TOKEN` are set, every health
check reports `"Not Configured"` (`GET /api/v1/integrations/{id}/health`,
`POST /api/v1/integrations/{id}/health-check`) rather than failing the
API or attempting any network call.

## Rate limiting & retry

GraphQL cost-based throttling (`extensions.cost.throttleStatus` /
`extensions.code == "THROTTLED"`) and HTTP `429`/`5xx`/timeout/network
errors are all classified as retryable and retried with
`app.integrations.retry`'s exponential backoff
(`RetryPolicy(max_retries=5, base_delay_seconds=60, ...)` by default).
Authentication (401) and authorization (403) failures are classified
non-retryable — retrying a bad token can't succeed, so `ShopifyClient`
raises immediately instead of burning through retry attempts.

## Verification

Built and tested entirely against **mocked** Shopify GraphQL responses
(`httpx.MockTransport` for the client, hand-built response shapes for
the adapter/sync/webhook tests) — no real Shopify store or credentials
were available in the environment this was built in, and none were
invented. 67 tests across
`apps/api/tests/test_shopify_{client,adapter,normalizer,sync,webhooks}.py`
cover authentication, health check, pagination, rate-limit/retry,
normalization (customer/product/variant/order/line-item/payment-status),
idempotent upsert + duplicate prevention for all three entities, partial
sync failure, `SyncJob` lifecycle, incremental sync, webhook HMAC
validation (valid/invalid/missing), duplicate webhook delivery, webhook
processing, RBAC, and credential protection. Before pointing this at a
real store for the first time: run a `Test Connection` from
`/integrations/{id}` and re-verify the GraphQL field/enum names in
`queries.py` against that store's live schema.
