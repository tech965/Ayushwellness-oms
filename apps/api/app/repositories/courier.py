from __future__ import annotations

from sqlalchemy import func, select

from app.models.courier import Courier
from app.repositories.base import BaseRepository


class CourierRepository(BaseRepository[Courier]):
    model = Courier

    async def get_by_code(self, code: str) -> Courier | None:
        stmt = select(Courier).where(Courier.code == code)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_name_ci(self, name: str) -> Courier | None:
        """Case/whitespace-insensitive exact name match; `None` unless
        exactly one courier has that name (never guesses between two).
        """
        normalized = " ".join(name.split()).lower()
        if not normalized:
            return None
        stmt = select(Courier).where(func.lower(func.trim(Courier.name)) == normalized).limit(2)
        rows = list((await self.session.execute(stmt)).scalars().all())
        return rows[0] if len(rows) == 1 else None
