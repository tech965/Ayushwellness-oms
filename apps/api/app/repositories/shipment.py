from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import or_, select

from app.models.shipment import Shipment, ShipmentEvent
from app.repositories.base import AppendOnlyRepository, BaseRepository


class ShipmentRepository(BaseRepository[Shipment]):
    model = Shipment

    async def get_by_awb(self, awb: str) -> Shipment | None:
        stmt = select(Shipment).where(Shipment.awb == awb)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_order(self, order_id: uuid.UUID) -> list[Shipment]:
        stmt = select(Shipment).where(Shipment.order_id == order_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    def search_query(
        self,
        *,
        q: str | None = None,
        status: str | None = None,
        courier_id: uuid.UUID | None = None,
        order_id: uuid.UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ):
        stmt = self._base_query()
        if q:
            stmt = stmt.where(
                or_(Shipment.awb.ilike(f"%{q}%"), Shipment.shiprocket_shipment_id.ilike(f"%{q}%"))
            )
        if status:
            stmt = stmt.where(Shipment.current_status == status)
        if courier_id:
            stmt = stmt.where(Shipment.courier_id == courier_id)
        if order_id:
            stmt = stmt.where(Shipment.order_id == order_id)
        if date_from:
            stmt = stmt.where(Shipment.expected_delivery_date >= date_from)
        if date_to:
            stmt = stmt.where(Shipment.expected_delivery_date <= date_to)
        return stmt


class ShipmentEventRepository(AppendOnlyRepository[ShipmentEvent]):
    model = ShipmentEvent

    async def list_for_shipment(self, shipment_id: uuid.UUID) -> list[ShipmentEvent]:
        stmt = (
            select(ShipmentEvent)
            .where(ShipmentEvent.shipment_id == shipment_id)
            .order_by(ShipmentEvent.event_timestamp.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def find_duplicate(
        self,
        *,
        shipment_id: uuid.UUID,
        external_event_id: str | None,
        status: str,
        event_timestamp: datetime,
    ) -> ShipmentEvent | None:
        if external_event_id:
            stmt = select(ShipmentEvent).where(
                ShipmentEvent.shipment_id == shipment_id,
                ShipmentEvent.external_event_id == external_event_id,
            )
        else:
            stmt = select(ShipmentEvent).where(
                ShipmentEvent.shipment_id == shipment_id,
                ShipmentEvent.status == status,
                ShipmentEvent.event_timestamp == event_timestamp,
            )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
