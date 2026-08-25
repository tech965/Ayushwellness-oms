from __future__ import annotations

from sqlalchemy import select

from app.models.integration import Integration
from app.repositories.base import BaseRepository


class IntegrationRepository(BaseRepository[Integration]):
    model = Integration

    async def get_by_code(self, code: str) -> Integration | None:
        stmt = select(Integration).where(Integration.code == code)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
