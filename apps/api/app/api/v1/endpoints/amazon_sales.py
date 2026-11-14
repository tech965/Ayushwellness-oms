"""Marketplaces > Amazon > Sales Analytics (read-only, live from SP-API)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from starlette.responses import StreamingResponse

from app.dependencies.auth import require_permission
from app.models.auth import User
from app.schemas.amazon_sales import (
    AccountSalesReport,
    AmazonStatus,
    ChunkRef,
    DailyPoint,
    DayOrdersReport,
    FetchRequest,
    PollRequest,
    PollResponse,
    SalesReport,
)
from app.schemas.response import ApiResponse
from app.services.amazon_export import report_to_xlsx
from app.services.amazon_sales_service import AmazonSalesService

router = APIRouter()
_perm = require_permission("analytics.read")


def get_service() -> AmazonSalesService:
    return AmazonSalesService()


@router.get("/status", response_model=ApiResponse[AmazonStatus])
async def status(svc: AmazonSalesService = Depends(get_service), _: User = Depends(_perm)):
    return ApiResponse(data=svc.status())


@router.post("/reports", response_model=ApiResponse[list[ChunkRef]])
async def start_reports(
    body: FetchRequest, svc: AmazonSalesService = Depends(get_service), _: User = Depends(_perm)
):
    """Requests the Amazon report(s) for the range; returns provider handles."""
    return ApiResponse(data=await svc.start_fetch(body.date_from, body.date_to))


@router.post("/reports/poll", response_model=ApiResponse[PollResponse])
async def poll_reports(
    body: PollRequest, svc: AmazonSalesService = Depends(get_service), _: User = Depends(_perm)
):
    return ApiResponse(data=await svc.poll(body.chunks, body.q))


@router.post("/reports/export")
async def export_reports(
    body: PollRequest, svc: AmazonSalesService = Depends(get_service), _: User = Depends(_perm)
) -> StreamingResponse:
    report = await svc.full_report(body.chunks)
    name = f"amazon-sales-{report.date_from}-{report.date_to}.xlsx"
    return StreamingResponse(
        iter([report_to_xlsx(report)]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={name}"},
    )


@router.get("/account", response_model=ApiResponse[AccountSalesReport])
async def account_report(
    date_from: date,
    date_to: date,
    svc: AmazonSalesService = Depends(get_service),
    _: User = Depends(_perm),
):
    """Account-level ordered units/revenue (Sales API, works independently
    of the Reports API). Not product-wise -- see `AccountSalesReport.
    scope_note`.
    """
    return ApiResponse(data=await svc.account_report(date_from, date_to))


@router.get("/orders-product-report", response_model=ApiResponse[SalesReport])
async def orders_product_report(
    date_from: date,
    date_to: date,
    svc: AmazonSalesService = Depends(get_service),
    _: User = Depends(_perm),
):
    """Product-wise sales aggregated from the Orders API -- an
    alternative to the Reports API while it's blocked on authorization.
    See the response's `warnings` for why totals differ from the other
    two data sources.
    """
    return ApiResponse(data=await svc.orders_api_product_report(date_from, date_to))


@router.get("/orders-by-date", response_model=ApiResponse[DayOrdersReport])
async def orders_by_date(
    date: date,
    svc: AmazonSalesService = Depends(get_service),
    _: User = Depends(_perm),
):
    """Order-level drill-down for one day: product line items + COD/
    Prepaid payment method only -- no buyer/address fields. Used by the
    product-wise views' click-through.
    """
    return ApiResponse(data=await svc.orders_for_date(date))


@router.get("/product-trend", response_model=ApiResponse[list[DailyPoint]])
async def product_trend(
    date_from: date,
    date_to: date,
    sku: str | None = Query(default=None),
    asin: str | None = Query(default=None),
    svc: AmazonSalesService = Depends(get_service),
    _: User = Depends(_perm),
):
    return ApiResponse(data=await svc.product_trend(date_from, date_to, sku, asin))
