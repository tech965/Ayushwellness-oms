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
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.integrations.shiprocket.normalizer import TRACKING_NORMALIZER, extract_tracking_events
from app.models.enums import RTOStatus, ShipmentStatus
from app.models.shipment import Shipment
from app.repositories.courier import CourierRepository
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


def milestone_timestamp_updates(
    shipment: Shipment,
    *,
    mapped_status: ShipmentStatus | None,
    event_timestamp,
    expected_delivery_date=None,
) -> dict:
    """`pickup_date`/`actual_delivery_date`/`expected_delivery_date` are
    "first value wins, never overwritten" milestones -- a duplicate
    replay of an already-processed event, or a genuinely new event that
    happens to arrive out of chronological order (e.g. a delayed pickup
    scan reaching the OMS after a later in-transit/delivered update
    already did), must never move or erase a timestamp this shipment
    already has. Checking the *current* value on `shipment` (not
    `created`/some separate "is this a duplicate" flag) makes both cases
    safe with one guard: if it's already set, this call contributes
    nothing, regardless of why it was called again.

    `pickup_date` is set on the first event in `_DISPATCHED_STATUSES`
    (not only literal `PICKED_UP`) so a shipment whose first-ever ingested
    event is already IN_TRANSIT/OUT_FOR_DELIVERY/DELIVERED (a missed
    pickup scan) still gets a pickup timestamp, reusing the exact set
    `InventoryService.apply_dispatch` already keys its own "has this
    shipment been dispatched" decision on -- never a second, separately
    reasoned definition of "pickup happened."

    Returns a dict suitable for `ShipmentService.update_shipment(**updates)`
    -- empty when nothing newly qualifies. Never raises.
    """
    updates: dict = {}
    if (
        shipment.pickup_date is None
        and mapped_status is not None
        and mapped_status in _DISPATCHED_STATUSES
    ):
        updates["pickup_date"] = event_timestamp
    if (
        shipment.actual_delivery_date is None
        and mapped_status is not None
        and mapped_status == ShipmentStatus.DELIVERED
    ):
        updates["actual_delivery_date"] = event_timestamp
    if shipment.expected_delivery_date is None and expected_delivery_date is not None:
        updates["expected_delivery_date"] = expected_delivery_date
    return updates


async def apply_tracking_event(
    session: AsyncSession,
    shipment: Shipment,
    normalized: dict,
    *,
    shipment_service: ShipmentService,
    rto_service: RTOService,
    inventory_service: InventoryService,
    source: str = "shiprocket",
    source_system: str = "shiprocket",
) -> bool:
    """One normalized tracking event (the shape `TRACKING_NORMALIZER.
    normalize_event`/a webhook-flavored equivalent both produce) -> the
    same OMS writes every tracking source needs: append the
    `ShipmentEvent` (idempotent), advance `Shipment.current_status` when
    the event maps to a known status, derive an `RTO` row when that
    status is `RTO_INITIATED`/`RTO_DELIVERED`, and move OMS-tracked stock
    (dispatch decrement / RTO restock) accordingly. Shared by
    `refresh_tracking` (pull), `app.services.shiprocket_webhook_service`
    (push/webhook) AND `app.services.nimbuspost_webhook_service` (Module
    5) so every ingestion path, for every courier, can never silently
    diverge in behaviour. Returns True only when a genuinely new
    `ShipmentEvent` was created — a duplicate replay (same
    external_event_id, or same (status, event_timestamp) when no id is
    available) returns False.

    `source_system` (default `"shiprocket"`, unchanged for every
    existing call site) is the `RTO.source_system`/`external_id` key
    `process_courier_rto_status` upserts under -- it must match the
    courier that actually produced this event, not be hardcoded, so a
    NimbusPost-derived RTO is never recorded as if it were Shiprocket's.
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
        milestone_updates = milestone_timestamp_updates(
            shipment,
            mapped_status=mapped_status,
            event_timestamp=normalized["event_timestamp"],
            expected_delivery_date=normalized.get("expected_delivery_date"),
        )
        await shipment_service.update_shipment(
            shipment.id, actor=None, current_status=mapped_status, **milestone_updates
        )

    # Copy the real event times onto the denormalised columns analytics
    # reads. Only ever the courier's own event timestamp -- never "now".
    # Pickup keeps the EARLIEST pickup scan; delivery the first DELIVERED.
    event_ts = normalized["event_timestamp"]
    stamps: dict[str, datetime] = {}
    if mapped_status == ShipmentStatus.PICKED_UP and (
        shipment.pickup_date is None or event_ts < shipment.pickup_date
    ):
        stamps["pickup_date"] = event_ts
    if mapped_status == ShipmentStatus.DELIVERED and shipment.actual_delivery_date is None:
        stamps["actual_delivery_date"] = event_ts
    if shipment.courier_id is None and normalized.get("courier_name"):
        courier = await CourierRepository(session).get_by_name_ci(normalized["courier_name"])
        if courier is not None:
            stamps["courier_id"] = courier.id
    if stamps:
        await shipment_service.update_shipment(shipment.id, actor=None, **stamps)

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
        # `process_courier_rto_status` is the ONE place every ingestion
        # path (this method -- shared by webhook + scheduled pull-sync --
        # and `ShiprocketService.refresh_tracking_for_shipment`'s manual
        # refresh) derives RTO state (Module 1 requirement 2); it never
        # restocks directly off a courier status (Module 1 requirement 1
        # — see `RTOService.verify_warehouse_receipt`), so no
        # `apply_rto_restock` call belongs here any more.
        try:
            await rto_service.process_courier_rto_status(
                source_system=source_system,
                external_id=shipment.awb,
                shipment_id=shipment.id,
                order_id=shipment.order_id,
                courier_id=shipment.courier_id,
                status=rto_status,
                external_reason=normalized["description"],
            )
        except Exception:  # noqa: BLE001 - stock tracking must never block tracking ingestion
            await session.rollback()
            logger.exception(
                "rto_status_processing_failed", shipment_id=str(shipment.id), source=source
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
