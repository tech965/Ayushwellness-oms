from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.enums import RefundStatus


class RefundResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    payment_id: uuid.UUID | None
    return_id: uuid.UUID | None
    amount: Decimal
    reason: str | None
    status: RefundStatus
    initiated_at: datetime | None
    completed_at: datetime | None
    source_system: str | None
    created_at: datetime
    updated_at: datetime


class RefundListResponse(RefundResponse):
    """`RefundResponse` plus denormalized order/customer/product columns —
    see `NDRListResponse`'s docstring (app/schemas/ndr.py) for the
    identical convention this mirrors. `order_amount` is the ORIGINAL
    order total, deliberately distinct from `amount` above (the refund
    amount) — never conflate the two.
    """

    order_number: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    product: str | None = None
    order_amount: Decimal | None = None
    payment_type: str | None = None
