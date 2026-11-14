"""Daily sales report -- aggregates yesterday's (Asia/Kolkata) sales
across every platform this OMS can actually reach, for the 9 AM WhatsApp
report. Every figure is either real (from a live platform call or the
local DB) or explicitly `None` ("N/A") -- never fabricated, never a
spreadsheet error (#VALUE!/#REF!/#DIV/0!), matching the user's uploaded
report's columns: Platform / Orders Count / Revenue / Revenue from
Ecom.Ads / SMM Spent / SEM Spent / ROI from Ecom.Ads / ROAS.

No ad-spend/ROAS data source exists anywhere in this codebase (checked
`app/models` and `app/services` broadly before concluding this) -- those
five columns are always N/A for every platform, for every row, until a
real ad-platform integration exists. This is not a bug to fix here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.amazon.config import AmazonConfig
from app.integrations.flipkart.config import FlipkartConfig
from app.models.abandoned_checkout import AbandonedCheckout
from app.models.enums import OrderStatus, PaymentType
from app.models.order import Order
from app.services.amazon_sales_service import AmazonSalesService
from app.services.flipkart_sales_service import FlipkartSalesService

IST = ZoneInfo("Asia/Kolkata")

# Platforms with no integration in this codebase at all -- always N/A,
# never a live call attempted.
UNCONFIGURED_PLATFORMS = ("Meesho", "Tata 1mg", "Jio Mart", "Blinkit")

# No ad-spend/ROAS data source exists anywhere in this codebase -- these
# five fields are always None (N/A) for every platform row.
_NO_AD_DATA = {
    "revenue_from_ads": None,
    "smm_spent": None,
    "sem_spent": None,
    "roi_from_ads": None,
    "roas": None,
}


@dataclass
class PlatformRow:
    platform: str
    configured: bool
    orders_count: int | None = None
    revenue: Decimal | None = None
    revenue_from_ads: Decimal | None = None
    smm_spent: Decimal | None = None
    sem_spent: Decimal | None = None
    roi_from_ads: Decimal | None = None
    roas: Decimal | None = None
    note: str | None = None


@dataclass
class DailySalesReport:
    report_date: date  # yesterday, Asia/Kolkata
    generated_at: datetime
    platforms: list[PlatformRow]
    total_orders: int
    total_revenue: Decimal
    total_ad_spend: Decimal | None
    abandoned_count: int
    abandoned_revenue: Decimal
    prepaid_orders: int
    prepaid_eligible_orders: int  # orders from platforms where payment type is actually known
    prepaid_percent: Decimal | None  # None when prepaid_eligible_orders == 0 (never #DIV/0!)
    warnings: list[str] = field(default_factory=list)


def yesterday_ist_window() -> tuple[date, datetime, datetime]:
    """Yesterday's calendar date in IST, and its [start, end) as UTC
    instants for querying `AwareDateTime` columns.
    """
    now_ist = datetime.now(IST)
    yesterday = (now_ist - timedelta(days=1)).date()
    start_ist = datetime(yesterday.year, yesterday.month, yesterday.day, tzinfo=IST)
    end_ist = start_ist + timedelta(days=1)
    return yesterday, start_ist.astimezone(UTC), end_ist.astimezone(UTC)


def _shopify_order_filter(start: datetime, end: datetime):
    return (
        Order.source_system == "shopify",
        Order.order_datetime >= start,
        Order.order_datetime < end,
        Order.status != OrderStatus.CANCELLED,
    )


async def _shopify_row(session: AsyncSession, start: datetime, end: datetime) -> PlatformRow:
    filters = _shopify_order_filter(start, end)
    count = (
        await session.execute(select(func.count()).select_from(Order).where(*filters))
    ).scalar_one()
    revenue = (
        await session.execute(
            select(func.coalesce(func.sum(Order.total_amount), 0)).where(*filters)
        )
    ).scalar_one()
    return PlatformRow(
        platform="Shopify",
        configured=True,
        orders_count=int(count),
        revenue=Decimal(str(revenue)),
        **_NO_AD_DATA,
    )


async def _shopify_prepaid_counts(
    session: AsyncSession, start: datetime, end: datetime
) -> tuple[int, int]:
    """(prepaid_count, total_count) for Shopify orders in the window --
    the only platform this OMS has a real payment-type field for.
    """
    filters = _shopify_order_filter(start, end)
    total = (
        await session.execute(select(func.count()).select_from(Order).where(*filters))
    ).scalar_one()
    prepaid = (
        await session.execute(
            select(func.count())
            .select_from(Order)
            .where(*filters, Order.payment_type == PaymentType.PREPAID)
        )
    ).scalar_one()
    return int(prepaid), int(total)


async def _amazon_row(report_date: date, warnings: list[str]) -> PlatformRow:
    config = AmazonConfig.from_settings()
    if not config.configured:
        return PlatformRow(platform="Amazon", configured=False, note="Not configured")
    try:
        svc = AmazonSalesService(config)
        report = await svc.account_report(report_date, report_date)
        order_items = sum((d.order_items or 0) for d in report.daily)
        return PlatformRow(
            platform="Amazon",
            configured=True,
            orders_count=order_items,
            revenue=report.summary.total_ordered_revenue,
            note="Orders Count is Amazon's order-item count (orderItemCount), not a "
            "distinct-order count -- the fast account-level Sales API doesn't expose that.",
            **_NO_AD_DATA,
        )
    except Exception as exc:  # noqa: BLE001 - one platform failing must not break the report
        warnings.append(f"Amazon row unavailable: {exc}")
        return PlatformRow(platform="Amazon", configured=True, note=f"Fetch failed: {exc}")


async def _flipkart_row(report_date: date, warnings: list[str]) -> PlatformRow:
    config = FlipkartConfig.from_settings()
    if not config.configured:
        return PlatformRow(platform="Flipkart", configured=False, note="Not configured")
    try:
        svc = FlipkartSalesService(config)
        page = await svc.all_orders_for_export(
            date_from=report_date, date_to=report_date, status=None
        )
        order_ids = {it.order_id for it in page.items}
        revenue = sum((it.selling_price or Decimal(0) for it in page.items), Decimal(0))
        warnings.extend(page.warnings)
        return PlatformRow(
            platform="Flipkart",
            configured=True,
            orders_count=len(order_ids),
            revenue=revenue,
            note="Revenue is the sum of each line item's sellingPrice; Flipkart's docs don't "
            "confirm whether this is per-unit or per-line, so multi-quantity lines may be "
            "undercounted.",
            **_NO_AD_DATA,
        )
    except Exception as exc:  # noqa: BLE001 - one platform failing must not break the report
        warnings.append(f"Flipkart row unavailable: {exc}")
        return PlatformRow(platform="Flipkart", configured=True, note=f"Fetch failed: {exc}")


async def _abandoned_checkouts(
    session: AsyncSession, start: datetime, end: datetime
) -> tuple[int, Decimal]:
    count = (
        await session.execute(
            select(func.count())
            .select_from(AbandonedCheckout)
            .where(
                AbandonedCheckout.checkout_created_at >= start,
                AbandonedCheckout.checkout_created_at < end,
                AbandonedCheckout.is_recovered.is_(False),
            )
        )
    ).scalar_one()
    revenue = (
        await session.execute(
            select(func.coalesce(func.sum(AbandonedCheckout.total_amount), 0)).where(
                AbandonedCheckout.checkout_created_at >= start,
                AbandonedCheckout.checkout_created_at < end,
                AbandonedCheckout.is_recovered.is_(False),
            )
        )
    ).scalar_one()
    return int(count), Decimal(str(revenue))


async def build_daily_sales_report(session: AsyncSession) -> DailySalesReport:
    report_date, start, end = yesterday_ist_window()
    warnings: list[str] = []

    shopify = await _shopify_row(session, start, end)
    amazon = await _amazon_row(report_date, warnings)
    flipkart = await _flipkart_row(report_date, warnings)
    unconfigured = [
        PlatformRow(platform=p, configured=False, note="Not configured")
        for p in UNCONFIGURED_PLATFORMS
    ]
    platforms = [shopify, amazon, flipkart, *unconfigured]

    total_orders = sum(p.orders_count or 0 for p in platforms)
    total_revenue = sum((p.revenue or Decimal(0) for p in platforms), Decimal(0))
    # No platform has ad spend -> total ad spend is N/A, never 0 (0 would
    # falsely claim "measured, spent nothing").
    total_ad_spend = None

    abandoned_count, abandoned_revenue = await _abandoned_checkouts(session, start, end)
    prepaid, prepaid_total = await _shopify_prepaid_counts(session, start, end)
    prepaid_percent = (
        (Decimal(prepaid) / Decimal(prepaid_total) * 100).quantize(Decimal("0.01"))
        if prepaid_total > 0
        else None  # never #DIV/0! -- explicit N/A instead
    )
    if prepaid_total > 0:
        warnings.append(
            "Prepaid % is Shopify-only -- Amazon/Flipkart don't expose payment type via "
            "their APIs (verified this session), so they can't be included without "
            "fabricating a number."
        )

    return DailySalesReport(
        report_date=report_date,
        generated_at=datetime.now(UTC),
        platforms=platforms,
        total_orders=total_orders,
        total_revenue=total_revenue,
        total_ad_spend=total_ad_spend,
        abandoned_count=abandoned_count,
        abandoned_revenue=abandoned_revenue,
        prepaid_orders=prepaid,
        prepaid_eligible_orders=prepaid_total,
        prepaid_percent=prepaid_percent,
        warnings=warnings,
    )
