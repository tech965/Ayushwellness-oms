"""WhatsApp Cloud API webhook: GET verification handshake + POST
delivery (status updates + inbound messages). Verifies
`X-Hub-Signature-256` against the raw body before trusting anything in
it (same non-negotiable rule as the Shopify webhook's HMAC check) --
see `app.integrations.whatsapp.webhook_security`.

Actual parsing/persistence is shared with the internal Sales-CRM
forwarding endpoint (`app.api.v1.endpoints.internal_whatsapp`) via
`app.services.whatsapp_webhook_processor` -- see that module for the
status-transition and 24h-window rules. `delivery_digest` (sha256 of
the raw body) gives whole-delivery idempotency here too, on the same
basis Sales CRM's own forwarding worker uses, so a delivery processed
directly by Meta and one somehow also relayed by Sales CRM can't double
-apply.
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import PlainTextResponse, Response

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.integrations.whatsapp.webhook_security import verify_signature, verify_webhook_challenge
from app.services.whatsapp_webhook_processor import (
    DuplicateDelivery,
    process_whatsapp_webhook_payload,
)

router = APIRouter()
logger = get_logger(__name__)


@router.get("")
async def verify(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> Response:
    challenge = verify_webhook_challenge(
        mode=hub_mode,
        token=hub_verify_token,
        challenge=hub_challenge,
        expected_token=settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN,
    )
    if challenge is None:
        return Response(status_code=403)
    return PlainTextResponse(challenge)


@router.post("", status_code=200)
async def receive(request: Request, session: AsyncSession = Depends(get_db)) -> dict:
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_signature(
        raw_body=raw_body, signature_header=signature, app_secret=settings.META_APP_SECRET
    ):
        logger.warning("whatsapp_webhook_signature_invalid")
        return {"ok": False, "reason": "invalid_signature"}

    payload = await request.json()
    delivery_digest = f"sha256:{hashlib.sha256(raw_body).hexdigest()}"
    try:
        await process_whatsapp_webhook_payload(
            session, payload, delivery_digest=delivery_digest, source="meta"
        )
    except DuplicateDelivery:
        return {"ok": True, "reason": "duplicate_delivery"}
    except Exception:
        await session.rollback()
        raise

    await session.commit()
    return {"ok": True}
