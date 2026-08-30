"""Cashfree Payment Gateway webhook endpoint.

    POST /api/v1/webhooks/cashfree/payment

Verifies `x-webhook-signature` (HMAC-SHA256 of
`x-webhook-timestamp + RAW_REQUEST_BODY`, base64-encoded, keyed by the
Cashfree client secret — see `app.integrations.cashfree.webhooks`)
against the *raw* bytes Cashfree sent, BEFORE any JSON parsing, then
hands the parsed payload to the generic `WebhookService` for idempotent
ingestion (the same mechanism Shopify's/Shiprocket's webhooks use)
before applying it via `CashfreePaymentService.apply_payment_event` —
the one place a Cashfree result is ever written to OMS `Payment`/`Order`
state. The frontend is never involved in this path (spec: never mark an
order paid from anything but a verified Cashfree webhook/reconciliation
result).

Always acks with 200 once the signature is valid and the payload is
well-formed — including when the event can't be safely applied (unknown
order, amount mismatch, already paid, unrecognized event type; the
`WebhookEvent` is marked IGNORED with the reason, never fabricated) — so
Cashfree never retries a delivery this endpoint already understood. Only
a genuine processing failure (e.g. a database error) returns 5xx.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.integrations.cashfree.normalizer import (
    RECOGNIZED_WEBHOOK_TYPES,
    extract_decimal_amount,
    extract_error_details,
    extract_gateway_details,
    extract_order_data,
    extract_payment_data,
    parse_iso_datetime,
)
from app.integrations.cashfree.webhooks import resolve_webhook_secret, verify_webhook_signature
from app.models.integration import IntegrationCode
from app.repositories.integration import IntegrationRepository
from app.services.cashfree_payment_service import CashfreePaymentService
from app.services.webhook_service import WebhookService

router = APIRouter()
logger = get_logger(__name__)


@router.post("/payment", status_code=200)
async def receive_cashfree_payment_webhook(
    request: Request,
    x_webhook_signature: str | None = Header(default=None, alias="x-webhook-signature"),
    x_webhook_timestamp: str | None = Header(default=None, alias="x-webhook-timestamp"),
    x_webhook_version: str | None = Header(default=None, alias="x-webhook-version"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    # CRITICAL: read the raw body before any JSON parsing — signature
    # verification MUST run against these exact bytes (spec: never
    # reconstruct/reserialize the payload for verification).
    raw_body = await request.body()

    secret = resolve_webhook_secret(
        client_secret=settings.CASHFREE_CLIENT_SECRET,
        webhook_secret=settings.CASHFREE_WEBHOOK_SECRET,
    )
    signature_valid = verify_webhook_signature(
        raw_body=raw_body,
        timestamp=x_webhook_timestamp,
        signature=x_webhook_signature,
        secret=secret,
    )
    logger.info(
        "cashfree_webhook_received",
        webhook_version=x_webhook_version,
        signature_present=bool(x_webhook_signature),
        timestamp_present=bool(x_webhook_timestamp),
        secret_configured=bool(secret),
        signature_valid=signature_valid,
    )
    if not signature_valid:
        logger.warning("cashfree_webhook_rejected", reason="invalid_signature")
        raise HTTPException(status_code=401, detail="Invalid or missing webhook signature.")

    # Only parsed (and only ever trusted) after the signature above has
    # already verified these exact bytes came from Cashfree.
    try:
        payload = json.loads(raw_body)
    except ValueError:
        logger.warning("cashfree_webhook_malformed_payload", reason="invalid_json")
        raise HTTPException(status_code=400, detail="Invalid JSON payload.") from None
    if not isinstance(payload, dict):
        logger.warning("cashfree_webhook_malformed_payload", reason="not_a_json_object")
        raise HTTPException(status_code=400, detail="Expected a JSON object payload.")

    # A second, Decimal-safe parse used ONLY to extract the payment
    # amount (spec §9: never use floating point for payment amounts) —
    # `json.loads`'s default `float` would already have lost the
    # guarantee of exact decimal precision before `apply_payment_event`
    # ever saw the value. Never stored anywhere; `payload` (float-based,
    # like every other webhook payload this codebase stores) is what
    # `WebhookEvent.payload`/`PaymentTransaction.raw_payload` persist.
    precise_payload = json.loads(raw_body, parse_float=Decimal)

    event_type = payload.get("type")
    order_data = extract_order_data(payload)
    payment_data = extract_payment_data(payload)
    cashfree_order_id = order_data.get("order_id")
    if not cashfree_order_id or not isinstance(cashfree_order_id, str):
        logger.warning("cashfree_webhook_malformed_payload", reason="missing_order_id")
        raise HTTPException(status_code=400, detail="Payload is missing data.order.order_id.")

    integration = await IntegrationRepository(session).get_by_code(IntegrationCode.CASHFREE)
    if integration is None:
        raise HTTPException(status_code=404, detail="Cashfree integration is not configured.")

    cf_payment_id = payment_data.get("cf_payment_id")
    webhook_service = WebhookService(session)
    event, created = await webhook_service.ingest(
        integration_id=integration.id,
        event_type=str(event_type) if event_type else "unknown",
        payload=payload,
        # Cashfree's own documented idempotency guidance: track
        # cf_payment_id and process it only once, regardless of retries
        # — see app.integrations.cashfree.normalizer/docs/integrations/
        # cashfree.md. Falls back to the generic content-hash idempotency
        # key (WebhookService.compute_fallback_event_id) when absent.
        external_event_id=str(cf_payment_id) if cf_payment_id is not None else None,
        external_resource_id=cashfree_order_id,
    )
    if not created:
        logger.info("cashfree_webhook_duplicate_ignored", webhook_event_id=str(event.id))
        return {"success": True}

    if event_type not in RECOGNIZED_WEBHOOK_TYPES:
        await webhook_service.mark_ignored(
            event.id, reason=f"unrecognized_event_type:{event_type}"
        )
        logger.warning("cashfree_webhook_unrecognized_type", event_type=event_type)
        return {"success": True}

    await webhook_service.mark_processing(event.id)

    precise_payment_data = extract_payment_data(precise_payload)
    amount = extract_decimal_amount(precise_payment_data, "payment_amount")
    method = payment_data.get("payment_method")
    method_name = next(iter(method), None) if isinstance(method, dict) else None
    # Sanitized transaction record — the gateway/error details Cashfree
    # sent, never `customer_details` (PII already unavoidably captured on
    # `WebhookEvent.payload`; no reason to duplicate it here too).
    transaction_payload: dict[str, Any] = {
        "type": event_type,
        "event_time": payload.get("event_time"),
        "payment": {k: v for k, v in payment_data.items() if k != "payment_method"}
        | ({"payment_method": method_name} if method_name else {}),
        "payment_gateway_details": extract_gateway_details(payload),
        "error_details": extract_error_details(payload),
    }

    try:
        result = await CashfreePaymentService(session).apply_payment_event(
            cashfree_order_id=cashfree_order_id,
            cf_payment_id=str(cf_payment_id) if cf_payment_id is not None else None,
            raw_status=payment_data.get("payment_status"),
            amount=amount,
            currency=payment_data.get("payment_currency"),
            payment_method_name=method_name,
            paid_at=parse_iso_datetime(payment_data.get("payment_time")),
            raw_payload=transaction_payload,
        )
    except Exception as exc:  # noqa: BLE001 - persisted before Cashfree is told to retry
        await session.rollback()
        await webhook_service.mark_failed(event.id, error_message=str(exc))
        logger.error(
            "cashfree_webhook_processing_failed", webhook_event_id=str(event.id), error=str(exc)
        )
        raise HTTPException(status_code=500, detail="Internal error processing webhook.") from exc

    if result.applied:
        await webhook_service.mark_processed(event.id)
    else:
        await webhook_service.mark_ignored(event.id, reason=result.reason or "not_applied")
        logger.warning(
            "cashfree_webhook_not_applied",
            webhook_event_id=str(event.id),
            reason=result.reason,
        )

    return {"success": True}
