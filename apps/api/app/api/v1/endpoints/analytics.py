"""Dashboard business-intelligence endpoints (fills in the Phase-3 stub —
see `app.services.analytics_service` for metric semantics). `dashboard/
summary` (`app/api/v1/endpoints/dashboard.py`) is left untouched for
backward compatibility; the redesigned dashboard calls these instead.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.dependencies.pagination import pagination_params
from app.models.auth import User
from app.models.enums import FulfillmentStatus, PaymentType
from app.schemas.analytics import (
    AnalyticsSummaryResponse,
    BreakdownsResponse,
    CodDashboardResponse,
    CourierPerformance,
    FulfillmentPaymentBreakdownResponse,
    FulfillmentPaymentClassificationResponse,
    NdrReasonBreakdownResponse,
    OrdersTimeseriesResponse,
    PaymentStatusBreakdownResponse,
    PaymentStatusTimeseriesResponse,
    RecentActivityResponse,
    ReturnsRefundsSummaryResponse,
    RevenueTimeseriesResponse,
    TopProduct,
)
from app.schemas.common import PageParams, build_pagination_meta
from app.schemas.response import ApiResponse, PaginatedResponse
from app.schemas.shipment import ShipmentStatusDrilldownRowResponse
from app.services.analytics_service import AnalyticsService
from app.services.shipment_status import UNIFIED_STATUSES
from app.services.shipment_status_analytics_service import ShipmentStatusAnalyticsService

router = APIRouter()


@router.get("/summary", response_model=ApiResponse[AnalyticsSummaryResponse])
async def get_analytics_summary(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[AnalyticsSummaryResponse]:
    summary = await AnalyticsService(session).get_summary(date_from, date_to)
    return ApiResponse(data=summary)


@router.get("/orders-timeseries", response_model=ApiResponse[OrdersTimeseriesResponse])
async def get_orders_timeseries(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    # "hour" is only used by the dashboard's Today-vs-Yesterday comparison
    # chart (a single IST calendar day has nothing useful to show at
    # day/week/month granularity); every other caller keeps using
    # day/week/month.
    interval: str = Query(default="day", pattern="^(hour|day|week|month)$"),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[OrdersTimeseriesResponse]:
    series = await AnalyticsService(session).get_orders_timeseries(date_from, date_to, interval)
    return ApiResponse(data=series)


@router.get(
    "/payment-status-breakdown", response_model=ApiResponse[PaymentStatusBreakdownResponse]
)
async def get_payment_status_breakdown(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    payment_type: PaymentType | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[PaymentStatusBreakdownResponse]:
    breakdown = await AnalyticsService(session).get_payment_status_breakdown(
        date_from, date_to, payment_type
    )
    return ApiResponse(data=breakdown)


@router.get("/revenue-timeseries", response_model=ApiResponse[RevenueTimeseriesResponse])
async def get_revenue_timeseries(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    interval: str = Query(default="day", pattern="^(day|week|month)$"),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[RevenueTimeseriesResponse]:
    series = await AnalyticsService(session).get_revenue_timeseries(date_from, date_to, interval)
    return ApiResponse(data=series)


@router.get(
    "/payment-status-timeseries", response_model=ApiResponse[PaymentStatusTimeseriesResponse]
)
async def get_payment_status_timeseries(
    payment_type: PaymentType,
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    interval: str = Query(default="day", pattern="^(day|week|month)$"),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[PaymentStatusTimeseriesResponse]:
    series = await AnalyticsService(session).get_payment_status_timeseries(
        date_from, date_to, interval, payment_type
    )
    return ApiResponse(data=series)


@router.get("/breakdowns", response_model=ApiResponse[BreakdownsResponse])
async def get_breakdowns(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[BreakdownsResponse]:
    breakdowns = await AnalyticsService(session).get_breakdowns(date_from, date_to)
    return ApiResponse(data=breakdowns)


@router.get("/top-products", response_model=ApiResponse[list[TopProduct]])
async def get_top_products(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[list[TopProduct]]:
    products = await AnalyticsService(session).get_top_products(date_from, date_to, limit)
    return ApiResponse(data=products)


@router.get("/couriers", response_model=ApiResponse[list[CourierPerformance]])
async def get_courier_performance(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[list[CourierPerformance]]:
    couriers = await AnalyticsService(session).get_courier_performance(date_from, date_to)
    return ApiResponse(data=couriers)


@router.get("/returns-refunds", response_model=ApiResponse[ReturnsRefundsSummaryResponse])
async def get_returns_refunds_summary(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[ReturnsRefundsSummaryResponse]:
    summary = await AnalyticsService(session).get_returns_refunds_summary(date_from, date_to)
    return ApiResponse(data=summary)


@router.get("/recent-activity", response_model=ApiResponse[RecentActivityResponse])
async def get_recent_activity(
    limit: int = Query(default=5, ge=1, le=20),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[RecentActivityResponse]:
    activity = await AnalyticsService(session).get_recent_activity(limit)
    return ApiResponse(data=activity)


@router.get(
    "/fulfillment-payment-breakdown",
    response_model=ApiResponse[FulfillmentPaymentBreakdownResponse],
)
async def get_fulfillment_payment_breakdown(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[FulfillmentPaymentBreakdownResponse]:
    """Fulfilled/Unfulfilled dashboard's parent KPIs: Total/COD/Prepaid
    amount, count and % for Shopify-fulfilled vs Shopify-unfulfilled
    orders (`Order.fulfillment_status`) -- see
    `AnalyticsService.get_fulfillment_payment_breakdown`.
    """
    data = await AnalyticsService(session).get_fulfillment_payment_breakdown(date_from, date_to)
    return ApiResponse(data=FulfillmentPaymentBreakdownResponse(**data))


@router.get(
    "/fulfillment-payment-classification",
    response_model=ApiResponse[FulfillmentPaymentClassificationResponse],
)
async def get_fulfillment_payment_classification(
    fulfillment_status: FulfillmentStatus,
    payment_type: PaymentType,
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[FulfillmentPaymentClassificationResponse]:
    """The "clicking COD or Prepaid expands a detailed classification"
    requirement: shipment-status (Module 2 unified-status) breakdown for
    one Fulfilled/Unfulfilled x COD/Prepaid slice, with order count,
    amount and % of the slice per classification row.
    """
    data = await AnalyticsService(session).get_fulfillment_payment_classification(
        fulfillment_status=fulfillment_status.value,
        payment_type=payment_type.value,
        date_from=date_from,
        date_to=date_to,
    )
    return ApiResponse(data=FulfillmentPaymentClassificationResponse(**data))


@router.get(
    "/fulfillment-payment-ndr-reasons", response_model=ApiResponse[NdrReasonBreakdownResponse]
)
async def get_fulfillment_payment_ndr_reasons(
    fulfillment_status: FulfillmentStatus | None = Query(default=None),
    payment_type: PaymentType | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[NdrReasonBreakdownResponse]:
    """Real NDR reasons for the `ndr` classification row -- the one
    place genuine "why unfulfilled" text exists in this schema.
    """
    data = await AnalyticsService(session).get_ndr_reason_breakdown(
        fulfillment_status=fulfillment_status.value if fulfillment_status else None,
        payment_type=payment_type.value if payment_type else None,
        date_from=date_from,
        date_to=date_to,
    )
    return ApiResponse(data=NdrReasonBreakdownResponse(**data))


@router.get(
    "/fulfillment-payment-orders",
    response_model=PaginatedResponse[ShipmentStatusDrilldownRowResponse],
)
async def get_fulfillment_payment_orders(
    unified_status: str | None = Query(default=None, pattern=f"^({'|'.join(UNIFIED_STATUSES)})$"),
    fulfillment_status: FulfillmentStatus | None = Query(default=None),
    payment_type: PaymentType | None = Query(default=None),
    q: str | None = Query(default=None, max_length=100),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> PaginatedResponse[ShipmentStatusDrilldownRowResponse]:
    """Order-level drill-down behind any Fulfilled/Unfulfilled
    classification row, or any of the COD Orders dashboard's 4 KPIs
    (Total COD = `payment_type=cod` alone; Fulfilled/Unfulfilled COD =
    add `fulfillment_status`; Delivered COD = `payment_type=cod` +
    `unified_status=delivered`). Delegates straight to Module 2's own
    drilldown query/row-shaping (`ShipmentStatusAnalyticsService.
    get_status_drilldown`) so a drill-down here always matches exactly
    the count/amount shown on the card or row that opened it.
    """
    rows, total = await ShipmentStatusAnalyticsService(session).get_status_drilldown(
        unified_status=unified_status,
        fulfillment_status=fulfillment_status.value if fulfillment_status else None,
        payment_type=payment_type.value if payment_type else None,
        q=q,
        date_from=date_from,
        date_to=date_to,
        page_params=page_params,
    )
    return PaginatedResponse(
        data=[ShipmentStatusDrilldownRowResponse(**row) for row in rows],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.get("/cod-dashboard", response_model=ApiResponse[CodDashboardResponse])
async def get_cod_dashboard(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[CodDashboardResponse]:
    """COD Orders dashboard: Total COD / Fulfilled COD / Unfulfilled COD
    / Delivered COD, each with amount, count and %. Drill down into any
    of the 4 via `GET /analytics/fulfillment-payment-orders` with
    `payment_type=cod` (plus `fulfillment_status`/`unified_status` as
    needed).
    """
    data = await AnalyticsService(session).get_cod_dashboard(date_from, date_to)
    return ApiResponse(data=CodDashboardResponse(**data))
