# Inventory Management System (IMS)

Workspace route: `/ims` (the older `/inventory` OMS stock pages are untouched). API prefix: `/api/v1/ims`.

## Model
- `warehouses` (existing table, extended with `address`, `description`, `sort_order`, `is_ims`). Four IMS slots are seeded by the migration: Royal Palm 251, Royal Palm 310, Office 402 and an **inactive, unconfigured `SLOT-4`** (name/code to be supplied; edit under *Warehouses*). Mithani is an `ims_external_parties` row, never a warehouse.
- `ims_items`: one row per tracked thing — a finished-goods Shopify variant (`kind=finished`, identity + label only) or one packaging unit of a packaging product (`kind=packaging`, `unit` in BOX/OUTER/SACHETS/ZIPPER/BOTTLE/JAR). Finished goods and packaging are never summed.
- `ims_balances` (running balance, `CHECK quantity >= 0`) and `ims_ledger` (append-only: ORM guard + PostgreSQL trigger).
- `ims_unit_conversions`: per packaging product, "1 larger = N smaller"; contradictory rule sets are rejected. Display only — never consumes/produces stock.
- `ims_transfers(+_lines)`, `ims_returns(+_lines)`, `ims_adjustments` (immutable), each with a unique reference number and client `idempotency_key`.

## Behaviour
- Every stock change locks the (warehouse, item) balance rows in a fixed order (`SELECT … FOR UPDATE`), writes balance + ledger rows in one transaction, and rejects negative stock.
- Transfers are immediate (no receiving step): `transfer_out` + `transfer_in` ledger rows share the transfer reference.
- Historical stock = sum of ledger deltas up to the end of the chosen IST day.
- Opening balances: `POST /ims/stock/opening-balances`, only for (warehouse, item) pairs without history. Nothing is imported automatically.
- Shopify: catalogue is fetched live through the existing integration (`live_catalog.fetch_products`), cached in-process (`IMS_CATALOG_CACHE_SECONDS`, default 300) — no catalogue snapshot in PostgreSQL. Manual refresh only updates labels of already-tracked items.
- Low stock: per-item `low_stock_threshold`, else `IMS_DEFAULT_LOW_STOCK_THRESHOLD` (10) for finished goods; packaging has no default.

## Permissions (seeded by `scripts/seed.py`)
`ims.read`, `ims.stock.write` (inward/transfer/return/adjust), `ims.manage` (warehouses, packaging, conversions, parties, opening balances, refresh). Roles: `INVENTORY_STAFF` (read+write), `INVENTORY_MANAGER` (all). ADMIN has everything.
