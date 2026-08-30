"""Cashfree checkout/payment-status endpoints.

    POST /api/v1/payments/cashfree/orders/{order_id}/create
    GET  /api/v1/payments/cashfree/orders/{order_id}
    POST /api/v1/payments/cashfree/orders/{order_id}/reconcile

`create` is the only endpoint that ever calls Cashfree's Create Order
API — the order amount always comes from the server-side OMS `Order`,
never a request body (spec: never trust an amount supplied by the
browser; this endpoint takes no body at all). `reconcile` is an
authenticated, on-demand fallback for a delayed/missed webhook (spec
§13) — never a replacement for the webhook, and never automatically
polled from here.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.integrations.cashfree.config import CashfreeConfig
from app.models.auth import User
from app.schemas.cashfree import (
    CashfreeCheckoutResponse,
    CashfreePaymentStatusResponse,
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
