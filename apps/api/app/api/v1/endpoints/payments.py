"""Read-only — see `app.services.payment_service` docstring."""

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
from app.schemas.payment import PaymentDetailResponse, PaymentResponse
from app.schemas.response import ApiResponse, PaginatedResponse
from app.services.payment_service import PaymentService

router = APIRouter()


@router.get("", response_model=PaginatedResponse[PaymentResponse])
async def list_payments(
    order_id: uuid.UUID | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("payments.read")),
) -> PaginatedResponse[PaymentResponse]:
    items, total = await PaymentService(session).list_payments(
        page_params=page_params, sort_params=sort_params, order_id=order_id
    )
    return PaginatedResponse(
        data=[PaymentResponse.model_validate(p) for p in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.get("/{payment_id}", response_model=ApiResponse[PaymentDetailResponse])
async def get_payment(
    payment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("payments.read")),
) -> ApiResponse[PaymentDetailResponse]:
    payment = await PaymentService(session).get_payment(payment_id)
    return ApiResponse(data=PaymentDetailResponse.model_validate(payment))
