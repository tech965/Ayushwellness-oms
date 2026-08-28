"""Generic `entity_type -> OMS service upsert` dispatch.

Shared by `app.services.sync_service.SyncService.execute_sync`'s
fetch/normalize/upsert loop and `app.tasks.webhook_processing`, so
neither has to hardcode which OMS service owns which entity type more
than once. This is the one place in the integrations layer that imports
OMS domain services — adapters themselves never do (see
`docs/architecture/integrations.md#why-the-oms-core-must-not-import-a-provider-sdk`,
which is also why this dependency only points one direction).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.repositories.order import OrderRepository
from app.services.customer_service import CustomerService
from app.services.ndr_service import NDRService
from app.services.order_service import OrderService
from app.services.product_service import ProductService
from app.services.shipment_service import ShipmentService

UpsertHandler = Callable[[AsyncSession, dict[str, Any]], Awaitable[tuple[Any, bool]]]


async def _upsert_customer(session: AsyncSession, data: dict[str, Any]) -> tuple[Any, bool]:
    return await CustomerService(session).upsert_synced_customer(**data)


async def _upsert_product(session: AsyncSession, data: dict[str, Any]) -> tuple[Any, bool]:
    return await ProductService(session).upsert_synced_product(**data)


async def _upsert_order(session: AsyncSession, data: dict[str, Any]) -> tuple[Any, bool]:
    return await OrderService(session).upsert_synced_order(**data)


async def _upsert_ndr(session: AsyncSession, data: dict[str, Any]) -> tuple[Any, bool]:
    return await NDRService(session).upsert_synced_ndr(**data)


async def _upsert_shipment(session: AsyncSession, data: dict[str, Any]) -> tuple[Any, bool]:
    """A Shiprocket shipment has no OMS order id of its own — `Shipment.
    order_id` is a required FK, so one must be resolved (never invented)
    before `ShipmentService.upsert_synced_shipment` can run at all.
    `channel_order_id` (the merchant's own order number, the same value
    Shiprocket reports on NDR records too) is the only field Shiprocket
    returns that a real, already-existing OMS `Order` can be looked up
    by — `OrderRepository.get_by_order_number` is exactly that existing
    lookup, not a new one invented for this.

    A shipment whose `channel_order_id` doesn't resolve to a real OMS
    order raises `NotFoundError`, exactly like `NDRService.
    upsert_synced_ndr` already does for an unmatched AWB (spec §16, "do
    not invent NDR/shipment data") — `SyncService._run_entity_sync`'s
    existing per-record try/except records it as a `SyncError` and moves
    on to the next shipment; the job still lands PARTIAL, not FAILED,
    and nothing fabricated ever reaches the database.
    """
    channel_order_id = data.pop("channel_order_id", None)
    order = (
        await OrderRepository(session).get_by_order_number(channel_order_id)
        if channel_order_id
        else None
    )
    if order is None:
        raise NotFoundError(
            "No OMS order found for Shiprocket shipment "
            f"(channel_order_id={channel_order_id!r})."
        )
    return await ShipmentService(session).upsert_synced_shipment(order_id=order.id, **data)


ENTITY_UPSERT_HANDLERS: dict[str, UpsertHandler] = {
    "customers": _upsert_customer,
    "products": _upsert_product,
    "orders": _upsert_order,
    "ndr": _upsert_ndr,
    "shipments": _upsert_shipment,
}
