from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import AddressType


class CustomerAddressCreateRequest(BaseModel):
    address_type: AddressType = AddressType.SHIPPING
    line1: str = Field(min_length=1, max_length=255)
    line2: str | None = None
    city: str = Field(min_length=1, max_length=120)
    state: str | None = None
    country: str = "India"
    pin_code: str = Field(min_length=1, max_length=16)
    landmark: str | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    is_default: bool = False


class CustomerAddressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    address_type: AddressType
    line1: str
    line2: str | None
    city: str
    state: str | None
    country: str
    pin_code: str
    landmark: str | None
    contact_name: str | None
    contact_phone: str | None
    is_default: bool
    source_system: str | None
    created_at: datetime
    updated_at: datetime


class CustomerCreateRequest(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    alternate_phone: str | None = None
    notes: str | None = None


class CustomerUpdateRequest(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    alternate_phone: str | None = None
    notes: str | None = None
    is_active: bool | None = None


class CustomerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    shopify_customer_id: str | None
    first_name: str | None
    last_name: str | None
    full_name: str | None
    email: str | None
    phone: str | None
    alternate_phone: str | None
    is_active: bool
    notes: str | None
    source_system: str | None
    external_id: str | None
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CustomerSummaryResponse(BaseModel):
    customer_id: uuid.UUID
    total_orders: int
    total_spent: Decimal
    delivered_orders: int
    cancelled_orders: int
    rto_orders: int
    average_order_value: Decimal
    last_order_at: datetime | None
