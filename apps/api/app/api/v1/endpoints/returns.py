"""Return CRUD. Completing a return creates a linked Refund — see
`app.services.return_service`.
"""

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
from app.models.returns import Return
from app.schemas.common import PageParams, SortParams, build_pagination_meta
from app.schemas.response import ApiResponse, PaginatedResponse
from app.schemas.returns import (
    ReturnCreateRequest,
    ReturnListResponse,
    ReturnResponse,
    ReturnUpdateRequest,
)
from app.services.return_service import ReturnService

router = APIRouter()


def _return_filters(
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    payment_type: str | None = Query(default=None),
    customer_id: uuid.UUID | None = Query(default=None),
    order_id: uuid.UUID | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
) -> dict:
    """Shared filter set for `list_returns` — same shape/purpose as
    Orders' `_order_filters` (app/api/v1/endpoints/orders.py). `order_id`
    is what `useReturnsForOrder` (frontend) relies on — left unchanged.
    """
    return {
        "q": q,
        "status": status,
        "payment_type": payment_type,
        "customer_id": customer_id,
        "order_id": order_id,
        "date_from": date_from,
        "date_to": date_to,
    }


def _to_list_response(return_: Return) -> ReturnListResponse:
    """Denormalizes the Returns-table columns from the relationships
    `ReturnRepository.search_query` already eager-loads — see the
    identical convention in app/api/v1/endpoints/ndr.py. `product`
    prefers the specific returned `order_item` (when the return targets
    one) over the order's overall item summary, since a return is
    usually about one particular line item.
    """
    order = return_.order
    product: str | None = None
    if return_.order_item is not None:
        product = return_.order_item.product_name
    elif order and order.items:
        items = order.items
        first = items[0].product_name
        product = first if len(items) == 1 else f"{first} +{len(items) - 1} more"

    return ReturnListResponse(
        **ReturnResponse.model_validate(return_).model_dump(),
        order_number=order.order_number if order else None,
        customer_name=order.customer.full_name if order and order.customer else None,
        customer_phone=order.customer.phone if order and order.customer else None,
        product=product,
        order_amount=order.total_amount if order else None,
        payment_type=order.payment_type.value if order else None,
    )


@router.get("", response_model=PaginatedResponse[ReturnListResponse])
async def list_returns(
    filters: dict = Depends(_return_filters),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("returns.read")),
) -> PaginatedResponse[ReturnListResponse]:
    items, total = await ReturnService(session).list_returns(
        page_params=page_params, sort_params=sort_params, **filters
    )
    return PaginatedResponse(
        data=[_to_list_response(r) for r in items],
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
