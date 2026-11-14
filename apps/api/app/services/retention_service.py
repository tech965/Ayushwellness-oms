"""Non-destructive retention for `SyncMetadataMixin.raw_external_payload`
(API-first / minimal-storage initiative, Phase 1). Every normalized field
a UI/report/service actually reads (status, amounts, tracking numbers,
`source_system`/`external_id`/`last_synced_at`) lives in its own typed
column, never inside this JSONB blob -- it exists purely as a
full-fidelity debug/reconciliation copy of whatever the platform last
sent, and is the single largest storage-duplication lever in the schema
(the same mixin column, inherited by every synced table below).

This module only ever NULLs that one already-nullable column on rows
past a configurable age. It never drops a column or table, never
deletes a row, and never runs automatically -- see
`app.api.v1.endpoints.retention` for the dry-run-by-default, operator-
triggered, audit-logged endpoint that calls this.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.abandoned_checkout import AbandonedCheckout
from app.models.courier import Courier
from app.models.customer import Customer, CustomerAddress
from app.models.ndr import NDR
from app.models.order import Order, OrderItem
from app.models.payment import Payment
from app.models.product import Product, ProductVariant
from app.models.refund import Refund
from app.models.returns import Return
from app.models.rto import RTO

# Every `SyncMetadataMixin`-backed model -- i.e. every table that has a
# `raw_external_payload` column to begin with -- EXCEPT `shipments`
# (see below). Deliberately excludes append-only history tables
# (`order_events`, `shipment_events`, `webhook_events`) -- those need a
# row-retention/archival policy, not a single-column clear, and Phase 1
# only measures them (see `scripts/audit_storage.py`), it doesn't act on
# them yet.
RETAINABLE_TABLES: dict[str, type[Any]] = {
    "orders": Order,
    "order_items": OrderItem,
    "customers": Customer,
    "customer_addresses": CustomerAddress,
    "products": Product,
    "product_variants": ProductVariant,
    "payments": Payment,
    "refunds": Refund,
    "abandoned_checkouts": AbandonedCheckout,
    "ndrs": NDR,
    "rtos": RTO,
    "returns": Return,
    "couriers": Courier,
}

# `Shipment.raw_external_payload` is deliberately NOT retainable here --
# Phase 2 verification found a real, live dependency: `app.services.
# shiprocket_service.shiprocket_order_id()` reads `raw_external_payload
# ["order_id"]` directly (no other column stores Shiprocket's real
# numeric order id), and that id drives the Fulfillment Queue's "Ship
# Order"/"Process Shipment" actions (copies it to the clipboard to open
# Shiprocket's "Ready to Ship" page) for every shipment this OMS created
# itself via `create_shipment_for_order`. Clearing it on an old shipment
# would silently make that action unusable for that shipment -- not a
# debug-only copy like every other table's payload. See
# `test_retention_service.py::
# test_shipments_excluded_because_shiprocket_order_id_depends_on_raw_payload`
# for the regression this documents. Revisit only after
# `shiprocket_order_id` is persisted on its own dedicated column.


class RetentionError(ValueError):
    """An unknown table name was requested -- never silently a no-op."""


async def null_out_stale_raw_payloads(
    session: AsyncSession,
    *,
    table: str,
    older_than_days: int,
    dry_run: bool = True,
) -> int:
    """Clears `raw_external_payload` on rows whose `updated_at` is older
    than `older_than_days` for the given table. Returns the number of
    rows affected (or, under `dry_run=True`, the number that *would* be
    affected -- no write happens in that case).
    """
    model = RETAINABLE_TABLES.get(table)
    if model is None:
        raise RetentionError(
            f"Unknown table '{table}'. Known tables: {sorted(RETAINABLE_TABLES)}."
        )

    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    condition = (model.raw_external_payload.is_not(None)) & (model.updated_at < cutoff)

    if dry_run:
        count_stmt = select(func.count()).select_from(model).where(condition)
        result = await session.execute(count_stmt)
        return int(result.scalar_one())

    stmt = (
        update(model)
        .where(condition)
        .values(raw_external_payload=None)
        .execution_options(synchronize_session=False)
    )
    cursor_result = cast(CursorResult[Any], await session.execute(stmt))
    return cursor_result.rowcount or 0
