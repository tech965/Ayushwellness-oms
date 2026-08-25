from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    CancellationStatus,
    FulfillmentStatus,
    OrderStatus,
    PaymentStatus,
    PaymentType,
)


class OrderItemCreateRequest(BaseModel):
    product_variant_id: uuid.UUID | None = None
    sku: str = Field(min_length=1, max_length=120)
    product_name: str = Field(min_length=1, max_length=500)
    quantity: int = Field(gt=0)
    unit_price: Decimal = Field(ge=0)
    discount_amount: Decimal = Field(default=Decimal("0"), ge=0)
    tax_amount: Decimal = Field(default=Decimal("0"), ge=0)


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    product_variant_id: uuid.UUID | None
    sku: str
    product_name: str
    quantity: int
    unit_price: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal


class OrderCreateRequest(BaseModel):
    order_number: str = Field(min_length=1, max_length=64)
    customer_id: uuid.UUID | None = None
    order_datetime: datetime | None = None
    currency: str = "INR"
    payment_type: PaymentType = PaymentType.PREPAID
    shipping_charge: Decimal = Field(default=Decimal("0"), ge=0)
    notes: str | None = None
    items: list[OrderItemCreateRequest] = Field(min_length=1)


class OrderStatusTransitionRequest(BaseModel):
    status: OrderStatus
    description: str | None = None


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_number: str
    shopify_order_id: str | None
    customer_id: uuid.UUID | None
    order_datetime: datetime
    currency: str
    subtotal: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    shipping_charge: Decimal
    total_amount: Decimal
    payment_type: PaymentType
    payment_status: PaymentStatus
    status: OrderStatus
    fulfillment_status: FulfillmentStatus
    cancellation_status: CancellationStatus
    notes: str | None
    shipping_address: dict | None
    billing_address: dict | None
    source_system: str | None
    created_at: datetime
    updated_at: datetime


class OrderDetailResponse(OrderResponse):
    items: list[OrderItemResponse] = []


class OrderEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    event_type: str
    status: str | None
    description: str | None
    source: str
    actor_user_id: uuid.UUID | None
    event_metadata: dict | None
    created_at: datetime


class OrderEventCreateRequest(BaseModel):
    event_type: str = Field(min_length=1, max_length=100)
    status: str | None = None
    description: str | None = None
    event_metadata: dict | None = None
