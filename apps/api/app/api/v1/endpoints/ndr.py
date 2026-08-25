"""NDR foundation — read + status update. Creation is Phase 2 sync work
(spec's endpoint list has no POST for NDR).
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
from app.schemas.ndr import NDRResponse, NDRUpdateRequest
from app.schemas.response import ApiResponse, PaginatedResponse
from app.schemas.shiprocket import ShiprocketNdrReattemptRequest
from app.services.ndr_service import NDRService
from app.services.shiprocket_service import ShiprocketOperationsService

router = APIRouter()


@router.get("", response_model=PaginatedResponse[NDRResponse])
async def list_ndrs(
    status: str | None = Query(default=None),
    courier_id: uuid.UUID | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("ndr.read")),
) -> PaginatedResponse[NDRResponse]:
    items, total = await NDRService(session).list_ndrs(
        page_params=page_params, sort_params=sort_params, status=status, courier_id=courier_id
    )
    return PaginatedResponse(
        data=[NDRResponse.model_validate(n) for n in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.get("/{ndr_id}", response_model=ApiResponse[NDRResponse])
async def get_ndr(
    ndr_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("ndr.read")),
) -> ApiResponse[NDRResponse]:
    ndr = await NDRService(session).get_ndr(ndr_id)
    return ApiResponse(data=NDRResponse.model_validate(ndr))


@router.patch("/{ndr_id}", response_model=ApiResponse[NDRResponse])
async def update_ndr(
    ndr_id: uuid.UUID,
    payload: NDRUpdateRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("ndr.update")),
) -> ApiResponse[NDRResponse]:
    ndr = await NDRService(session).update_ndr(ndr_id, actor=current_user, **payload.model_dump())
    return ApiResponse(data=NDRResponse.model_validate(ndr), message="NDR updated.")


@router.post("/{ndr_id}/reattempt", response_model=ApiResponse[NDRResponse])
async def request_ndr_reattempt(
    ndr_id: uuid.UUID,
    payload: ShiprocketNdrReattemptRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("ndr.update")),
) -> ApiResponse[NDRResponse]:
    """Requests a delivery reattempt via Shiprocket (spec §17) — the NDR's
    status is only updated after Shiprocket confirms the request.
    """
    ndr = await ShiprocketOperationsService(session).ndr_reattempt(
        ndr_id,
        actor=current_user,
        address_1=payload.address_1,
        address_2=payload.address_2,
        phone=payload.phone,
    )
    return ApiResponse(data=NDRResponse.model_validate(ndr), message="Reattempt requested.")
