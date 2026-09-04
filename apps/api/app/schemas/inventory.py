from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import InventoryMovementType, ProductStatus


class InventoryStockResponse(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    variant_title: str | None
    product_title: str
    available_quantity: int
    inventory_quantity: int
    status: ProductStatus
    updated_at: datetime


class InventoryMovementResponse(BaseModel):
    id: uuid.UUID
    product_variant_id: uuid.UUID
    sku: str | None
    movement_type: InventoryMovementType
    quantity_delta: int
    quantity_after: int
    order_id: uuid.UUID | None
    shipment_id: uuid.UUID | None
    rto_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    reason: str | None
    notes: str | None
    created_at: datetime


class InventoryAdjustmentRequest(BaseModel):
    delta: int = Field(description="Signed change to available_quantity; must be non-zero.")
    reason: str = Field(min_length=1, max_length=255)
