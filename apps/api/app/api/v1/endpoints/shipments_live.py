"""Live Shiprocket reads that have no equivalent in the stored-data API:
NDR straight from Shiprocket and a per-order shipment view. Nothing here
stores provider data."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.models.auth import User
from app.schemas.response import ApiResponse
from app.schemas.shipment import ShipmentResponse
from app.services.live_shiprocket import LiveShiprocketService
from app.services.order_ref import resolve_ref

router = APIRouter()


class NdrReattemptRequest(BaseModel):
    address_1: str
    address_2: str | None = None
    phone: str


@router.get("/ndr", response_model=ApiResponse[dict])
async def list_live_ndr(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("ndr.read")),
) -> ApiResponse[dict[str, Any]]:
    return ApiResponse(
        data=await LiveShiprocketService(session).list_ndr(page=page, per_page=per_page)
    )


@router.post("/shipments/{shipment_id}/ndr-reattempt", response_model=ApiResponse[dict])
async def request_live_ndr_reattempt(
    shipment_id: uuid.UUID,
    payload: NdrReattemptRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("ndr.update")),
) -> ApiResponse[dict[str, Any]]:
    result = await LiveShiprocketService(session).ndr_reattempt(
        shipment_id,
        actor=current_user,
        address_1=payload.address_1,
        address_2=payload.address_2,
        phone=payload.phone,
    )
    return ApiResponse(data=result, message="Reattempt requested.")


@router.get("/orders/{order_id}/shipments", response_model=ApiResponse[list[ShipmentResponse]])
async def list_live_order_shipments(
    order_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("shipments.read")),
) -> ApiResponse[list[ShipmentResponse]]:
    ref = await resolve_ref(session, order_id)
    shipments = await LiveShiprocketService(session).shipments_for_order(
        ref.ref_id if ref is not None else order_id
    )
    return ApiResponse(data=[ShipmentResponse.model_validate(s) for s in shipments])
