"""OMS-shipment-driven tracking refresh.

Unlike NDR (a genuine provider-paginated list, routed through the
generic `SyncService.execute_sync` loop — see `app.integrations.entity_sync`),
Shiprocket has no documented "list every shipment that changed since X"
feed: the OMS already knows which AWBs it holds. This module composes
`SyncService`'s lifecycle primitives (`record_progress`/`record_error`)
directly, the same way `app.tasks.retry_processing` does, rather than
going through the generic provider-paginated `fetch()` loop — one set of
`SyncJob` primitives, two different looping strategies depending on
which side owns the record list.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.integrations.shiprocket.normalizer import TRACKING_NORMALIZER, extract_tracking_events
from app.models.enums import RTOStatus, ShipmentStatus
from app.models.shipment import Shipment
from app.services.inventory_service import InventoryService
from app.services.rto_service import RTOService
from app.services.shipment_service import ShipmentService
from app.services.sync_service import SyncService

logger = get_logger(__name__)

RTO_STATUS_FROM_SHIPMENT_STATUS: dict[ShipmentStatus, RTOStatus] = {
    ShipmentStatus.RTO_INITIATED: RTOStatus.INITIATED,
    ShipmentStatus.RTO_DELIVERED: RTOStatus.RECEIVED,
}

# A shipment reaching any of these means the courier has physically taken
# the order out for delivery -- the "dispatched" signal that triggers
# `InventoryService.apply_dispatch`. Idempotent per (order, variant), so
# it's safe to keep firing as the shipment advances through later statuses.
_DISPATCHED_STATUSES = (
    ShipmentStatus.PICKED_UP,
    ShipmentStatus.IN_TRANSIT,
    ShipmentStatus.OUT_FOR_DELIVERY,
    ShipmentStatus.DELIVERED,
)

_TERMINAL_STATUSES = (
    ShipmentStatus.DELIVERED,
    ShipmentStatus.CANCELLED,
    ShipmentStatus.RTO_DELIVERED,
)


async def apply_tracking_event(
    session: AsyncSession,
    shipment: Shipment,
    normalized: dict,
    *,
    shipment_service: ShipmentService,
    rto_service: RTOService,
    inventory_service: InventoryService,
    source: str = "shiprocket",
) -> bool:
    """One normalized tracking event (the shape `TRACKING_NORMALIZER.
    normalize_event`/a webhook-flavored equivalent both produce) -> the
    same OMS writes every tracking source needs: append the
    `ShipmentEvent` (idempotent), advance `Shipment.current_status` when
    the event maps to a known status, derive an `RTO` row when that
    status is `RTO_INITIATED`/`RTO_DELIVERED`, and move OMS-tracked stock
    (dispatch decrement / RTO restock) accordingly. Shared by
    `refresh_tracking` (pull) and `app.services.shiprocket_webhook_service`
    (push/webhook) so the ingestion paths can never silently diverge in
    behaviour. Returns True only when a genuinely new `ShipmentEvent` was
    created — a duplicate replay (same external_event_id, or same
    (status, event_timestamp) when no id is available) returns False.
    """
    if normalized["event_timestamp"] is None:
        return False

    _, created = await shipment_service.add_tracking_event(
        shipment.id,
        external_event_id=normalized["external_event_id"],
        status=normalized["status"],
        location=normalized["location"],
        event_timestamp=normalized["event_timestamp"],
        description=normalized["description"],
        courier_name=normalized["courier_name"],
        source=source,
        raw_payload=normalized["raw_payload"],
    )

    mapped_status = normalized["mapped_status"]
    if mapped_status is not None:
        await shipment_service.update_shipment(
            shipment.id, actor=None, current_status=mapped_status
        )

    if mapped_status in _DISPATCHED_STATUSES:
        try:
            await inventory_service.apply_dispatch(
                order_id=shipment.order_id, shipment_id=shipment.id
            )
        except Exception:  # noqa: BLE001 - stock tracking must never block tracking ingestion
            await session.rollback()
            logger.exception(
                "inventory_dispatch_failed", shipment_id=str(shipment.id), source=source
            )

    rto_status = RTO_STATUS_FROM_SHIPMENT_STATUS.get(mapped_status)
    if rto_status is not None:
        # No dedicated RTO listing endpoint was confirmed for Shiprocket
        # (see docs/integrations/shiprocket.md) — RTO records are derived
        # from tracking events instead of a separate, unverified sync path.
        rto, _ = await rto_service.upsert_synced_rto(
            source_system="shiprocket",
            external_id=shipment.awb,
            shipment_id=shipment.id,
            order_id=shipment.order_id,
            courier_id=shipment.courier_id,
            status=rto_status,
            external_reason=normalized["description"],
        )
        if rto_status == RTOStatus.RECEIVED:
            try:
                await inventory_service.apply_rto_restock(
                    order_id=shipment.order_id, rto_id=rto.id
                )
            except Exception:  # noqa: BLE001 - stock tracking must never block tracking ingestion
                await session.rollback()
                logger.exception(
                    "inventory_rto_restock_failed", shipment_id=str(shipment.id), source=source
                )

    return created


async def refresh_tracking(
    session: AsyncSession, sync_job_id: uuid.UUID, adapter: ShiprocketAdapter
) -> None:
    sync_service = SyncService(session)
    shipment_service = ShipmentService(session)
    rto_service = RTOService(session)
    inventory_service = InventoryService(session)

    stmt = select(Shipment).where(
        Shipment.awb.is_not(None), Shipment.current_status.not_in(_TERMINAL_STATUSES)
    )
    shipments = list((await session.execute(stmt)).scalars().all())

    updated_count = 0
    failed_count = 0

    for shipment in shipments:
        try:
            assert shipment.awb is not None  # guaranteed by the query filter above
            raw_response = await adapter.get_tracking(shipment.awb)
            any_new_event = False

            for raw_event in extract_tracking_events(raw_response):
                normalized = TRACKING_NORMALIZER.normalize_event(raw_event)
                created = await apply_tracking_event(
                    session,
                    shipment,
                    normalized,
                    shipment_service=shipment_service,
                    rto_service=rto_service,
                    inventory_service=inventory_service,
                )
                any_new_event = any_new_event or created

            if any_new_event:
                updated_count += 1

        except Exception as exc:  # noqa: BLE001 - one bad shipment must not kill the job
            await session.rollback()
            failed_count += 1
            await sync_service.record_error(
                sync_job_id,
                entity_type="tracking",
                external_id=shipment.awb,
                error_type="validation_error",
                error_message=str(exc),
            )

    await sync_service.record_progress(
        sync_job_id,
        received=len(shipments),
        updated=updated_count,
        failed=failed_count,
    )
