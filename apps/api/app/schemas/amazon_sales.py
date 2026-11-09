from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class ChunkRef(BaseModel):
    """Opaque provider handle for one report window. Not provider data."""

    report_id: str
    start: date
    end: date


class FetchRequest(BaseModel):
    date_from: date
    date_to: date


class PollRequest(BaseModel):
    chunks: list[ChunkRef] = Field(min_length=1)
    q: str | None = None  # product name / SKU / ASIN filter (table only)


class AmazonStatus(BaseModel):
    configured: bool
    missing: list[str]
    region: str | None
    marketplace_id: str | None
    max_range_days: int
    last_successful_fetch_at: datetime | None


class DailyPoint(BaseModel):
    date: date
    units_ordered: int
    ordered_revenue: Decimal
    order_items: int | None = None
    units_shipped: int | None = None
    shipped_revenue: Decimal | None = None
    units_refunded: int | None = None


class PeriodPoint(BaseModel):
    period: str  # ISO date of period start (week: Monday; month: first day)
    label: str
    units_ordered: int
    ordered_revenue: Decimal
    units_shipped: int | None = None
    shipped_revenue: Decimal | None = None
    units_refunded: int | None = None


class ProductRow(BaseModel):
    asin: str
    sku: str | None = None
    product_name: str | None = None  # None => unavailable
    units_ordered: int
    units_shipped: int | None = None  # None => unavailable (not in by-ASIN report)
    ordered_revenue: Decimal
    avg_selling_price: Decimal | None = None  # None when units_ordered == 0


class SalesSummary(BaseModel):
    currency: str | None
    total_units_ordered: int
    total_ordered_revenue: Decimal
    products_sold: int
    total_units_shipped: int | None = None
    total_shipped_revenue: Decimal | None = None
    total_units_refunded: int | None = None


class SalesReport(BaseModel):
    date_from: date
    date_to: date
    fetched_at: datetime
    summary: SalesSummary
    daily: list[DailyPoint]
    weekly: list[PeriodPoint]
    monthly: list[PeriodPoint]
    products: list[ProductRow]
    unavailable: dict[str, str]
    warnings: list[str]
    definitions: dict[str, str]


class PollResponse(BaseModel):
    state: str  # processing | done
    chunk_statuses: dict[str, str]
    report: SalesReport | None = None
