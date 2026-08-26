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
from app.models.auth import User
from app.schemas.analytics import (
    AnalyticsSummaryResponse,
    BreakdownsResponse,
    CourierPerformance,
    OrdersTimeseriesResponse,
    RecentActivityResponse,
    TopProduct,
)
from app.schemas.response import ApiResponse
from app.services.analytics_service import AnalyticsService

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
    interval: str = Query(default="day", pattern="^(day|week|month)$"),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[OrdersTimeseriesResponse]:
    series = await AnalyticsService(session).get_orders_timeseries(date_from, date_to, interval)
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


@router.get("/recent-activity", response_model=ApiResponse[RecentActivityResponse])
async def get_recent_activity(
    limit: int = Query(default=5, ge=1, le=20),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[RecentActivityResponse]:
    activity = await AnalyticsService(session).get_recent_activity(limit)
    return ApiResponse(data=activity)
