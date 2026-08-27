from __future__ import annotations

import uuid

from sqlalchemy import Select, select

from app.models.enums import SyncJobStatus
from app.models.integration import SyncJob
from app.repositories.base import BaseRepository


class SyncJobRepository(BaseRepository[SyncJob]):
    model = SyncJob

    def for_integration(self, integration_id: uuid.UUID) -> Select:
        return select(SyncJob).where(SyncJob.integration_id == integration_id)

    async def get_last_successful_for_entity(
        self, *, integration_id: uuid.UUID, entity_type: str
    ) -> SyncJob | None:
        """Most recent *completed* (COMPLETED or PARTIAL — a partial sync
        still legitimately advanced incrementally as far as it got) sync
        job for this exact `entity_type`. Used to derive that entity
        type's own incremental `since` boundary — deliberately NOT
        `Integration.last_successful_sync_at`, which is a single
        timestamp shared across every entity type an integration syncs.
        See `SyncService.execute_sync`'s docstring for why that shared
        timestamp starves every entity type after the first one in a
        multi-entity sync run (e.g. a scheduled orders -> customers ->
        products cycle for Shopify): the moment the first entity type's
        job completes, it bumps the *integration's* timestamp, so the
        next entity type's sync — even though it has never itself
        synced before — sees a `since` of "a few seconds ago" instead of
        "never," and incorrectly fetches almost nothing.
        """
        stmt = (
            select(SyncJob)
            .where(
                SyncJob.integration_id == integration_id,
                SyncJob.entity_type == entity_type,
                SyncJob.status.in_([SyncJobStatus.COMPLETED, SyncJobStatus.PARTIAL]),
                SyncJob.completed_at.is_not(None),
            )
            .order_by(SyncJob.completed_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
