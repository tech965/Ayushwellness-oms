from __future__ import annotations

import uuid
from datetime import datetime

from app.models.audit_log import AuditLog
from app.repositories.base import AppendOnlyRepository


class AuditLogRepository(AppendOnlyRepository[AuditLog]):
    model = AuditLog

    def search_query(
        self,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        user_id: uuid.UUID | None = None,
        action: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ):
        stmt = self._base_query()
        if entity_type:
            stmt = stmt.where(AuditLog.entity_type == entity_type)
        if entity_id:
            stmt = stmt.where(AuditLog.entity_id == entity_id)
        if user_id:
            stmt = stmt.where(AuditLog.user_id == user_id)
        if action:
            stmt = stmt.where(AuditLog.action == action)
        if date_from:
            stmt = stmt.where(AuditLog.created_at >= date_from)
        if date_to:
            stmt = stmt.where(AuditLog.created_at <= date_to)
        return stmt
