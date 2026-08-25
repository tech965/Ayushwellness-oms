"""RTO foundation — read + status update. Creation is Phase 2 sync work."""

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
from app.schemas.response import ApiResponse, PaginatedResponse
from app.schemas.rto import RTOResponse, RTOUpdateRequest
from app.services.rto_service import RTOService

router = APIRouter()


@router.get("", response_model=PaginatedResponse[RTOResponse])
async def list_rtos(
    status: str | None = Query(default=None),
    courier_id: uuid.UUID | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("rto.read")),
) -> PaginatedResponse[RTOResponse]:
    items, total = await RTOService(session).list_rtos(
        page_params=page_params, sort_params=sort_params, status=status, courier_id=courier_id
    )
    return PaginatedResponse(
        data=[RTOResponse.model_validate(r) for r in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.get("/{rto_id}", response_model=ApiResponse[RTOResponse])
async def get_rto(
    rto_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("rto.read")),
) -> ApiResponse[RTOResponse]:
    rto = await RTOService(session).get_rto(rto_id)
    return ApiResponse(data=RTOResponse.model_validate(rto))


@router.patch("/{rto_id}", response_model=ApiResponse[RTOResponse])
async def update_rto(
    rto_id: uuid.UUID,
    payload: RTOUpdateRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("rto.update")),
) -> ApiResponse[RTOResponse]:
    rto = await RTOService(session).update_rto(rto_id, actor=current_user, **payload.model_dump())
    return ApiResponse(data=RTOResponse.model_validate(rto), message="RTO updated.")
