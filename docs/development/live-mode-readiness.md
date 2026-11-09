# Live-data mode: readiness checklist

`SHOPIFY_LIVE_MODE` must stay **off** until every item below is done on a
staging copy. Nothing here has been run against production or against real
provider accounts. **No real provider API call has been made by any test in
this repository; every provider check below is still outstanding.**

## 1. What has and has not been verified

| Area | Verified | How |
|---|---|---|
| Alembic: fresh base→head, repeated head→base→head cycles, seeded historical upgrade (telecalling enum rewrite, workflow/shipment/checkout/inventory backfills) | Yes | Disposable PostgreSQL 16 (`tests/test_migrations_postgres.py`, set `OMS_TEST_PG_ADMIN_URL`) |
| API workflows (orders, assignment, round-robin, calls, checkouts, inventory, shipments, payments, webhooks, RBAC, reconciliation) | Yes — **providers mocked** | Test suite on real PostgreSQL (`OMS_TEST_DATABASE_URL`) |
| Stock concurrency: no oversell, no lost update, single dispatch, idempotent reserve/dispatch/release | Yes | `tests/test_inventory_concurrency_postgres.py` |
| Those concurrency tests detect the races (fail when the row lock / constraint is removed, test-scoped) | Yes | `tests/test_inventory_concurrency_mutation_postgres.py` |
| Shopify `orderCancel`, `Order.refunds`, `Refund` fields | From official docs (API version 2026-10), **not run on a store** | shopify.dev |
| Any Shopify / Shiprocket / Cashfree live call | **No** — mocked only | – |

Run the PostgreSQL suites only against a throw-away server:

```
OMS_TEST_PG_ADMIN_URL=postgresql://postgres@127.0.0.1:5432/postgres      pytest tests/test_migrations_postgres.py
OMS_TEST_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:5432/oms_test  pytest tests/test_live_*.py tests/test_inventory_*postgres.py
```

## 2. Credentials and scopes needed (development / sandbox only)

| Provider | Environment variables | Minimum access |
|---|---|---|
| Shopify | `SHOPIFY_STORE_DOMAIN`, `SHOPIFY_ACCESS_TOKEN` (or `SHOPIFY_CLIENT_ID` + `SHOPIFY_CLIENT_SECRET`), `SHOPIFY_WEBHOOK_SECRET`, `SHOPIFY_API_VERSION` | `read_orders`, `read_products`, `read_customers`; `read_all_orders` only if orders older than 60 days are needed; `write_orders` **only** for the tag / cancel tests on a throw-away order |
| Shiprocket | `SHIPROCKET_EMAIL`, `SHIPROCKET_PASSWORD`, `SHIPROCKET_PICKUP_LOCATION`, `SHIPROCKET_WEBHOOK_SECRET` | API user with Orders module access; a sandbox/test pickup location |
| Cashfree | `CASHFREE_CLIENT_ID`, `CASHFREE_CLIENT_SECRET` (+ `CASHFREE_WEBHOOK_SECRET`), `CASHFREE_API_URL` (sandbox: `https://sandbox.cashfree.com/pg`) | Payments, Reconciliation and Settlements read access on the **sandbox** account |

Never put these in the repository, test files or logs.

## 3. Real-API checks, in order (read-only first)

**Shopify**
1. Confirm `SHOPIFY_API_VERSION`. The cancel mutation was checked against the 2026-10 documentation (required: `orderId`, `reason`, `restock`; optional `notifyCustomer`, `staffNote`; the `refund` argument is deprecated and is not sent; errors arrive in `orderCancelUserErrors`; the cancel runs as an async job). The repository default is `2026-01`; run a read-only introspection query for `orderCancel` on the chosen version before relying on it.
2. `abandonedCheckouts`: response key (`abandonedCheckouts`), node shape, and the `id:` search. (The legacy adapter reads `abandoned_checkouts`, which never matches the GraphQL key; the live reader bypasses it.)
3. Order `email`; `id:… OR id:…` search; `fulfillment_status:unfulfilled`; product `sku:` / `status:` search.
4. Crawl completeness and `THROTTLED` handling over a real order history.
5. `tagsAdd` / `tagsRemove` on one throw-away order.
6. `orderCancel` on a throw-away order — **only with explicit written approval**; confirm `cancelledAt` appears via `GET /api/v1/live/orders/{id}/shopify-cancel/status`.
7. Refund creation is intentionally not implemented (full `RefundInput` shape not verified; idempotency key optional in 2026-01, required from 2026-04). Refund *reads* (`Order.refunds`, scope `read_orders`) are implemented.

**Shiprocket** — `/courier/track/awb/{awb}` response shape; `/orders/create/adhoc` and `/courier/assign/awb` response shapes; `/ndr/all` shape and pagination; 429 behaviour. Creating, assigning or cancelling a real shipment needs explicit approval.

**Cashfree** — `GET /orders/{id}` 404 `error_type`/status; payments-for-order shape; `/recon` row field names including REFUND rows; `/settlements` shape; 429 behaviour. Checkout creation only on the sandbox.

## 4. Staging procedure (do not run on production directly)

1. Restore a **copy** of production into staging.
2. `alembic upgrade head` on the copy. Check: `order_workflow` rows = `orders` rows; `checkout_workflow` = `abandoned_checkouts`; `shipment_refs` = `shipments`; every `inventory_movements.warehouse_id` set; `provider_variant_mappings` count vs. products with Shopify ids.
3. `python scripts/reconcile_order_workflow.py` (dry run), review the JSON, then `--apply --yes` **while the flag is still off** (apply is refused when it is on).
4. Map variants: `POST /api/v1/live/catalog/mappings/auto-map-by-sku`, review `unmatched`, map the rest with `POST /api/v1/live/catalog/mappings`.
5. Run the real-API checks in section 3.
6. Set `SHOPIFY_LIVE_MODE=true` on **staging only**; smoke-test every screen as admin, team lead and telecaller (telecallers must see only their assigned records).
7. Production: repeat steps 1–4 with a backup, then flip the flag. Rollback = flip it off (stored data is untouched).

## 5. Known limits in live mode

- Shopify orders older than 60 days need `read_all_orders`.
- Live lists and exports are bounded crawls; a truncated result is flagged (`warnings`, `X-Export-Incomplete`).
- No shipment totals / courier performance / standalone RTO list (no bulk Shiprocket API in this integration).
- Per-order Cashfree refunds are not available; refunds come from the bulk reconciliation feed.
- Single default warehouse (`MAIN`); multi-warehouse stock levels are not implemented.
- A variant detail page opens only for variants already mapped to an OMS variant.
- `returns` (OMS-created return requests) still reference local orders and are not available for live-only orders.
