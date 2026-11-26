"""Direct courier webhook endpoints (Blue Dart, Delhivery, Ecom Express, ...).

Status: PLANNED for most couriers — foundation added in Phase 2, onboarded
one at a time as each is actually implemented (no generic `{courier_code}`
dispatcher yet; that's unnecessary generalization for a single real
courier so far — see `app.services.courier.registry` for the same
one-provider-at-a-time pattern on the outbound side).

    POST /api/v1/webhooks/couriers/nimbuspost — IMPLEMENTED (Module 5).

New (v2), B2C only — the confirmed dashboard configuration for this
account (NimbusPost's official webhook reference, 2026-11-26). Legacy
(v1-shipment-status) and B2B cargo payloads are NOT implemented; a
delivery in either shape is never sent here since the subscription is
scoped to `order.created`/`order.updated`/`tracking.updated` (B2C-only
events) in the New format, but if NimbusPost ever changed that
subscription's format, this endpoint would reject it (signature scheme
differs: `X-Hmac-SHA256`, not checked here).

Verifies `x-nimbus-signature` (HMAC-SHA256 over the raw body, hex,
`sha256=` prefix) via `app.integrations.nimbuspost.webhooks.
verify_webhook_signature`, then routes to `NimbusPostWebhookService`
based on `x-nimbus-event`:
  - `tracking.updated` — authoritative for shipment/RTO state (via the
    same shared `apply_tracking_event` Shiprocket's own ingestion paths
    use — see that function's docstring).
  - `order.updated` — AWB/identifier backfill and cancellation only.
  - `order.created` — recorded for idempotency/audit; never mutates.
Any other `x-nimbus-event` is acked 200 and recorded IGNORED (never a
4xx/5xx that would make NimbusPost retry something it can never resolve
differently) — same "ack once understood, even when unmatched/
unsupported" philosophy as `app.api.v1.webhooks.shiprocket`.

Idempotency: `x-nimbus-delivery` (a genuine stable per-delivery id,
confirmed in NimbusPost's docs — unlike Shiprocket, no fallback-hash
needed) is the `WebhookEvent.external_event_id`.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.integrations.nimbuspost.normalizer import extract_webhook_identifiers
from app.integrations.nimbuspost.webhooks import verify_webhook_signature
from app.models.integration import IntegrationCode
from app.repositories.integration import IntegrationRepository
from app.services.nimbuspost_webhook_service import NimbusPostWebhookService
from app.services.webhook_service import WebhookService

router = APIRouter()
logger = get_logger(__name__)

_SUPPORTED_EVENTS = {"order.created", "order.updated", "tracking.updated"}


def _parse_json_object(raw_body: bytes) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw_body)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


@router.post("/nimbuspost", status_code=200)
async def receive_nimbuspost_webhook(
    request: Request,
    x_nimbus_signature: str | None = Header(default=None, alias="x-nimbus-signature"),
    x_nimbus_event: str | None = Header(default=None, alias="x-nimbus-event"),
    x_nimbus_delivery: str | None = Header(default=None, alias="x-nimbus-delivery"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    raw_body = await request.body()
    secret = (settings.NIMBUSPOST_WEBHOOK_SECRET or "").strip()

    # Signature verified over the RAW bytes before anything about the
    # body's meaning is inspected — never parse first, per NimbusPost's
    # own "Verify the signature" guidance (a parsed-then-re-serialized
    # JSON string no longer byte-matches what was actually signed).
    signature_valid = verify_webhook_signature(
        raw_body=raw_body, signature_header=x_nimbus_signature, secret=secret
    )
    logger.info(
        "nimbuspost_webhook_received",
        signature_valid=signature_valid,
        event_type=x_nimbus_event,
        delivery_id=x_nimbus_delivery,
        secret_configured=bool(secret),
    )
    if not signature_valid:
        logger.warning("nimbuspost_webhook_rejected", reason="invalid_signature")
        raise HTTPException(status_code=401, detail="Invalid or missing webhook signature.")

    payload = _parse_json_object(raw_body)
    if payload is None:
        logger.warning("nimbuspost_webhook_malformed_payload", reason="invalid_or_non_object_json")
        raise HTTPException(status_code=400, detail="Expected a JSON object payload.")

    integration = await IntegrationRepository(session).get_by_code(IntegrationCode.NIMBUSPOST)
    if integration is None:
        raise HTTPException(status_code=404, detail="NimbusPost integration is not configured.")

    event_type = x_nimbus_event or ""
    ids = extract_webhook_identifiers(payload)
    resource_id = ids["awb"] or ids["order_number"] or ids["nimbuspost_order_id"]

    webhook_service = WebhookService(session)
    event, created = await webhook_service.ingest(
        integration_id=integration.id,
        event_type=event_type,
        payload=payload,
        external_event_id=x_nimbus_delivery,
        external_resource_id=resource_id,
    )
    event_id = event.id
    if not created:
        logger.info("nimbuspost_webhook_duplicate_ignored", webhook_event_id=str(event_id))
        return {"success": True}

    if event_type not in _SUPPORTED_EVENTS:
        logger.warning("nimbuspost_webhook_unsupported_event_type", event_type=event_type)
        await webhook_service.mark_ignored(event_id, reason=f"unsupported_event_type:{event_type}")
        return {"success": True}

    await webhook_service.mark_processing(event_id)
    service = NimbusPostWebhookService(session)
    try:
        if event_type == "tracking.updated":
            result = await service.apply_tracking_webhook(payload)
        elif event_type == "order.updated":
            result = await service.apply_order_updated_webhook(payload)
        else:  # "order.created"
            result = await service.apply_order_created_webhook(payload)
    except Exception as exc:  # noqa: BLE001 - persisted before NimbusPost is told to retry
        await session.rollback()
        await webhook_service.mark_failed(event_id, error_message=str(exc))
        logger.error(
            "nimbuspost_webhook_processing_failed", webhook_event_id=str(event_id), error=str(exc)
        )
        raise HTTPException(status_code=500, detail="Internal error processing webhook.") from exc

    if result.matched:
        await webhook_service.mark_processed(event_id)
    else:
        await webhook_service.mark_ignored(
            event_id, reason=f"no_matching_shipment:{result.match_strategy}"
        )
        logger.warning(
            "nimbuspost_webhook_unmatched",
            webhook_event_id=str(event_id),
            match_strategy=result.match_strategy,
        )

    return {"success": True}
