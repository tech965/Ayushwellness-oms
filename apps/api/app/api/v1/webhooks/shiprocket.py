"""Shiprocket webhook endpoints.

Status: IMPLEMENTED — tracking status updates.

    POST /api/v1/webhooks/shiprocket/tracking

Verifies the shared `SHIPROCKET_WEBHOOK_SECRET` via
`app.integrations.shiprocket.webhooks.verify_webhook_token` (see that
module's docstring for why both a header and a body-field location are
checked — Shiprocket's transport for this secret is not confirmed
without a live delivery), then hands the parsed payload to the generic
`WebhookService` for idempotent ingestion (the same mechanism
`receive_shopify_webhook` uses) before matching+applying it via
`ShiprocketWebhookService` — synchronously, not through Celery, since a
Shiprocket webhook body carries no topic the generic
`ENTITY_UPSERT_HANDLERS` dispatch understands the way a Shopify REST
topic does.

Always acks with 200 once the token is valid and the body is well-formed
JSON — including when no OMS shipment can be safely matched (the
`WebhookEvent` is marked IGNORED with a sanitized reason, for
reconciliation; nothing is ever fabricated) — so Shiprocket never retries
a delivery this endpoint already understood. Only a genuine processing
failure (e.g. a database error) returns 5xx so Shiprocket's own retry
behaviour can recover it.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.integrations.shiprocket.webhooks import extract_body_token, verify_webhook_token
from app.models.integration import IntegrationCode
from app.repositories.integration import IntegrationRepository
from app.services.shiprocket_webhook_service import ShiprocketWebhookService
from app.services.webhook_service import WebhookService

router = APIRouter()
logger = get_logger(__name__)


def _parse_json_object(raw_body: bytes) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw_body)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


@router.post("/tracking", status_code=200)
async def receive_shiprocket_tracking_webhook(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    raw_body = await request.body()
    secret = (settings.SHIPROCKET_WEBHOOK_SECRET or "").strip()
    header_token = (x_api_key or "").strip() or None

    # Header check first — never requires parsing the body, and never
    # reveals whether the body is well-formed JSON to a caller who hasn't
    # proven they hold the secret. Only when the header alone doesn't
    # settle it do we parse the body, both to look for a body-embedded
    # token (see app.integrations.shiprocket.webhooks) and, if that
    # succeeds, to reuse the same parse for processing below.
    token_valid = verify_webhook_token(header_token=header_token, body_token=None, secret=secret)
    payload: dict[str, Any] | None = None
    token_source: str | None = "header" if token_valid else None

    if not token_valid:
        payload = _parse_json_object(raw_body)
        if payload is not None:
            token_valid = verify_webhook_token(
                header_token=None, body_token=extract_body_token(payload), secret=secret
            )
            if token_valid:
                token_source = "body"

    logger.info(
        "shiprocket_webhook_received",
        token_valid=token_valid,
        token_source=token_source,
        secret_configured=bool(secret),
    )
    if not token_valid:
        logger.warning("shiprocket_webhook_rejected", reason="invalid_token")
        raise HTTPException(status_code=401, detail="Invalid or missing webhook token.")

    if payload is None:
        payload = _parse_json_object(raw_body)
    if payload is None:
        logger.warning("shiprocket_webhook_malformed_payload", reason="invalid_or_non_object_json")
        raise HTTPException(status_code=400, detail="Expected a JSON object payload.")

    integration = await IntegrationRepository(session).get_by_code(IntegrationCode.SHIPROCKET)
    if integration is None:
        raise HTTPException(status_code=404, detail="Shiprocket integration is not configured.")

    resource_id = payload.get("awb") or payload.get("awb_code") or payload.get("shipment_id")
    webhook_service = WebhookService(session)
    event, created = await webhook_service.ingest(
        integration_id=integration.id,
        event_type="shipment.tracking_update",
        payload=payload,
        # No confirmed stable Shiprocket event id (see
        # app.integrations.shiprocket.normalizer) — the generic fallback
        # (a deterministic hash of integration + event_type + payload)
        # already makes a byte-identical retry idempotent while still
        # treating a genuinely different status update as a new event.
        external_event_id=None,
        external_resource_id=str(resource_id) if resource_id else None,
    )
    if not created:
        logger.info("shiprocket_webhook_duplicate_ignored", webhook_event_id=str(event.id))
        return {"success": True}

    await webhook_service.mark_processing(event.id)
    try:
        result = await ShiprocketWebhookService(session).apply_tracking_webhook(payload)
    except Exception as exc:  # noqa: BLE001 - persisted before Shiprocket is told to retry
        await session.rollback()
        await webhook_service.mark_failed(event.id, error_message=str(exc))
        logger.error(
            "shiprocket_webhook_processing_failed",
            webhook_event_id=str(event.id),
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail="Internal error processing webhook.") from exc

    if result.matched:
        await webhook_service.mark_processed(event.id)
    else:
        await webhook_service.mark_ignored(
            event.id, reason=f"no_matching_shipment:{result.match_strategy}"
        )
        logger.warning(
            "shiprocket_webhook_unmatched",
            webhook_event_id=str(event.id),
            match_strategy=result.match_strategy,
        )

    return {"success": True}
