"""Request schemas for the IMS API (responses are plain dicts in the
standard `ApiResponse` envelope)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

Unit = Literal["BOX", "OUTER", "SACHETS", "ZIPPER", "BOTTLE", "JAR"]
Qty = Field(gt=0, le=10**12)
Key = Field(min_length=8, max_length=100)


class WarehouseCreate(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=255)
    address: str | None = None
    description: str | None = None
    is_active: bool = True
    sort_order: int | None = None


class WarehouseUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=40)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    address: str | None = None
    description: str | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class FinishedItemResolve(BaseModel):
    product_id: str = Field(min_length=1, max_length=100)
    variant_id: str = Field(min_length=1, max_length=100)


class ItemUpdate(BaseModel):
    low_stock_threshold: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class PackagingProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    category: str | None = None
    shopify_product_id: str | None = None
    notes: str | None = None
    units: list[Unit] = Field(default_factory=list)


class PackagingProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = None
    shopify_product_id: str | None = None
    notes: str | None = None
    is_active: bool | None = None
    add_units: list[Unit] = Field(default_factory=list)


class ConversionRule(BaseModel):
    larger_unit: Unit
    smaller_unit: Unit
    ratio: int = Field(ge=1, le=10**9)


class ConversionSet(BaseModel):
    rules: list[ConversionRule]


class ConvertRequest(BaseModel):
    packaging_product_id: uuid.UUID
    from_unit: Unit
    to_unit: Unit
    quantity: int = Qty


class StockInRequest(BaseModel):
    idempotency_key: str = Key
    warehouse_id: uuid.UUID
    item_id: uuid.UUID
    quantity: int = Qty
    reason: str = Field(min_length=3, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)


class AdjustmentRequest(BaseModel):
    idempotency_key: str = Key
    warehouse_id: uuid.UUID
    item_id: uuid.UUID
    adjustment_type: Literal["increase", "decrease", "damaged", "missing", "count_correction", "other"]
    direction: Literal["in", "out"] | None = None
    quantity: int = Qty
    reason: str = Field(min_length=3, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)


class LineIn(BaseModel):
    item_id: uuid.UUID
    quantity: int = Qty


class TransferRequest(BaseModel):
    idempotency_key: str = Key
    source_warehouse_id: uuid.UUID
    destination_warehouse_id: uuid.UUID
    lines: list[LineIn] = Field(min_length=1, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)


class ReturnRequest(BaseModel):
    idempotency_key: str = Key
    warehouse_id: uuid.UUID
    party_id: uuid.UUID
    return_date: date
    document_no: str | None = Field(default=None, max_length=120)
    lines: list[LineIn] = Field(min_length=1, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)


class PartyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    notes: str | None = None


class PartyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    notes: str | None = None
    is_active: bool | None = None


class OpeningLine(BaseModel):
    warehouse_id: uuid.UUID
    item_id: uuid.UUID
    quantity: int = Qty


class OpeningBalanceRequest(BaseModel):
    as_of: date
    lines: list[OpeningLine] = Field(min_length=1, max_length=2000)
    notes: str | None = Field(default=None, max_length=2000)
