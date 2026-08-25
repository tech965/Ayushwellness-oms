"""Audit log read access. Pagination + entity/user/action/date filtering."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.dependencies.pagination import pagination_params
from app.dependencies.pagination import sort_params as sort_params_dep
from app.models.auth import User
from app.schemas.audit_log import AuditLogResponse
from app.schemas.common import PageParams, SortParams, build_pagination_meta
from app.schemas.response import PaginatedResponse
from app.services.audit_service import AuditService

router = APIRouter()


@router.get("", response_model=PaginatedResponse[AuditLogResponse])
async def list_audit_logs(
    entity_type: str | None = Query(default=None),
    entity_id: str | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("audit_logs.read")),
) -> PaginatedResponse[AuditLogResponse]:
    items, total = await AuditService(session).list_logs(
        page_params=page_params,
        sort_params=sort_params,
        entity_type=entity_type,
        entity_id=entity_id,
        user_id=user_id,
        action=action,
        date_from=date_from,
        date_to=date_to,
    )
    return PaginatedResponse(
        data=[AuditLogResponse.model_validate(a) for a in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )
