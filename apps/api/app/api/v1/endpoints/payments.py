"""Read-only — see `app.services.payment_service` docstring."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.dependencies.pagination import pagination_params
from app.dependencies.pagination import sort_params as sort_params_dep
from app.models.auth import User
from app.models.enums import PaymentStatus
from app.schemas.cashfree import (
    CashfreePaymentMethodBreakdownResponse,
    CashfreePaymentOverviewResponse,
    CashfreePaymentTrendResponse,
)
from app.schemas.common import PageParams, SortParams, build_pagination_meta
from app.schemas.payment import (
    PaymentDetailResponse,
    PaymentResponse,
    build_payment_detail_response,
    build_payment_response,
)
from app.schemas.response import ApiResponse, PaginatedResponse
from app.services.payment_service import PaymentService

router = APIRouter()


def _payment_filters(
    q: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    status: PaymentStatus | None = Query(default=None),
    payment_method: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
) -> dict:
    """Shared filter set for `list_payments` and `export_payments` — the
    same "one shared dependency so the two routes can never drift apart"
    convention `app.api.v1.endpoints.orders._order_filters` already uses.
    Typed against the real `PaymentStatus` enum (not a plain `str`) so a
    typo'd status is rejected with a 422 at the API boundary instead of
    silently matching zero rows.
    """
    return {
        "q": q,
        "provider": provider,
        "status": status,
        "payment_method": payment_method,
        "date_from": date_from,
        "date_to": date_to,
    }


@router.get("", response_model=PaginatedResponse[PaymentResponse])
async def list_payments(
    order_id: uuid.UUID | None = Query(default=None),
    filters: dict = Depends(_payment_filters),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("payments.read")),
) -> PaginatedResponse[PaymentResponse]:
    items, total = await PaymentService(session).list_payments(
        page_params=page_params, sort_params=sort_params, order_id=order_id, **filters
    )
    return PaginatedResponse(
        data=[build_payment_response(p) for p in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.get("/export")
async def export_payments(
    filters: dict = Depends(_payment_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("payments.read")),
) -> StreamingResponse:
    """Streams the filtered payments (no pagination — same filters as
    `list_payments`, capped at `ExportService.MAX_ROWS` rows) as a real
    `.xlsx` workbook. Read-only, same permission as viewing the list —
    matches `GET /orders/export`'s `orders.read`-only gating.
    """
    workbook = await PaymentService(session).export_payments(filters)
    return StreamingResponse(
        iter([workbook]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=payments-export.xlsx"},
    )


@router.get("/analytics/overview", response_model=ApiResponse[CashfreePaymentOverviewResponse])
async def get_payment_overview(
    provider: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("payments.read")),
) -> ApiResponse[CashfreePaymentOverviewResponse]:
    """Provider-agnostic sibling of `GET /payments/cashfree/analytics/
    overview` (untouched) — `provider=None` (or omitted) means every
    provider, exactly like the payments table above it on the dashboard.
    """
    overview = await PaymentService(session).get_payment_overview(date_from, date_to, provider)
    return ApiResponse(data=overview)


@router.get("/analytics/trend", response_model=ApiResponse[CashfreePaymentTrendResponse])
async def get_payment_trend(
    provider: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    interval: str = Query(default="day", pattern="^(day|week|month)$"),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("payments.read")),
) -> ApiResponse[CashfreePaymentTrendResponse]:
    trend = await PaymentService(session).get_payment_trend(
        date_from, date_to, interval, provider
    )
    return ApiResponse(data=trend)


@router.get(
    "/analytics/method-breakdown",
    response_model=ApiResponse[CashfreePaymentMethodBreakdownResponse],
)
async def get_payment_method_breakdown(
    provider: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("payments.read")),
) -> ApiResponse[CashfreePaymentMethodBreakdownResponse]:
    breakdown = await PaymentService(session).get_payment_method_breakdown(
        date_from, date_to, provider
    )
    return ApiResponse(data=breakdown)


@router.get("/{payment_id}", response_model=ApiResponse[PaymentDetailResponse])
async def get_payment(
    payment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("payments.read")),
) -> ApiResponse[PaymentDetailResponse]:
    payment = await PaymentService(session).get_payment(payment_id)
    return ApiResponse(data=build_payment_detail_response(payment))
