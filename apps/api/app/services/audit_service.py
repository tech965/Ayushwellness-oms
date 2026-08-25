"""Writes and reads for the OMS-owned AuditLog.

`record()` is called explicitly from the mutation points spec'd in
`docs/security/audit-logs.md` (order create/update/status-change, courier
change, shipment update, NDR create, RTO status change, customer update)
— not wrapped indiscriminately around every service call. Never pass a
password, token, or secret in `previous_value`/`new_value`/`metadata`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.auth import User
from app.repositories.audit_log import AuditLogRepository
from app.schemas.common import PageParams, SortParams


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = AuditLogRepository(session)

    async def record(
        self,
        *,
        user: User | None,
        action: str,
        entity_type: str,
        entity_id: str,
        previous_value: dict[str, Any] | None = None,
        new_value: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditLog:
        return await self.repo.create(
            user_id=user.id if user else None,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            previous_value=previous_value,
            new_value=new_value,
            ip_address=ip_address,
            user_agent=user_agent,
            audit_metadata=metadata,
        )

    async def list_logs(
        self,
        *,
        page_params: PageParams,
        sort_params: SortParams,
        entity_type: str | None = None,
        entity_id: str | None = None,
        user_id: uuid.UUID | None = None,
        action: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[AuditLog], int]:
        query = self.repo.search_query(
            entity_type=entity_type,
            entity_id=entity_id,
            user_id=user_id,
            action=action,
            date_from=date_from,
            date_to=date_to,
        )
        items, total = await self.repo.list(
            page_params=page_params, sort_params=sort_params, query=query
        )
        return list(items), total
