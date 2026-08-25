"""Product CRUD."""

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
from app.schemas.product import (
    ProductCreateRequest,
    ProductDetailResponse,
    ProductResponse,
    ProductUpdateRequest,
)
from app.schemas.response import ApiResponse, PaginatedResponse
from app.services.product_service import ProductService

router = APIRouter()


@router.get("", response_model=PaginatedResponse[ProductResponse])
async def list_products(
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("products.read")),
) -> PaginatedResponse[ProductResponse]:
    items, total = await ProductService(session).list_products(
        page_params=page_params, sort_params=sort_params, q=q, status=status
    )
    return PaginatedResponse(
        data=[ProductResponse.model_validate(p) for p in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.post("", response_model=ApiResponse[ProductResponse], status_code=201)
async def create_product(
    payload: ProductCreateRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("products.update")),
) -> ApiResponse[ProductResponse]:
    product = await ProductService(session).create_product(**payload.model_dump())
    return ApiResponse(data=ProductResponse.model_validate(product), message="Product created.")


@router.get("/{product_id}", response_model=ApiResponse[ProductDetailResponse])
async def get_product(
    product_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("products.read")),
) -> ApiResponse[ProductDetailResponse]:
    product = await ProductService(session).get_product(product_id)
    return ApiResponse(data=ProductDetailResponse.model_validate(product))


@router.patch("/{product_id}", response_model=ApiResponse[ProductResponse])
async def update_product(
    product_id: uuid.UUID,
    payload: ProductUpdateRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("products.update")),
) -> ApiResponse[ProductResponse]:
    product = await ProductService(session).update_product(product_id, **payload.model_dump())
    return ApiResponse(data=ProductResponse.model_validate(product), message="Product updated.")
