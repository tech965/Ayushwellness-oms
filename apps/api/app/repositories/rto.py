from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.rto import RTO
from app.repositories.base import BaseRepository


class RTORepository(BaseRepository[RTO]):
    model = RTO

    def search_query(self, *, status: str | None = None, courier_id: uuid.UUID | None = None):
        stmt = self._base_query()
        if status:
            stmt = stmt.where(RTO.status == status)
        if courier_id:
            stmt = stmt.where(RTO.courier_id == courier_id)
        return stmt

    async def list_for_shipment(self, shipment_id: uuid.UUID) -> list[RTO]:
        stmt = select(RTO).where(RTO.shipment_id == shipment_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
