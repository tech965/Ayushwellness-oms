from __future__ import annotations

import uuid

from sqlalchemy import Select, select

from app.models.integration import SyncError
from app.repositories.base import BaseRepository


class SyncErrorRepository(BaseRepository[SyncError]):
    model = SyncError

    def for_sync_job(self, sync_job_id: uuid.UUID) -> Select:
        return select(SyncError).where(SyncError.sync_job_id == sync_job_id)

    async def unresolved_for_integration(self, integration_id: uuid.UUID) -> list[SyncError]:
        stmt = select(SyncError).where(
            SyncError.integration_id == integration_id, SyncError.resolved.is_(False)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
