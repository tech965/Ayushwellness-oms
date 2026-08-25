from __future__ import annotations

import uuid

from sqlalchemy import Select, select

from app.models.integration import SyncJob
from app.repositories.base import BaseRepository


class SyncJobRepository(BaseRepository[SyncJob]):
    model = SyncJob

    def for_integration(self, integration_id: uuid.UUID) -> Select:
        return select(SyncJob).where(SyncJob.integration_id == integration_id)
