from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import PaymentStatus
from app.models.payment import Payment
from app.schemas.analytics import KPIValue, StatusCount


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


# --- Connection status (config snapshot + on-demand live probe) -----------
# Deliberately separate from `app.schemas.integration.IntegrationHealthResponse`
# — Cashfree has no registered `IntegrationAdapter` (it's a payment
# gateway, not a pull-sync provider; see `app.integrations.bootstrap`), so
# the generic `/integrations/{id}/health-check` always reports "no adapter
# registered" for it. These reuse `CashfreeConfig`/`CashfreeClient`
# directly instead.


class CashfreeStatusResponse(BaseModel):
    """Pure config snapshot — never makes a network call, so it's safe to
    call on every dashboard page load. Never the client secret/webhook
    secret.
    """

    configured: bool
    environment: str  # "sandbox" | "production" | "not_configured"
    api_url: str | None
    api_version: str | None


class CashfreeConnectionTestResponse(BaseModel):
    """One on-demand, read-only Cashfree API call
    (`CashfreeClient.get_order` against a sentinel id that can never
    exist) — the exact probe already verified safe from a Render shell:
    a `not_found`/404 proves the credentials and API URL are both good;
    an `authentication_error`/401 means the credentials are rejected.
    Never the client secret/webhook secret/any token.
    """

    configured: bool
    connected: bool
    environment: str
    error_type: str | None
    status_code: int | None
    checked_at: datetime


# --- Payment analytics (Cashfree-scoped: Payment.provider == "cashfree") --


class CashfreePaymentOverviewResponse(BaseModel):
    date_from: datetime
    date_to: datetime
    total_payments: KPIValue
    paid_payments: KPIValue
    pending_payments: KPIValue
    failed_payments: KPIValue
    refunded_payments: KPIValue
    total_amount: KPIValue
    pending_amount: KPIValue
    status_breakdown: list[StatusCount]


class CashfreePaymentTrendPoint(BaseModel):
    bucket: str
    total_count: int
    total_amount: Decimal
    paid_count: int
    paid_amount: Decimal
    pending_count: int
    failed_count: int


class CashfreePaymentTrendResponse(BaseModel):
    interval: str
    points: list[CashfreePaymentTrendPoint]


class CashfreePaymentMethodBreakdownItem(BaseModel):
    payment_method: str
    count: int
    amount: Decimal


class CashfreePaymentMethodBreakdownResponse(BaseModel):
    items: list[CashfreePaymentMethodBreakdownItem]


# --- Transaction sync (POST /payments/cashfree/sync) ----------------------


class CashfreeSyncRequest(BaseModel):
    date_from: datetime
    date_to: datetime


class CashfreeSyncResult(BaseModel):
    """Outcome of one `sync_transactions`/`sync_settlements` call —
    always returned, never silently swallowed, so the operator sees
    exactly what happened (spec §16/§17: "do not silently refresh
    without telling the operator what happened").
    """

    fetched: int = 0
    processed: int = 0
    # A row that genuinely wrote new/changed state -- a new Payment/
    # Settlement created, or an existing one's status legitimately
    # advanced. Named "applied" (not "created"/"updated") because
    # `CashfreePaymentService.apply_payment_event` -- reused unchanged --
    # doesn't itself distinguish the two; see that method's own
    # `PaymentEventResult.applied`.
    applied: int = 0
    duplicates: int = 0
    skipped: int = 0
    failures: int = 0
    errors: list[str] = Field(default_factory=list)


# --- Settlements (POST /payments/cashfree/settlements/sync,
# GET /payments/cashfree/analytics/settlements) ----------------------------


class CashfreeSettlementItem(BaseModel):
    cf_settlement_id: str
    status: str | None
    settlement_utr: str | None
    settlement_processed_on: datetime | None
    # Gross transaction amount this settlement covers -- distinct from
    # `amount_settled` below (spec: never equate the two; the difference
    # is PG service charge/tax/adjustments, shown explicitly here).
    payment_amount: Decimal | None
    amount_settled: Decimal | None


class CashfreeSettlementSummaryResponse(BaseModel):
    """Backs the Payments page's "Cashfree Settlement" section. Every
    field documented below as DERIVED is computed from the locally
    synced `cashfree_settlements` table (`POST /settlements`), not
    returned by a confirmed dedicated Cashfree endpoint — see
    `app.services.cashfree_sync_service`'s module docstring for exactly
    why, and never presented to a caller as if it were Cashfree-native.
    """

    # DERIVED: sum of `amount_settled` (or `payment_amount` when a
    # pending settlement has no `amount_settled` yet) across every
    # non-terminal (PENDING/PENDING_WITH_CASHFREE/PENDING_WITH_BANK)
    # settlement currently on record.
    unsettled_amount: Decimal
    # DERIVED: the nearest non-terminal settlement's own gross amount —
    # a best-effort stand-in for Cashfree's own "next settlement"
    # concept, not a dedicated Cashfree field.
    upcoming_settlement_amount: Decimal | None
    upcoming_settlement_status: str | None
    # From the most recent settlement Cashfree has actually completed
    # (status="SUCCESS") -- these ARE the real, Cashfree-reported values
    # for that one settlement, not derived.
    last_settled_amount: Decimal | None
    last_settled_date: datetime | None
    last_settlement_utr: str | None
    last_settlement_status: str | None
    history: list[CashfreeSettlementItem]
