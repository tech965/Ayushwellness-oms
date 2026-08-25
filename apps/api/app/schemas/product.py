from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ProductStatus


class ProductVariantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    shopify_variant_id: str | None
    sku: str
    title: str | None
    price: Decimal
    compare_at_price: Decimal | None
    inventory_quantity: int
    weight: Decimal | None
    barcode: str | None
    options: dict | None
    status: ProductStatus
    created_at: datetime
    updated_at: datetime


class ProductCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    vendor: str | None = None
    status: ProductStatus = ProductStatus.ACTIVE


class ProductUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    vendor: str | None = None
    status: ProductStatus | None = None


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    shopify_product_id: str | None
    title: str
    description: str | None
    status: ProductStatus
    vendor: str | None
    product_type: str | None
    tags: str | None
    source_system: str | None
    created_at: datetime
    updated_at: datetime


class ProductDetailResponse(ProductResponse):
    variants: list[ProductVariantResponse] = []
