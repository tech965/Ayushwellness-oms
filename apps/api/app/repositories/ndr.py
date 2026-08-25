from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.ndr import NDR
from app.repositories.base import BaseRepository


class NDRRepository(BaseRepository[NDR]):
    model = NDR

    def search_query(self, *, status: str | None = None, courier_id: uuid.UUID | None = None):
        stmt = self._base_query()
        if status:
            stmt = stmt.where(NDR.status == status)
        if courier_id:
            stmt = stmt.where(NDR.courier_id == courier_id)
        return stmt

    async def list_for_shipment(self, shipment_id: uuid.UUID) -> list[NDR]:
        stmt = select(NDR).where(NDR.shipment_id == shipment_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
