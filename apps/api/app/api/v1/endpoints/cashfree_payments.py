"""Cashfree checkout/payment-status/dashboard endpoints.

    POST /api/v1/payments/cashfree/orders/{order_id}/create
    GET  /api/v1/payments/cashfree/orders/{order_id}
    POST /api/v1/payments/cashfree/orders/{order_id}/reconcile
    GET  /api/v1/payments/cashfree/status
    POST /api/v1/payments/cashfree/status/test-connection
    GET  /api/v1/payments/cashfree/analytics/overview
    GET  /api/v1/payments/cashfree/analytics/trend
    GET  /api/v1/payments/cashfree/analytics/method-breakdown

`create` is the only endpoint that ever calls Cashfree's Create Order
API — the order amount always comes from the server-side OMS `Order`,
never a request body (spec: never trust an amount supplied by the
browser; this endpoint takes no body at all). `reconcile` is an
authenticated, on-demand fallback for a delayed/missed webhook (spec
§13) — never a replacement for the webhook, and never automatically
polled from here. `status`/`test-connection`/`analytics/*` back the
Cashfree payments dashboard (`/payments`); see
`app.services.cashfree_payment_service.CashfreePaymentService`'s
"Connection status"/"Payment analytics" sections for what each reuses.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.integrations.cashfree.config import CashfreeConfig
from app.models.auth import User
from app.schemas.cashfree import (
    CashfreeCheckoutResponse,
    CashfreeConnectionTestResponse,
    CashfreePaymentMethodBreakdownResponse,
    CashfreePaymentOverviewResponse,
    CashfreePaymentStatusResponse,
    CashfreePaymentTrendResponse,
    CashfreeStatusResponse,
    build_checkout_response,
    build_status_response,
)
from app.schemas.response import ApiResponse
from app.services.cashfree_payment_service import CashfreePaymentService

router = APIRouter()


@router.post("/orders/{order_id}/create", response_model=ApiResponse[CashfreeCheckoutResponse])
async def create_cashfree_checkout(
    order_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("payments.create")),
) -> ApiResponse[CashfreeCheckoutResponse]:
    payment, created = await CashfreePaymentService(session).create_or_reuse_checkout(
        order_id, actor=current_user
    )
    config = CashfreeConfig.from_settings()
    mode = config.environment if config is not None else "sandbox"
    return ApiResponse(
        data=build_checkout_response(payment, created=created, mode=mode),
        message="Cashfree checkout session ready." if created else "Existing session reused.",
    )


@router.get("/orders/{order_id}", response_model=ApiResponse[CashfreePaymentStatusResponse])
async def get_cashfree_payment_status(
    order_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("payments.read")),
) -> ApiResponse[CashfreePaymentStatusResponse]:
    payment = await CashfreePaymentService(session).get_payment_for_order(order_id)
    return ApiResponse(data=build_status_response(payment))


@router.post(
    "/orders/{order_id}/reconcile", response_model=ApiResponse[CashfreePaymentStatusResponse]
)
async def reconcile_cashfree_payment(
    order_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("payments.create")),
) -> ApiResponse[CashfreePaymentStatusResponse]:
    payment = await CashfreePaymentService(session).reconcile_payment(order_id, actor=current_user)
    return ApiResponse(
        data=build_status_response(payment), message="Reconciled against Cashfree."
    )


@router.get("/status", response_model=ApiResponse[CashfreeStatusResponse])
async def get_cashfree_status(
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("payments.read")),
) -> ApiResponse[CashfreeStatusResponse]:
    """Config snapshot only — configured/environment/api_url/api_version.
    No network call, safe to call on every dashboard load. Never the
    client secret/webhook secret.
    """
    return ApiResponse(data=CashfreePaymentService(session).get_status())


@router.post(
    "/status/test-connection", response_model=ApiResponse[CashfreeConnectionTestResponse]
)
async def test_cashfree_connection(
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("integrations.test")),
) -> ApiResponse[CashfreeConnectionTestResponse]:
    """On-demand, read-only probe against the real Cashfree API via the
    existing, unmodified `CashfreeClient` — never creates/modifies
    anything. Gated by `integrations.test` (the same permission
    Shopify/Shiprocket's "Test Connection" already uses), not
    `payments.read`, since this makes a live outbound call.
    """
    result = await CashfreePaymentService(session).test_connection()
    return ApiResponse(
        data=result,
        message="Cashfree reachable." if result.connected else "Cashfree connection test failed.",
    )


@router.get("/analytics/overview", response_model=ApiResponse[CashfreePaymentOverviewResponse])
async def get_cashfree_payment_overview(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("payments.read")),
) -> ApiResponse[CashfreePaymentOverviewResponse]:
    overview = await CashfreePaymentService(session).get_payment_overview(date_from, date_to)
    return ApiResponse(data=overview)


@router.get("/analytics/trend", response_model=ApiResponse[CashfreePaymentTrendResponse])
async def get_cashfree_payment_trend(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    interval: str = Query(default="day", pattern="^(day|week|month)$"),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("payments.read")),
) -> ApiResponse[CashfreePaymentTrendResponse]:
    trend = await CashfreePaymentService(session).get_payment_trend(date_from, date_to, interval)
    return ApiResponse(data=trend)


@router.get(
    "/analytics/method-breakdown",
    response_model=ApiResponse[CashfreePaymentMethodBreakdownResponse],
)
async def get_cashfree_payment_method_breakdown(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("payments.read")),
) -> ApiResponse[CashfreePaymentMethodBreakdownResponse]:
    breakdown = await CashfreePaymentService(session).get_payment_method_breakdown(
        date_from, date_to
    )
    return ApiResponse(data=breakdown)
