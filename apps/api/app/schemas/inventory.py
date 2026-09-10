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
    # Shopify's featured product image (`Product.image_url`), or null if
    # Shopify has none / the product was never synced. Source of truth is
    # Shopify -- never an OMS-hosted copy; the frontend renders this URL
    # directly and falls back to a placeholder when null.
    image_url: str | None
    # Number of OMS-VISIBLE catalog variants (not the raw Shopify
    # ProductVariant count): declared `CatalogVariant`s + any variant not
    # yet grouped (each of those counts as its own implicit OMS variant).
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
    # This row's OWN Shopify image (`ProductVariant.image_url`), if
    # Shopify assigned one distinct from the product's featured image.
    # Usually null -- most variants share the product photo.
    image_url: str | None


class OmsCatalogVariantResponse(BaseModel):
    """One OMS-visible variant on the product detail page. It groups one
    or more underlying Shopify `ProductVariant` rows.

    `available_boxes = SUM(underlying.available_quantity)`.
    `total_packets  = SUM(underlying.available_quantity *
                          underlying.packets_per_box)`.
    `packets_per_box_uniform` is False when the grouped variants disagree
    on pack size -- the UI shows "mixed pack sizes"; NO single conversion
    ratio is fabricated. Stock is never stored here; it is recomputed
    from the live underlying rows on every read.

    `catalog_variant_id` is None for an implicit OMS variant (a
    `ProductVariant` not yet grouped -- 1:1 with its single underlying
    row); a real UUID for a declared `CatalogVariant`.

    `image_url` is RESOLVED, not stored: the lowest-SKU underlying
    variant's own `image_url` if Shopify gave one of its members a
    distinct image (e.g. a real per-flavour photo) -- a deterministic
    tie-break, not "whichever the DB happened to return first" -- else
    the product's featured image, else null. Never guessed from
    position/filename/colour -- only an explicit Shopify variant/image
    association, or the product fallback.
    """

    catalog_variant_id: uuid.UUID | None
    name: str
    display_order: int
    is_active: bool
    available_boxes: int
    total_packets: int
    stock_status: StockStatus
    packets_per_box_uniform: bool
    underlying_variant_count: int
    underlying_variants: list[ProductVariantStockLine]
    image_url: str | None


class InventoryProductStockResponse(BaseModel):
    """Product detail payload. `oms_variants` is the ONLY variant view the
    UI shows -- e.g. Aayush Herbal Masala returns exactly 3 (one per
    flavour); every other product returns exactly 1 once grouped. The raw
    Shopify `ProductVariant` rows are preserved and reachable only inside
    each OMS variant's `underlying_variants`.

    Product-level `available_boxes` / `total_packets` are the sum across
    every underlying `ProductVariant`, same formula as the OMS-variant
    aggregates.
    """

    product_id: uuid.UUID
    # Shopify's own, immutable product id -- the ONLY safe key for the
    # frontend to scope any product-specific display behaviour by (e.g.
    # the canonical Herbal Masala product's pack-size labels). Never the
    # OMS UUID above, and never `title`/`product_name` (two products can
    # share the same title -- e.g. an unpublished draft duplicate).
    shopify_product_id: str | None
    product_name: str  # display name: title_override or Shopify title
    title: str
    title_override: str | None
    image_url: str | None  # Shopify's featured product image, or null
    available_boxes: int
    total_packets: int
    stock_status: StockStatus
    packets_per_box_uniform: bool
    oms_variant_count: int
    underlying_variant_count: int
    oms_variants: list[OmsCatalogVariantResponse]


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


class CatalogVariantNameUpdateRequest(BaseModel):
    """Rename an OMS-visible `CatalogVariant`. `max_length` matches
    `catalog_variants.name` (`String(255)`).
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
    # The OMS-visible variant this movement's `ProductVariant` is grouped
    # under, if any -- lets the UI show that a CatalogVariant's history
    # spans several underlying Shopify SKUs. Null == not grouped.
    catalog_variant_id: uuid.UUID | None
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
