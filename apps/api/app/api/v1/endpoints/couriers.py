"""Courier CRUD — database records only, no live courier API (Phase 1)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.dependencies.pagination import pagination_params
from app.dependencies.pagination import sort_params as sort_params_dep
from app.models.auth import User
from app.schemas.common import PageParams, SortParams, build_pagination_meta
from app.schemas.courier import CourierCreateRequest, CourierResponse, CourierUpdateRequest
from app.schemas.response import ApiResponse, PaginatedResponse
from app.services.courier_service import CourierService

router = APIRouter()


@router.get("", response_model=PaginatedResponse[CourierResponse])
async def list_couriers(
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("couriers.read")),
) -> PaginatedResponse[CourierResponse]:
    items, total = await CourierService(session).list_couriers(
        page_params=page_params, sort_params=sort_params
    )
    return PaginatedResponse(
        data=[CourierResponse.model_validate(c) for c in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.post("", response_model=ApiResponse[CourierResponse], status_code=201)
async def create_courier(
    payload: CourierCreateRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("couriers.update")),
) -> ApiResponse[CourierResponse]:
    courier = await CourierService(session).create_courier(**payload.model_dump())
    return ApiResponse(data=CourierResponse.model_validate(courier), message="Courier created.")


@router.get("/{courier_id}", response_model=ApiResponse[CourierResponse])
async def get_courier(
    courier_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("couriers.read")),
) -> ApiResponse[CourierResponse]:
    courier = await CourierService(session).get_courier(courier_id)
    return ApiResponse(data=CourierResponse.model_validate(courier))


@router.patch("/{courier_id}", response_model=ApiResponse[CourierResponse])
async def update_courier(
    courier_id: uuid.UUID,
    payload: CourierUpdateRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("couriers.update")),
) -> ApiResponse[CourierResponse]:
    courier = await CourierService(session).update_courier(courier_id, **payload.model_dump())
    return ApiResponse(data=CourierResponse.model_validate(courier), message="Courier updated.")
