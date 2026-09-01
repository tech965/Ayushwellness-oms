from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.enums import PaymentStatus, PaymentType
from app.models.payment import Payment, PaymentTransaction


class PaymentTransactionResponse(BaseModel):
    """One gateway callback/lookup applied to a `Payment` (append-only —
    see `app.models.payment.PaymentTransaction`). `error_reason`/
    `event_type`/`payment_method` are extracted from `raw_payload`, which
    is already sanitized before storage (never `customer_details` — see
    `app.api.v1.webhooks.cashfree.receive_cashfree_payment_webhook`), so
    surfacing them here duplicates no PII beyond what the transaction row
    already legitimately holds.
    """

    model_config = ConfigDict(from_attributes=False)

    id: uuid.UUID
    payment_id: uuid.UUID
    gateway: str | None
    gateway_transaction_id: str | None
    status: PaymentStatus
    amount: Decimal
    created_at: datetime
    event_type: str | None
    payment_method: str | None
    error_reason: str | None


class PaymentResponse(BaseModel):
    """Provider-agnostic — a COD/manually-recorded payment and a Cashfree
    payment both serialize through this shape. `external_id`/
    `payment_session_id`/`payment_method` are only ever populated for a
    gateway-backed payment (Cashfree today); `None` for anything else.
    `order_number`/`customer_*` are denormalized from the eager-loaded
    `Payment.order`/`Order.customer` (see
    `PaymentRepository._WITH_ORDER_AND_CUSTOMER`) so a list of payments
    never needs a per-row follow-up call, matching
    `app.schemas.order.OrderListResponse`'s existing convention.
    """

    model_config = ConfigDict(from_attributes=False)

    id: uuid.UUID
    order_id: uuid.UUID
    order_number: str | None
    customer_name: str | None
    customer_phone: str | None
    customer_email: str | None
    payment_type: PaymentType
    status: PaymentStatus
    amount: Decimal
    currency: str
    provider: str | None
    source_system: str | None
    # The gateway's own order identifier (Cashfree: `cashfree_order_id`) —
    # kept as the generic `external_id` name `SyncMetadataMixin` already
    # uses everywhere else, rather than inventing a Cashfree-only field on
    # a schema every payment (including non-Cashfree ones) serializes
    # through. The frontend labels this "Cashfree Order ID" only when
    # `provider == "cashfree"`.
    external_id: str | None
    external_transaction_id: str | None
    payment_session_id: str | None
    payment_method: str | None
    paid_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PaymentDetailResponse(PaymentResponse):
    transactions: list[PaymentTransactionResponse] = []


def _metadata(payment: Payment) -> dict[str, Any]:
    return payment.payment_metadata or {}


def build_payment_response(payment: Payment) -> PaymentResponse:
    """`payment.order`/`payment.order.customer` must already be loaded
    (see `PaymentRepository.search_query`/`get_by_id_with_transactions`)
    — a lazy load here would raise `MissingGreenlet` under `AsyncSession`.
    """
    metadata = _metadata(payment)
    order = payment.order
    customer = order.customer if order is not None else None
    return PaymentResponse(
        id=payment.id,
        order_id=payment.order_id,
        order_number=order.order_number if order is not None else None,
        customer_name=customer.full_name if customer is not None else None,
        customer_phone=customer.phone if customer is not None else None,
        customer_email=customer.email if customer is not None else None,
        payment_type=payment.payment_type,
        status=payment.status,
        amount=payment.amount,
        currency=payment.currency,
        provider=payment.provider,
        source_system=payment.source_system,
        external_id=payment.external_id,
        external_transaction_id=payment.external_transaction_id,
        payment_session_id=metadata.get("payment_session_id"),
        payment_method=metadata.get("payment_method"),
        paid_at=payment.paid_at,
        created_at=payment.created_at,
        updated_at=payment.updated_at,
    )


def _extract_error_reason(raw_payload: dict[str, Any] | None) -> str | None:
    if not isinstance(raw_payload, dict):
        return None
    error_details = raw_payload.get("error_details")
    if not isinstance(error_details, dict):
        return None
    return (
        error_details.get("error_description")
        or error_details.get("error_reason")
        or error_details.get("error_code")
    )


def _extract_transaction_payment_method(raw_payload: dict[str, Any] | None) -> str | None:
    if not isinstance(raw_payload, dict):
        return None
    payment_block = raw_payload.get("payment")
    if not isinstance(payment_block, dict):
        return None
    method = payment_block.get("payment_method")
    return method if isinstance(method, str) else None


def build_transaction_response(transaction: PaymentTransaction) -> PaymentTransactionResponse:
    raw_payload = transaction.raw_payload if isinstance(transaction.raw_payload, dict) else None
    return PaymentTransactionResponse(
        id=transaction.id,
        payment_id=transaction.payment_id,
        gateway=transaction.gateway,
        gateway_transaction_id=transaction.gateway_transaction_id,
        status=transaction.status,
        amount=transaction.amount,
        created_at=transaction.created_at,
        event_type=raw_payload.get("type") if raw_payload else None,
        payment_method=_extract_transaction_payment_method(raw_payload),
        error_reason=_extract_error_reason(raw_payload),
    )


def build_payment_detail_response(payment: Payment) -> PaymentDetailResponse:
    base = build_payment_response(payment)
    return PaymentDetailResponse(
        **base.model_dump(),
        transactions=[build_transaction_response(t) for t in payment.transactions],
    )
