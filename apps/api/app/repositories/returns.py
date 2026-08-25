from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.returns import Return
from app.repositories.base import BaseRepository


class ReturnRepository(BaseRepository[Return]):
    model = Return

    def search_query(
        self,
        *,
        status: str | None = None,
        customer_id: uuid.UUID | None = None,
        order_id: uuid.UUID | None = None,
    ):
        stmt = self._base_query()
        if status:
            stmt = stmt.where(Return.status == status)
        if customer_id:
            stmt = stmt.where(Return.customer_id == customer_id)
        if order_id:
            stmt = stmt.where(Return.order_id == order_id)
        return stmt

    async def list_for_order(self, order_id: uuid.UUID) -> list[Return]:
        stmt = select(Return).where(Return.order_id == order_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
