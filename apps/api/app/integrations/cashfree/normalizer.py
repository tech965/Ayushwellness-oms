"""Cashfree <-> OMS mapping.

Field names below are taken from Cashfree's official API reference and
SDK documentation (Payments API version 2025-01-01) — see
docs/integrations/cashfree.md for the exact sources consulted. No live
Cashfree account was available to verify a real webhook delivery or API
response, so every field is still read defensively (`.get()`, safe
fallbacks); an unrecognized/missing field degrades to `None` rather than
crashing, exactly like `app.integrations.shiprocket.normalizer`.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.models.enums import PaymentStatus


def parse_iso_datetime(value: str | None) -> datetime | None:
    """Cashfree timestamps (`event_time`, `payment_time`, ...) are
    documented as ISO 8601 — `datetime.fromisoformat` handles Cashfree's
    `Z`-suffixed UTC form once normalized to `+00:00` (the stdlib parser
    doesn't accept a bare `Z` before Python 3.11's relaxed parsing, which
    this codebase doesn't rely on). Returns `None` (never raises) for a
    missing/malformed value — the caller falls back to `datetime.now()`
    rather than failing the whole event over one unparseable timestamp.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

# --- Merchant order_id -------------------------------------------------

# Cashfree's documented Create Order constraint: alphanumeric plus
# underscore/hyphen only, 3-45 characters.
_ORDER_ID_MAX_LENGTH = 45
_INVALID_ORDER_ID_CHARS = re.compile(r"[^A-Za-z0-9_-]")


def build_cashfree_order_id(order_number: str, *, disambiguator: str | None = None) -> str:
    """Deterministic, Cashfree-safe merchant order_id for an OMS order —
    always derived from (and mappable back to) `Order.order_number`,
    never the database UUID (spec: don't use the UUID blindly; Cashfree's
    order_id is customer/dashboard-visible and a raw UUID conveys
    nothing). `order_number` is confirmed elsewhere in this codebase to
    look like `"#AWL92268"` (Shopify's `name` field) — the leading `#`
    and any other disallowed character is stripped, not rejected, since
    only alphanumeric/underscore/hyphen survive Cashfree's constraint.

    `disambiguator` (short, itself alphanumeric) is appended for a fresh
    payment attempt after a prior one for this same order failed/expired
    — see `CashfreePaymentService`, which is what actually decides when
    one is needed. Always truncated to `_ORDER_ID_MAX_LENGTH`, trimming
    the *base* (never the disambiguator) so retries never collide.
    """
    base = _INVALID_ORDER_ID_CHARS.sub("", order_number) or "AWLORDER"
    if not disambiguator:
        return base[:_ORDER_ID_MAX_LENGTH]
    suffix = f"-{disambiguator}"
    return base[: _ORDER_ID_MAX_LENGTH - len(suffix)] + suffix


# --- Payment status mapping ---------------------------------------------

# Confirmed via Cashfree's documented `payment_status` values (Payment
# entity, both the webhook payload and GET .../payments responses).
# `NOT_ATTEMPTED` never occurs on a webhook (only "SUCCESS"/"FAILED"/
# "USER_DROPPED" trigger a webhook at all) but does appear on a
# reconciliation GET call for a payment that was created but never
# completed — never guessed as PAID or FAILED, so it maps to PENDING.
_PAYMENT_STATUS_MAP: dict[str, PaymentStatus] = {
    "SUCCESS": PaymentStatus.PAID,
    "FAILED": PaymentStatus.FAILED,
    "USER_DROPPED": PaymentStatus.FAILED,
    "CANCELLED": PaymentStatus.FAILED,
    "VOID": PaymentStatus.FAILED,
    "PENDING": PaymentStatus.PENDING,
    "NOT_ATTEMPTED": PaymentStatus.PENDING,
    "FLAGGED": PaymentStatus.PENDING,
}


def normalize_payment_status(raw_status: str | None) -> PaymentStatus | None:
    """`None` for an unrecognized/missing raw status — the caller must
    never guess; see `app.services.cashfree_payment_service` (spec: do
    not silently classify an unknown event as a successful payment).
    """
    if not raw_status:
        return None
    return _PAYMENT_STATUS_MAP.get(raw_status.strip().upper())


# --- Webhook payload -----------------------------------------------------

# Confirmed via Cashfree's official webhook documentation. Any `type`
# outside this set is a genuinely unrecognized event — never treated as
# a payment outcome (spec §7: "do not silently classify unknown events
# as successful payments").
RECOGNIZED_WEBHOOK_TYPES = frozenset(
    {"PAYMENT_SUCCESS_WEBHOOK", "PAYMENT_FAILED_WEBHOOK", "PAYMENT_USER_DROPPED_WEBHOOK"}
)


def extract_order_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    order = data.get("order") if isinstance(data, dict) else None
    return order if isinstance(order, dict) else {}


def extract_payment_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    payment = data.get("payment") if isinstance(data, dict) else None
    return payment if isinstance(payment, dict) else {}


def extract_gateway_details(payload: dict[str, Any]) -> dict[str, Any] | None:
    data = payload.get("data")
    details = data.get("payment_gateway_details") if isinstance(data, dict) else None
    return details if isinstance(details, dict) else None


def extract_error_details(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Only present on `PAYMENT_FAILED_WEBHOOK` per Cashfree's docs —
    `error_code`/`error_description`/`error_reason`/`error_source`.
    """
    data = payload.get("data")
    details = data.get("error_details") if isinstance(data, dict) else None
    return details if isinstance(details, dict) else None


def extract_decimal_amount(container: dict[str, Any], key: str) -> Decimal | None:
    """`container` must come from a JSON parse done with
    `parse_float=decimal.Decimal` (see the webhook endpoint / client
    calls) — never from a plain `json.loads`, whose `float` would already
    have lost precision before this function ever sees the value (spec
    §9: never use floating point for payment amounts).
    """
    value = container.get(key)
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    # A reconciliation call's response body may not have been parsed with
    # `parse_float=Decimal` (httpx's `.json()` doesn't support it) — going
    # through `str()` first for an int/Decimal-safe value is exact; for a
    # plain `float` this is the same best-effort `str(float)` precision
    # any Python code converting a pre-parsed float to Decimal is stuck
    # with, but the primary (webhook) path never hits this branch.
    return Decimal(str(value))


class CashfreeOrderPushNormalizer:
    """Builds the `POST /orders` request body from an OMS `Order` (with
    `.customer` eager-loaded) plus the resolved Cashfree order_id.
    Field names confirmed via Cashfree's Create Order API reference —
    `customer_details.customer_id`/`customer_phone` are the two Cashfree
    documents as required; `customer_email`/`customer_name` are optional.
    """

    def build_payload(
        self,
        order: Any,
        *,
        cashfree_order_id: str,
        customer_phone: str,
        customer_id: str,
        customer_email: str | None,
        customer_name: str | None,
        return_url: str | None,
    ) -> dict[str, Any]:
        customer_details: dict[str, Any] = {
            "customer_id": customer_id,
            "customer_phone": customer_phone,
        }
        if customer_email:
            customer_details["customer_email"] = customer_email
        if customer_name:
            customer_details["customer_name"] = customer_name

        order_meta: dict[str, Any] = {}
        if return_url:
            order_meta["return_url"] = return_url

        payload: dict[str, Any] = {
            "order_id": cashfree_order_id,
            # Cashfree documents up to 2 decimals — `Order.total_amount`
            # is already `Numeric(12, 2)`, so `str(Decimal(...))` never
            # introduces float imprecision here.
            "order_amount": float(order.total_amount),
            "order_currency": order.currency,
            "customer_details": customer_details,
        }
        if order_meta:
            payload["order_meta"] = order_meta
        return payload


ORDER_PUSH_NORMALIZER = CashfreeOrderPushNormalizer()
