from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.enums import RTORestockStatus, RTOStatus


class RTOUpdateRequest(BaseModel):
    status: RTOStatus | None = None
    notes: str | None = None


class RTOVerifyReceiptRequest(BaseModel):
    """`product_variant_id`/`accepted_quantity`/`rejected_quantity`: all
    three together (a partial/per-line verification), or all omitted (a
    full accept of every order line) -- see `RTOService.
    verify_warehouse_receipt`'s docstring.
    """

    product_variant_id: uuid.UUID | None = None
    accepted_quantity: int | None = None
    rejected_quantity: int | None = None
    notes: str | None = None


class RTOResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    shipment_id: uuid.UUID
    order_id: uuid.UUID
    courier_id: uuid.UUID | None
    reason: str | None
    normalized_reason: str | None
    external_reason: str | None
    status: RTOStatus
    restock_status: RTORestockStatus
    verified_at: datetime | None
    verified_by_user_id: uuid.UUID | None
    accepted_quantity: int | None
    rejected_quantity: int | None
    initiated_at: datetime | None
    completed_at: datetime | None
    notes: str | None
    source_system: str | None
    created_at: datetime
    updated_at: datetime


class RTOListResponse(RTOResponse):
    """`RTOResponse` plus denormalized order/customer/product columns —
    see `NDRListResponse`'s docstring (app/schemas/ndr.py) for the
    identical convention this mirrors.
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
