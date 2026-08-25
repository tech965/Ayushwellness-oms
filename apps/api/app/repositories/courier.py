from __future__ import annotations

from sqlalchemy import select

from app.models.courier import Courier
from app.repositories.base import BaseRepository


class CourierRepository(BaseRepository[Courier]):
    model = Courier

    async def get_by_code(self, code: str) -> Courier | None:
        stmt = select(Courier).where(Courier.code == code)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
