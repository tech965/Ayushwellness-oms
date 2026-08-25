from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.refund import Refund
from app.repositories.base import BaseRepository


class RefundRepository(BaseRepository[Refund]):
    model = Refund

    def search_query(self, *, status: str | None = None, order_id: uuid.UUID | None = None):
        stmt = self._base_query()
        if status:
            stmt = stmt.where(Refund.status == status)
        if order_id:
            stmt = stmt.where(Refund.order_id == order_id)
        return stmt

    async def list_for_order(self, order_id: uuid.UUID) -> list[Refund]:
        stmt = select(Refund).where(Refund.order_id == order_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_return_id(self, return_id: uuid.UUID) -> Refund | None:
        stmt = select(Refund).where(Refund.return_id == return_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
