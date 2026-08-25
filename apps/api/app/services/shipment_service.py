"""Shipment current-state + append-only tracking history.

`add_tracking_event` NEVER updates or deletes a prior `ShipmentEvent` — it
only appends, then refreshes `Shipment`'s current-state columns from the
new event. Dedup: by `external_event_id` when the source provides a
stable one, else by `(status, event_timestamp)` — see spec §22/§57.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.auth import User
from app.models.enums import ShipmentStatus
from app.models.shipment import Shipment, ShipmentEvent
from app.repositories.shipment import ShipmentEventRepository, ShipmentRepository
from app.schemas.common import PageParams, SortParams
from app.services.audit_service import AuditService


class ShipmentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.shipments = ShipmentRepository(session)
        self.shipment_events = ShipmentEventRepository(session)
        self.audit = AuditService(session)

    async def list_shipments(
        self,
        *,
        page_params: PageParams,
        sort_params: SortParams,
        q: str | None = None,
        status: str | None = None,
        courier_id: uuid.UUID | None = None,
        order_id: uuid.UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[Shipment], int]:
        query = self.shipments.search_query(
            q=q,
            status=status,
            courier_id=courier_id,
            order_id=order_id,
            date_from=date_from,
            date_to=date_to,
        )
        items, total = await self.shipments.list(
            page_params=page_params, sort_params=sort_params, query=query
        )
        return list(items), total

    async def get_shipment(self, shipment_id: uuid.UUID) -> Shipment:
        shipment = await self.shipments.get_by_id(shipment_id)
        if shipment is None:
            raise NotFoundError("Shipment not found.")
        return shipment

    async def get_timeline(self, shipment_id: uuid.UUID) -> list[ShipmentEvent]:
        await self.get_shipment(shipment_id)
        return await self.shipment_events.list_for_shipment(shipment_id)

    async def create_shipment(
        self,
        *,
        actor: User | None,
        order_id: uuid.UUID,
        awb: str | None,
        courier_id: uuid.UUID | None,
        expected_delivery_date: datetime | None,
    ) -> Shipment:
        shipment = await self.shipments.create(
            order_id=order_id,
            awb=awb,
            courier_id=courier_id,
            expected_delivery_date=expected_delivery_date,
            source_system="manual",
        )
        await self.audit.record(
            user=actor,
            action="shipment.created",
            entity_type="shipment",
            entity_id=str(shipment.id),
            new_value={"order_id": str(order_id), "awb": awb},
        )
        await self.session.commit()
        return shipment

    async def update_shipment(
        self, shipment_id: uuid.UUID, *, actor: User | None, **fields
    ) -> Shipment:  # noqa: ANN003
        shipment = await self.get_shipment(shipment_id)
        clean = {k: v for k, v in fields.items() if v is not None}
        previous = {k: str(getattr(shipment, k)) for k in clean}
        if clean:
            await self.shipments.update(shipment, **clean)
            await self.audit.record(
                user=actor,
                action="shipment.updated",
                entity_type="shipment",
                entity_id=str(shipment.id),
                previous_value=previous,
                new_value={k: str(v) for k, v in clean.items()},
            )
        await self.session.commit()
        return shipment

    async def add_tracking_event(
        self,
        shipment_id: uuid.UUID,
        *,
        external_event_id: str | None,
        status: str,
        location: str | None,
        event_timestamp: datetime,
        description: str | None,
        courier_name: str | None,
        source: str = "manual",
        raw_payload: dict | None = None,
    ) -> tuple[ShipmentEvent, bool]:
        """Returns (event, created) — `created=False` means this exact
        event was already recorded and the existing row was returned
        untouched (idempotent replay, e.g. a webhook retry).
        """
        shipment = await self.get_shipment(shipment_id)

        duplicate = await self.shipment_events.find_duplicate(
            shipment_id=shipment_id,
            external_event_id=external_event_id,
            status=status,
            event_timestamp=event_timestamp,
        )
        if duplicate is not None:
            return duplicate, False

        event = await self.shipment_events.create(
            shipment_id=shipment_id,
            external_event_id=external_event_id,
            status=status,
            location=location,
            event_timestamp=event_timestamp,
            description=description,
            courier_name=courier_name,
            source=source,
            raw_payload=raw_payload,
        )

        await self.shipments.update(
            shipment,
            current_location=location or shipment.current_location,
            last_tracking_update_at=event_timestamp,
        )

        await self.session.commit()
        return event, True

    async def upsert_synced_shipment(
        self,
        *,
        source_system: str,
        external_id: str,
        order_id: uuid.UUID,
        shiprocket_shipment_id: str | None = None,
        awb: str | None = None,
        courier_id: uuid.UUID | None = None,
        current_status: ShipmentStatus | None = None,
        raw_external_payload: dict | None = None,
    ) -> tuple[Shipment, bool]:
        """Idempotent create-or-update keyed by `(source_system, external_id)`
        — used both when a Shiprocket order/shipment is first created (the
        push flow, `ShiprocketOperationsService.create_shipment_for_order`)
        and by any future pull-based shipment sync.
        """
        optional = {
            k: v
            for k, v in {
                "shiprocket_shipment_id": shiprocket_shipment_id,
                "awb": awb,
                "courier_id": courier_id,
                "current_status": current_status,
                "raw_external_payload": raw_external_payload,
            }.items()
            if v is not None
        }
        shipment, created = await self.shipments.upsert_by_external_id(
            source_system=source_system, external_id=external_id, order_id=order_id, **optional
        )
        await self.session.commit()
        return shipment, created
