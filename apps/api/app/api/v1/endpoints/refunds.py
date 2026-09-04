"""Read-only — see `app.services.return_service` for the creation path."""

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
from app.models.refund import Refund
from app.schemas.common import PageParams, SortParams, build_pagination_meta
from app.schemas.refund import RefundListResponse, RefundResponse
from app.schemas.response import ApiResponse, PaginatedResponse
from app.services.refund_service import RefundService

router = APIRouter()


def _refund_filters(
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    payment_type: str | None = Query(default=None),
    order_id: uuid.UUID | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
) -> dict:
    """Shared filter set for `list_refunds` — same shape/purpose as
    Orders' `_order_filters` (app/api/v1/endpoints/orders.py). `order_id`
    is what `useRefundsForOrder` (frontend) relies on — left unchanged.
    """
    return {
        "q": q,
        "status": status,
        "payment_type": payment_type,
        "order_id": order_id,
        "date_from": date_from,
        "date_to": date_to,
    }


def _to_list_response(refund: Refund) -> RefundListResponse:
    """Denormalizes the Refunds-table columns from the relationships
    `RefundRepository.search_query` already eager-loads — see the
    identical convention in app/api/v1/endpoints/ndr.py. `product`
    prefers the linked return's specific order item when present,
    matching `returns.py`'s own `_to_list_response`.
    """
    order = refund.order
    return_ = refund.return_
    product: str | None = None
    if return_ is not None and return_.order_item is not None:
        product = return_.order_item.product_name
    elif order and order.items:
        items = order.items
        first = items[0].product_name
        product = first if len(items) == 1 else f"{first} +{len(items) - 1} more"

    return RefundListResponse(
        **RefundResponse.model_validate(refund).model_dump(),
        order_number=order.order_number if order else None,
        customer_name=order.customer.full_name if order and order.customer else None,
        customer_phone=order.customer.phone if order and order.customer else None,
        product=product,
        order_amount=order.total_amount if order else None,
        payment_type=order.payment_type.value if order else None,
    )


@router.get("", response_model=PaginatedResponse[RefundListResponse])
async def list_refunds(
    filters: dict = Depends(_refund_filters),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("refunds.read")),
) -> PaginatedResponse[RefundListResponse]:
    items, total = await RefundService(session).list_refunds(
        page_params=page_params, sort_params=sort_params, **filters
    )
    return PaginatedResponse(
        data=[_to_list_response(r) for r in items],
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
