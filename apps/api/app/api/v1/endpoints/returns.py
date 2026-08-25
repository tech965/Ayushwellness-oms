"""Return CRUD. Completing a return creates a linked Refund — see
`app.services.return_service`.
"""

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
from app.schemas.returns import ReturnCreateRequest, ReturnResponse, ReturnUpdateRequest
from app.services.return_service import ReturnService

router = APIRouter()


@router.get("", response_model=PaginatedResponse[ReturnResponse])
async def list_returns(
    status: str | None = Query(default=None),
    customer_id: uuid.UUID | None = Query(default=None),
    order_id: uuid.UUID | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("returns.read")),
) -> PaginatedResponse[ReturnResponse]:
    items, total = await ReturnService(session).list_returns(
        page_params=page_params,
        sort_params=sort_params,
        status=status,
        customer_id=customer_id,
        order_id=order_id,
    )
    return PaginatedResponse(
        data=[ReturnResponse.model_validate(r) for r in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.post("", response_model=ApiResponse[ReturnResponse], status_code=201)
async def create_return(
    payload: ReturnCreateRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("returns.update")),
) -> ApiResponse[ReturnResponse]:
    return_ = await ReturnService(session).create_return(actor=current_user, **payload.model_dump())
    return ApiResponse(data=ReturnResponse.model_validate(return_), message="Return created.")


@router.get("/{return_id}", response_model=ApiResponse[ReturnResponse])
async def get_return(
    return_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("returns.read")),
) -> ApiResponse[ReturnResponse]:
    return_ = await ReturnService(session).get_return(return_id)
    return ApiResponse(data=ReturnResponse.model_validate(return_))


@router.patch("/{return_id}", response_model=ApiResponse[ReturnResponse])
async def update_return(
    return_id: uuid.UUID,
    payload: ReturnUpdateRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("returns.update")),
) -> ApiResponse[ReturnResponse]:
    return_ = await ReturnService(session).update_return(
        return_id, actor=current_user, status=payload.status, notes=payload.notes
    )
    return ApiResponse(data=ReturnResponse.model_validate(return_), message="Return updated.")
