"""Inventory schemas -- Product -> Variant -> Inventory hierarchy.

All quantities exposed here (`available_boxes`, movement deltas/balances)
are in BOXES. `inventory_quantity`/`shopify_inventory_quantity` fields are
Shopify's own count, included only as a passive reference value -- never
an input to any calculation (see `app.services.inventory_service`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.enums import InventoryMovementType, ProductStatus, StockStatus


class InventoryVariantResponse(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    # `variant_title` is the raw Shopify name; `variant_title_override` is
    # the staff-set custom name (or null); `display_title` is what the UI
    # should show -- override if present, else the Shopify title, else SKU.
    variant_title: str | None
    variant_title_override: str | None
    display_title: str
    packets_per_box: int
    available_boxes: int
    total_packets: int
    stock_status: StockStatus
    status: ProductStatus
    shopify_inventory_quantity: int
    updated_at: datetime


class InventoryProductSummaryResponse(BaseModel):
    id: uuid.UUID
    # `title` is the raw Shopify name; `title_override` is the staff-set
    # custom name (or null); `display_title` is what the UI should show.
    title: str
    title_override: str | None
    display_title: str
    vendor: str | None
    variant_count: int
    total_available_boxes: int
    total_packets: int
    stock_status: StockStatus
    updated_at: datetime


class InventoryProductVariantsResponse(BaseModel):
    product_id: uuid.UUID
    product_title: str
    product_title_override: str | None
    product_display_title: str
    variants: list[InventoryVariantResponse]


class ProductVariantStockLine(BaseModel):
    """One underlying variant row inside the product-level Inventory card.
    Kept so the product card can drive a per-variant Edit Stock without a
    second API call, and so the UI never has to invent a product-level
    stock distribution -- each line maps 1:1 to a real `ProductVariant`.
    """

    id: uuid.UUID
    sku: str
    variant_title: str | None
    variant_title_override: str | None
    display_title: str
    available_boxes: int
    packets_per_box: int
    total_packets: int
    stock_status: StockStatus


class InventoryProductStockResponse(BaseModel):
    """Product-level Inventory card -- ONE per product, aggregated from the
    product's underlying `ProductVariant` rows (never a separate stored
    record). `available_boxes` is `sum(variant.available_quantity)` (boxes
    are the common unit for every variant); `total_packets` is
    `sum(variant.available_quantity * variant.packets_per_box)` (each term
    uses that variant's own factor). `packets_per_box_uniform` is False
    when the variants disagree on pack size -- the UI shows "mixed pack
    sizes" rather than implying a single conversion.
    """

    product_id: uuid.UUID
    product_name: str  # display name: title_override or Shopify title
    title: str
    title_override: str | None
    available_boxes: int
    total_packets: int
    stock_status: StockStatus
    variant_count: int
    packets_per_box_uniform: bool
    variant_ids: list[uuid.UUID]
    variants: list[ProductVariantStockLine]


class ProductStockAdjustmentRequest(BaseModel):
    """Absolute-target adjustment for a SINGLE-variant product -- the same
    contract as `InventoryAdjustmentRequest`. A product with more than one
    variant is rejected (no invented distribution rule); the client edits
    each variant line individually via `POST /inventory/stock/{id}/adjust`.
    """

    target_boxes: int = Field(ge=0, description="The new total stock, in boxes.")
    reason: str = Field(min_length=1, max_length=255)


def _normalize_display_name(value: str) -> str:
    """Trim surrounding whitespace and reject a blank/whitespace-only name."""
    stripped = value.strip()
    if not stripped:
        raise ValueError("Name cannot be empty.")
    return stripped


class ProductNameUpdateRequest(BaseModel):
    """Set a custom display name for a product. `max_length` matches
    `products.title` (`String(500)`).
    """

    name: str = Field(min_length=1, max_length=500)

    _normalize = field_validator("name")(_normalize_display_name)


class VariantNameUpdateRequest(BaseModel):
    """Set a custom display name for a variant. `max_length` matches
    `product_variants.title` (`String(255)`).
    """

    name: str = Field(min_length=1, max_length=255)

    _normalize = field_validator("name")(_normalize_display_name)


class CatalogNameResponse(BaseModel):
    """Returned by the Edit-Name / Reset-Name endpoints so the client can
    update the shown name immediately without reshaping a list row.
    """

    id: uuid.UUID
    title: str | None
    title_override: str | None
    display_title: str


class InventoryMovementResponse(BaseModel):
    id: uuid.UUID
    product_variant_id: uuid.UUID
    product_id: uuid.UUID | None
    product_title: str | None
    variant_title: str | None
    # Resolved name for display -- variant `title_override` if set, else
    # the Shopify title, else the SKU.
    variant_display_title: str | None
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
