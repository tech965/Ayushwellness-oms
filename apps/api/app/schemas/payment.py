from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.enums import PaymentStatus, PaymentType


class PaymentTransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    payment_id: uuid.UUID
    gateway: str | None
    gateway_transaction_id: str | None
    status: PaymentStatus
    amount: Decimal
    created_at: datetime


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    payment_type: PaymentType
    status: PaymentStatus
    amount: Decimal
    currency: str
    provider: str | None
    external_transaction_id: str | None
    paid_at: datetime | None
    source_system: str | None
    created_at: datetime
    updated_at: datetime


class PaymentDetailResponse(PaymentResponse):
    transactions: list[PaymentTransactionResponse] = []
