"""Marketplaces > Flipkart > Orders (read-only, live from the Seller API)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from starlette.responses import StreamingResponse

from app.dependencies.auth import require_permission
from app.models.auth import User
from app.schemas.flipkart_sales import FlipkartOrdersPage, FlipkartStatus
from app.schemas.response import ApiResponse
from app.services.flipkart_export import page_to_xlsx
from app.services.flipkart_sales_service import FlipkartSalesService

router = APIRouter()
_perm = require_permission("analytics.read")


def get_service() -> FlipkartSalesService:
    return FlipkartSalesService()


@router.get("/status", response_model=ApiResponse[FlipkartStatus])
async def status(svc: FlipkartSalesService = Depends(get_service), _: User = Depends(_perm)):
    return ApiResponse(data=svc.status())


@router.get("/orders", response_model=ApiResponse[FlipkartOrdersPage])
async def orders(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    status: str | None = Query(default=None),
    page_size: int = Query(default=20, ge=1, le=20),
    next_page_url: str | None = Query(default=None),
    svc: FlipkartSalesService = Depends(get_service),
    _: User = Depends(_perm),
):
    return ApiResponse(
        data=await svc.orders_page(
            date_from=date_from,
            date_to=date_to,
            status=status,
            page_size=page_size,
            next_page_url=next_page_url,
        )
    )


@router.get("/orders/export")
async def orders_export(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    status: str | None = Query(default=None),
    svc: FlipkartSalesService = Depends(get_service),
    _: User = Depends(_perm),
) -> StreamingResponse:
    page = await svc.all_orders_for_export(date_from=date_from, date_to=date_to, status=status)
    name = f"flipkart-orders-{date_from or 'all'}-{date_to or 'all'}.xlsx"
    return StreamingResponse(
        iter([page_to_xlsx(page)]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={name}"},
    )
