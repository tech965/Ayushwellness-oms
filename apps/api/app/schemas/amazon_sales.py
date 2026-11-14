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


class AccountSalesSummary(BaseModel):
    """Account-level totals (all ASINs combined) -- never product-wise."""

    currency: str | None
    total_units_ordered: int
    total_ordered_revenue: Decimal


class OrderItemRow(BaseModel):
    """One line item -- product fields only, never buyer/address data."""

    asin: str
    sku: str | None = None
    product_name: str | None = None
    units_ordered: int
    item_price: Decimal | None = None  # None when Amazon gave no price (e.g. gift/promo)


class OrderSummary(BaseModel):
    """One order's product + payment-method breakdown -- deliberately has
    no buyer name, email, phone or address field, even though Amazon's
    raw order response includes `BuyerInfo`/`ShippingAddress`.
    """

    order_id: str
    order_status: str
    payment_method: str  # "COD" | "Prepaid" | "Unknown" (from Amazon's own PaymentMethod field)
    items: list[OrderItemRow]


class DayOrdersReport(BaseModel):
    date: date
    orders: list[OrderSummary]
    truncated: bool
    truncated_note: str | None = None


class AccountSalesReport(BaseModel):
    """Account-level ordered units/revenue from the Sales API
    (`orderMetrics`, no `sku`/`asin` filter). Independent of the Reports
    API (`GET_SALES_AND_TRAFFIC_REPORT`) -- has no per-product breakdown
    and is NOT a substitute for it; see `scope_note`.
    """

    scope_note: str = (
        "Account-level totals across all products combined. Not product-wise -- "
        "per-ASIN/SKU figures require the Reports API, which is currently blocked "
        "pending Amazon authorization (see /status)."
    )
    date_from: date
    date_to: date
    fetched_at: datetime
    summary: AccountSalesSummary
    daily: list[DailyPoint]
    weekly: list[PeriodPoint]
    monthly: list[PeriodPoint]
    definitions: dict[str, str]
