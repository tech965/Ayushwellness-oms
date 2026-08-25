"""Customer CRUD + Customer 360 summary/orders."""

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
from app.schemas.customer import (
    CustomerCreateRequest,
    CustomerResponse,
    CustomerSummaryResponse,
    CustomerUpdateRequest,
)
from app.schemas.order import OrderResponse
from app.schemas.response import ApiResponse, PaginatedResponse
from app.services.customer_service import CustomerService

router = APIRouter()


@router.get("", response_model=PaginatedResponse[CustomerResponse])
async def list_customers(
    q: str | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("customers.read")),
) -> PaginatedResponse[CustomerResponse]:
    items, total = await CustomerService(session).list_customers(
        page_params=page_params, sort_params=sort_params, q=q
    )
    return PaginatedResponse(
        data=[CustomerResponse.model_validate(c) for c in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.post("", response_model=ApiResponse[CustomerResponse], status_code=201)
async def create_customer(
    payload: CustomerCreateRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("customers.update")),
) -> ApiResponse[CustomerResponse]:
    customer = await CustomerService(session).create_customer(**payload.model_dump())
    return ApiResponse(data=CustomerResponse.model_validate(customer), message="Customer created.")


@router.get("/{customer_id}", response_model=ApiResponse[CustomerResponse])
async def get_customer(
    customer_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("customers.read")),
) -> ApiResponse[CustomerResponse]:
    customer = await CustomerService(session).get_customer(customer_id)
    return ApiResponse(data=CustomerResponse.model_validate(customer))


@router.patch("/{customer_id}", response_model=ApiResponse[CustomerResponse])
async def update_customer(
    customer_id: uuid.UUID,
    payload: CustomerUpdateRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("customers.update")),
) -> ApiResponse[CustomerResponse]:
    customer = await CustomerService(session).update_customer(customer_id, **payload.model_dump())
    return ApiResponse(data=CustomerResponse.model_validate(customer), message="Customer updated.")


@router.get("/{customer_id}/summary", response_model=ApiResponse[CustomerSummaryResponse])
async def get_customer_summary(
    customer_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("customers.read")),
) -> ApiResponse[CustomerSummaryResponse]:
    summary = await CustomerService(session).get_summary(customer_id)
    return ApiResponse(data=summary)


@router.get("/{customer_id}/orders", response_model=PaginatedResponse[OrderResponse])
async def get_customer_orders(
    customer_id: uuid.UUID,
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("customers.read")),
) -> PaginatedResponse[OrderResponse]:
    items, total = await CustomerService(session).get_orders_for_customer(
        customer_id, page_params=page_params, sort_params=sort_params
    )
    return PaginatedResponse(
        data=[OrderResponse.model_validate(o) for o in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )
