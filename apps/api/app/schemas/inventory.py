"""Inventory schemas -- Product -> Variant -> Inventory hierarchy.

All quantities exposed here (`available_boxes`, movement deltas/balances)
are in BOXES. `inventory_quantity`/`shopify_inventory_quantity` fields are
Shopify's own count, included only as a passive reference value -- never
an input to any calculation (see `app.services.inventory_service`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import InventoryMovementType, ProductStatus, StockStatus


class InventoryVariantResponse(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    variant_title: str | None
    packets_per_box: int
    available_boxes: int
    total_packets: int
    stock_status: StockStatus
    status: ProductStatus
    shopify_inventory_quantity: int
    updated_at: datetime


class InventoryProductSummaryResponse(BaseModel):
    id: uuid.UUID
    title: str
    vendor: str | None
    variant_count: int
    total_available_boxes: int
    total_packets: int
    stock_status: StockStatus
    updated_at: datetime


class InventoryProductVariantsResponse(BaseModel):
    product_id: uuid.UUID
    product_title: str
    variants: list[InventoryVariantResponse]


class InventoryMovementResponse(BaseModel):
    id: uuid.UUID
    product_variant_id: uuid.UUID
    product_id: uuid.UUID | None
    product_title: str | None
    variant_title: str | None
    sku: str | None
    movement_type: InventoryMovementType
    quantity_delta: int
    previous_balance: int
    quantity_after: int
    order_id: uuid.UUID | None
    shipment_id: uuid.UUID | None
    rto_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    actor_label: str
    reason: str | None
    notes: str | None
    created_at: datetime


class InventoryAdjustmentRequest(BaseModel):
    """Absolute-target adjustment -- staff enters the new total stock, not
    a raw delta. `InventoryService.adjust_to_target` computes and records
    the resulting delta.
    """

    target_boxes: int = Field(ge=0, description="The new total stock, in boxes.")
    reason: str = Field(min_length=1, max_length=255)


class PacketsPerBoxUpdateRequest(BaseModel):
    packets_per_box: int = Field(gt=0, description="Packets contained in one box.")
