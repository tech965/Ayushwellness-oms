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

    async def get_latest_for_entity_and_external_id(
        self, *, entity_type: str, external_id: str
    ) -> SyncError | None:
        """The most recent recorded failure for one specific record
        (matched by `entity_type` + `external_id`, e.g. a Shiprocket
        shipment id) — used by `entity_sync._upsert_shipment` to tell
        "we already checked this and it genuinely doesn't match" (safe to
        skip re-checking) apart from "we couldn't even check last time"
        (a permission/network failure — must always be retried, since
        that condition can and does change, unlike a genuine non-match).
        The caller distinguishes the two via `error_type`, not by
        assuming presence alone means "confirmed."
        """
        stmt = (
            select(SyncError)
            .where(SyncError.entity_type == entity_type, SyncError.external_id == external_id)
            .order_by(SyncError.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
