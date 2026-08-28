"""Shopify webhook endpoint.

Status: IMPLEMENTED (Phase 2.2).

    POST /api/v1/webhooks/shopify

A single endpoint handles every subscribed topic (`X-Shopify-Topic`
header), matching how a Shopify app typically registers one callback
URL for all its webhooks. Verifies `X-Shopify-Hmac-Sha256` against the
*raw* request body (spec §17 — mandatory, constant-time, never trust an
event just because it reached this URL), then hands off to the generic
`WebhookService` for idempotent ingestion before enqueuing Celery
processing. Always acks with 200 once the signature and JSON body are
valid — even for a duplicate delivery or an unsubscribed topic — so
Shopify never retries a webhook this endpoint already understood.
"""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.integrations.shopify.webhooks import content_length_matches_body, webhook_hmac_debug_info
from app.models.integration import IntegrationCode
from app.repositories.integration import IntegrationRepository
from app.services.audit_service import AuditService
from app.services.webhook_service import WebhookService
from app.tasks.webhook_processing import process_webhook_event_task

router = APIRouter()
logger = get_logger(__name__)


@router.post("", status_code=200)
async def receive_shopify_webhook(
    request: Request,
    x_shopify_topic: str | None = Header(default=None, alias="X-Shopify-Topic"),
    x_shopify_hmac_sha256: str | None = Header(default=None, alias="X-Shopify-Hmac-Sha256"),
    x_shopify_webhook_id: str | None = Header(default=None, alias="X-Shopify-Webhook-Id"),
    session: AsyncSession = Depends(get_db),
) -> dict:
    raw_body = await request.body()

    # Cross-checks the body FastAPI/Starlette actually handed us against
    # what Shopify's request declared it was sending. Neither this nor
    # `verify_webhook_hmac` can be fooled by a proxy/edge layer silently
    # truncating or otherwise altering the body in transit (a class of
    # bug no in-process test can ever exercise, since the test client
    # never goes over a real network hop) — a mismatch here is direct
    # proof the bytes we're hashing aren't the bytes Shopify sent, with
    # zero reliance on the secret or signature.
    content_length_header = request.headers.get("content-length")
    length_matches = content_length_matches_body(content_length_header, len(raw_body))
    # A safe, non-reversible fingerprint of the exact bytes hashed for
    # this attempt — reveals nothing about the payload content, but lets
    # a body captured independently (e.g. via a temporary request-catcher
    # URL) be hashed the same way and compared against this log line.
    raw_body_sha256 = hashlib.sha256(raw_body).hexdigest()

    # .strip(): a secret pasted into Render's dashboard (or any env var
    # UI) can silently pick up a trailing newline/space that's invisible
    # when eyeballing the value against the Shopify dashboard but changes
    # the byte sequence HMAC is keyed with — stripping incidental
    # whitespace here does not weaken verification (Shopify secrets never
    # intentionally contain leading/trailing whitespace).
    secret = (settings.SHOPIFY_WEBHOOK_SECRET or "").strip()
    diagnostics = webhook_hmac_debug_info(
        raw_body=raw_body, signature_header=x_shopify_hmac_sha256, secret=secret
    )
    logger.info(
        "shopify_webhook_hmac_check",
        topic=x_shopify_topic,
        content_length_header=content_length_header,
        content_length_matches_body=length_matches,
        raw_body_sha256=raw_body_sha256,
        **diagnostics,
    )

    if not diagnostics["hmac_valid"]:
        logger.warning("shopify_webhook_rejected", topic=x_shopify_topic, reason="invalid_hmac")
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")

    if not x_shopify_topic:
        raise HTTPException(status_code=400, detail="Missing X-Shopify-Topic header.")

    try:
        payload = json.loads(raw_body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.") from exc

    integration = await IntegrationRepository(session).get_by_code(IntegrationCode.SHOPIFY)
    if integration is None:
        raise HTTPException(status_code=404, detail="Shopify integration is not configured.")

    event, created = await WebhookService(session).ingest(
        integration_id=integration.id,
        event_type=x_shopify_topic,
        payload=payload,
        external_event_id=x_shopify_webhook_id,
        external_resource_id=str(payload.get("id")) if isinstance(payload, dict) else None,
    )

    await AuditService(session).record(
        user=None,
        action="webhook.received" if created else "webhook.duplicate_ignored",
        entity_type="webhook_event",
        entity_id=str(event.id),
        metadata={"topic": x_shopify_topic, "integration": "shopify"},
    )
    await session.commit()

    if created:
        try:
            process_webhook_event_task.delay(str(event.id))
        except Exception as exc:  # noqa: BLE001 - broker outage must not fail the webhook ack
            logger.warning(
                "shopify_webhook_enqueue_failed", webhook_event_id=str(event.id), error=str(exc)
            )

    return {"success": True}
