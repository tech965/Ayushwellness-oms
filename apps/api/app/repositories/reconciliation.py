from __future__ import annotations

import uuid

from sqlalchemy import Select, select

from app.models.reconciliation import ReconciliationResult, ReconciliationRun
from app.repositories.base import BaseRepository


class ReconciliationRunRepository(BaseRepository[ReconciliationRun]):
    model = ReconciliationRun


class ReconciliationResultRepository(BaseRepository[ReconciliationResult]):
    model = ReconciliationResult

    def for_run(self, run_id: uuid.UUID) -> Select:
        return select(ReconciliationResult).where(ReconciliationResult.run_id == run_id)

    def search_query(
        self,
        *,
        run_id: uuid.UUID | None = None,
        status: str | None = None,
        check_type: str | None = None,
        provider: str | None = None,
        resolved: bool | None = None,
    ) -> Select:
        stmt = select(ReconciliationResult)
        if run_id is not None:
            stmt = stmt.where(ReconciliationResult.run_id == run_id)
        if status is not None:
            stmt = stmt.where(ReconciliationResult.status == status)
        if check_type is not None:
            stmt = stmt.where(ReconciliationResult.check_type == check_type)
        if provider is not None:
            stmt = stmt.where(ReconciliationResult.provider == provider)
        if resolved is not None:
            stmt = stmt.where(ReconciliationResult.resolved.is_(resolved))
        return stmt
