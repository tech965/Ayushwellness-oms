"""Daily sales report aggregation. Amazon/Flipkart are unconfigured in
the test environment (no env vars set) -- their rows must show
"Not configured", never fabricated numbers, which this suite checks
explicitly alongside the Shopify/abandoned-checkout DB aggregation.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from app.models.abandoned_checkout import AbandonedCheckout
from app.models.enums import FulfillmentStatus, OrderStatus, PaymentStatus, PaymentType
from app.models.order import Order
from app.services.whatsapp_daily_report_service import (
    UNCONFIGURED_PLATFORMS,
    build_daily_sales_report,
    yesterday_ist_window,
)
from app.services.whatsapp_report_formatter import format_report_text, report_to_xlsx
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

IST = ZoneInfo("Asia/Kolkata")


def _order(
    order_number: str, *, order_datetime, payment_type, amount, status=OrderStatus.CONFIRMED
):
    return Order(
        order_number=order_number,
        order_datetime=order_datetime,
        currency="INR",
        subtotal=amount,
        total_amount=amount,
        payment_type=payment_type,
        payment_status=PaymentStatus.PAID,
        status=status,
        fulfillment_status=FulfillmentStatus.UNFULFILLED,
        source_system="shopify",
        external_id=order_number,
    )


async def test_yesterday_window_is_calendar_day_before_in_ist():
    yesterday, start_utc, end_utc = yesterday_ist_window()
    now_ist = datetime.now(IST)
    assert yesterday == (now_ist - timedelta(days=1)).date()
    assert (end_utc - start_utc) == timedelta(days=1)


async def test_unconfigured_platforms_never_fabricate_data(db_session: AsyncSession):
    report = await build_daily_sales_report(db_session)
    by_name = {p.platform: p for p in report.platforms}
    for name in UNCONFIGURED_PLATFORMS:
        assert by_name[name].configured is False
        assert by_name[name].orders_count is None
        assert by_name[name].revenue is None
    # No ad-spend source exists anywhere -- always None for every row.
    for p in report.platforms:
        assert p.revenue_from_ads is None
        assert p.smm_spent is None
        assert p.sem_spent is None
        assert p.roi_from_ads is None
        assert p.roas is None
    assert report.total_ad_spend is None


async def test_prepaid_percent_is_none_not_div_zero_when_no_shopify_orders(
    db_session: AsyncSession,
):
    report = await build_daily_sales_report(db_session)
    if report.prepaid_eligible_orders == 0:
        assert report.prepaid_percent is None


async def test_shopify_orders_aggregated_correctly(db_session: AsyncSession):
    _, start_utc, end_utc = yesterday_ist_window()
    mid = start_utc + (end_utc - start_utc) / 2
    db_session.add(
        _order("S1", order_datetime=mid, payment_type=PaymentType.PREPAID, amount=Decimal("500"))
    )
    db_session.add(
        _order("S2", order_datetime=mid, payment_type=PaymentType.COD, amount=Decimal("300"))
    )
    db_session.add(
        _order(
            "S3",
            order_datetime=mid,
            payment_type=PaymentType.PREPAID,
            amount=Decimal("9999"),
            status=OrderStatus.CANCELLED,
        )
    )
    # Outside the window -- must not be counted.
    db_session.add(
        _order(
            "S4",
            order_datetime=start_utc - timedelta(days=2),
            payment_type=PaymentType.PREPAID,
            amount=Decimal("1000"),
        )
    )
    await db_session.commit()

    report = await build_daily_sales_report(db_session)
    shopify = next(p for p in report.platforms if p.platform == "Shopify")
    assert shopify.orders_count == 2  # cancelled and out-of-window excluded
    assert shopify.revenue == Decimal("800")
    assert report.prepaid_orders == 1
    assert report.prepaid_eligible_orders == 2
    assert report.prepaid_percent == Decimal("50.00")


async def test_abandoned_checkouts_excludes_recovered(db_session: AsyncSession):
    _, start_utc, end_utc = yesterday_ist_window()
    mid = start_utc + (end_utc - start_utc) / 2
    db_session.add(
        AbandonedCheckout(
            customer_name="X",
            customer_phone="9991234567",
            total_amount=Decimal("250"),
            checkout_created_at=mid,
            is_recovered=False,
            source_system="shopify",
            external_id="abc1",
        )
    )
    db_session.add(
        AbandonedCheckout(
            customer_name="Y",
            customer_phone="9991234568",
            total_amount=Decimal("999"),
            checkout_created_at=mid,
            is_recovered=True,
            source_system="shopify",
            external_id="abc2",
        )
    )
    await db_session.commit()

    report = await build_daily_sales_report(db_session)
    assert report.abandoned_count == 1
    assert report.abandoned_revenue == Decimal("250")


async def test_formatter_never_raises_and_renders_na_for_none(db_session: AsyncSession):
    report = await build_daily_sales_report(db_session)
    text = format_report_text(report)
    assert "N/A" in text or report.prepaid_percent is not None
    assert "Not configured" in text
    # Must not contain any spreadsheet-error-looking tokens.
    for bad in ("#VALUE!", "#REF!", "#DIV/0!"):
        assert bad not in text


async def test_xlsx_export_never_raises(db_session: AsyncSession):
    report = await build_daily_sales_report(db_session)
    data = report_to_xlsx(report)
    assert data[:2] == b"PK"  # a real xlsx (zip) file was produced
