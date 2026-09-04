from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.enums import NDRStatus


class NDRUpdateRequest(BaseModel):
    status: NDRStatus | None = None
    customer_response: str | None = None
    reattempt_status: str | None = None
    reattempt_date: datetime | None = None
    notes: str | None = None


class NDRResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    shipment_id: uuid.UUID
    order_id: uuid.UUID
    courier_id: uuid.UUID | None
    reason: str | None
    normalized_reason: str | None
    external_reason: str | None
    attempt_number: int
    status: NDRStatus
    customer_response: str | None
    reattempt_status: str | None
    reattempt_date: datetime | None
    notes: str | None
    source_system: str | None
    created_at: datetime
    updated_at: datetime


class NDRListResponse(NDRResponse):
    """`NDRResponse` plus the denormalized order/customer/product columns
    the NDR operational table needs, computed by `NDRService.list_ndrs`
    from the same eager-loaded `NDR.order`/`order.customer`/`order.items`/
    `NDR.shipment` relationships `NDRRepository.search_query` already
    loads — no per-row query. Mirrors `OrderListResponse`'s existing
    denormalization convention (app/schemas/order.py).
    """

    order_number: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    product: str | None = None
    order_amount: Decimal | None = None
    payment_type: str | None = None
    shipment_status: str | None = None
    awb: str | None = None
    courier_name: str | None = None
