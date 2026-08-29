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
from app.repositories.shipment import ShipmentRepository
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
    """Round 13 fix: a Shiprocket shipment this OMS already knows about
    (every one ever created went through `ShiprocketOperationsService.
    create_shipment_for_order` — the only code path that creates a
    `Shipment` row) already has a correct `order_id`, set the day it was
    created, from the real OMS order it was created *for* — never
    derived from anything Shiprocket returns. Its `(source_system,
    external_id)` — `external_id` being that shipment's own Shiprocket
    `id`, confirmed live to be exactly what `ShiprocketShipmentNormalizer`
    already reads (`raw.get("id")`) — is the reliable, pre-existing
    identity, so it's checked *first*, before any order lookup is even
    attempted. Real production evidence for why this matters: 150/150
    real shipments failed with "channel_order_id=None" because
    `/shipments` simply doesn't have a `channel_order_id` field at all —
    but every one of those shipments' `Shipment` rows already existed in
    this OMS with a correct `order_id`, and this path never got to
    reuse it.

    Only a shipment with NO existing `Shipment` row falls through to the
    order-lookup path below — genuinely new to this OMS, where an
    `order_id` must still be resolved (never invented) via
    `channel_order_id`/`OrderRepository.get_by_order_number`, exactly as
    before. A shipment that reaches that path and still can't be
    resolved raises `NotFoundError`, exactly like `NDRService.
    upsert_synced_ndr` already does for an unmatched AWB (spec §16, "do
    not invent NDR/shipment data") — `SyncService._run_entity_sync`'s
    existing per-record try/except records it as a `SyncError` and moves
    on to the next shipment; the job still lands PARTIAL, not FAILED,
    and nothing fabricated ever reaches the database.
    """
    source_system = data.get("source_system")
    external_id = data.get("external_id")
    existing = (
        await ShipmentRepository(session).get_by_source_external_id(
            source_system=source_system, external_id=external_id
        )
        if source_system and external_id
        else None
    )

    if existing is not None:
        data.pop("channel_order_id", None)  # not needed -- order_id is already known
        return await ShipmentService(session).upsert_synced_shipment(
            order_id=existing.order_id, **data
        )

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
