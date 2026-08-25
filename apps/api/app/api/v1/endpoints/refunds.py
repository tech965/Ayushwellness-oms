"""Read-only — see `app.services.return_service` for the creation path."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.dependencies.pagination import pagination_params
from app.dependencies.pagination import sort_params as sort_params_dep
from app.models.auth import User
from app.schemas.common import PageParams, SortParams, build_pagination_meta
from app.schemas.refund import RefundResponse
from app.schemas.response import ApiResponse, PaginatedResponse
from app.services.refund_service import RefundService

router = APIRouter()


@router.get("", response_model=PaginatedResponse[RefundResponse])
async def list_refunds(
    status: str | None = Query(default=None),
    order_id: uuid.UUID | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("refunds.read")),
) -> PaginatedResponse[RefundResponse]:
    items, total = await RefundService(session).list_refunds(
        page_params=page_params, sort_params=sort_params, status=status, order_id=order_id
    )
    return PaginatedResponse(
        data=[RefundResponse.model_validate(r) for r in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.get("/{refund_id}", response_model=ApiResponse[RefundResponse])
async def get_refund(
    refund_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("refunds.read")),
) -> ApiResponse[RefundResponse]:
    refund = await RefundService(session).get_refund(refund_id)
    return ApiResponse(data=RefundResponse.model_validate(refund))
