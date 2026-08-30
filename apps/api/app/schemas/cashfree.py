from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.enums import PaymentStatus
from app.models.payment import Payment


class CashfreeCheckoutResponse(BaseModel):
    """Everything the frontend needs to open Cashfree Checkout —
    deliberately never the client secret/webhook secret (spec §12/§14).
    """

    model_config = ConfigDict(from_attributes=False)

    payment_id: uuid.UUID
    order_id: uuid.UUID
    cashfree_order_id: str
    payment_session_id: str | None
    status: PaymentStatus
    amount: Decimal
    currency: str
    created: bool
    # "sandbox" or "production" — which Cashfree environment this session
    # belongs to, so the frontend initializes the Checkout JS SDK with a
    # matching mode (see app.integrations.cashfree.config.CashfreeConfig
    # .environment). Never a secret; purely a routing hint.
    mode: str


class CashfreePaymentStatusResponse(BaseModel):
    """OMS-safe payment status snapshot (spec §12) — provider,
    provider_order_id, status, amount, currency, payment method,
    timestamps. Never the client secret/webhook secret/raw auth headers.
    """

    model_config = ConfigDict(from_attributes=False)

    payment_id: uuid.UUID
    order_id: uuid.UUID
    provider: str | None
    cashfree_order_id: str | None
    payment_session_id: str | None
    status: PaymentStatus
    amount: Decimal
    currency: str
    payment_method: str | None
    created_at: datetime
    updated_at: datetime
    paid_at: datetime | None


def _metadata(payment: Payment) -> dict:
    return payment.payment_metadata or {}


def build_checkout_response(
    payment: Payment, *, created: bool, mode: str
) -> CashfreeCheckoutResponse:
    metadata = _metadata(payment)
    return CashfreeCheckoutResponse(
        payment_id=payment.id,
        order_id=payment.order_id,
        cashfree_order_id=payment.external_id or metadata.get("cashfree_order_id", ""),
        payment_session_id=metadata.get("payment_session_id"),
        status=payment.status,
        amount=payment.amount,
        currency=payment.currency,
        created=created,
        mode=mode,
    )


def build_status_response(payment: Payment) -> CashfreePaymentStatusResponse:
    metadata = _metadata(payment)
    return CashfreePaymentStatusResponse(
        payment_id=payment.id,
        order_id=payment.order_id,
        provider=payment.provider,
        cashfree_order_id=payment.external_id,
        payment_session_id=metadata.get("payment_session_id"),
        status=payment.status,
        amount=payment.amount,
        currency=payment.currency,
        payment_method=metadata.get("payment_method"),
        created_at=payment.created_at,
        updated_at=payment.updated_at,
        paid_at=payment.paid_at,
    )
