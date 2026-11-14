"""Internal-only: receives WhatsApp webhook deliveries forwarded by
Sales CRM's own Meta webhook pipeline (Meta's callback URL points at
Sales CRM, not here -- see `app.api.v1.webhooks.whatsapp`'s module
docstring and this session's fan-out design discussion for why).

Authenticated with a dedicated `SALES_CRM_FORWARD_SECRET` bearer token
(`app.dependencies.internal_auth`) -- deliberately NOT Meta's
`X-Hub-Signature-256` scheme, since the caller is Sales CRM's backend,
not Meta directly. Reuses the same parse/persist logic as the public
Meta-facing webhook via `app.services.whatsapp_webhook_processor`, so
the two paths can't drift apart.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_db
from app.dependencies.internal_auth import require_sales_crm_forward_secret
from app.services.whatsapp_webhook_processor import (
    DuplicateDelivery,
    process_whatsapp_webhook_payload,
)

router = APIRouter()
logger = get_logger(__name__)


class ForwardedEventRequest(BaseModel):
    payload: dict
    delivery_digest: str
    # Informational only (when Sales CRM originally received/recorded
    # it) -- processing always uses this request's own arrival, never
    # trusts a caller-supplied processing timestamp for anything
    # security- or ordering-relevant.
    original_received_at: datetime | None = None


class ForwardedEventResponse(BaseModel):
    ok: bool
    duplicate: bool = False


@router.post("/forwarded-event", response_model=ForwardedEventResponse)
async def receive_forwarded_event(
    body: ForwardedEventRequest,
    session: AsyncSession = Depends(get_db),
    _: None = Depends(require_sales_crm_forward_secret),
) -> ForwardedEventResponse:
    try:
        await process_whatsapp_webhook_payload(
            session,
            body.payload,
            delivery_digest=body.delivery_digest,
            source="sales_crm_forward",
        )
    except DuplicateDelivery:
        logger.info("whatsapp_forwarded_event_duplicate", digest=body.delivery_digest)
        return ForwardedEventResponse(ok=True, duplicate=True)
    except Exception:
        await session.rollback()
        logger.error("whatsapp_forwarded_event_processing_failed", digest=body.delivery_digest)
        raise

    await session.commit()
    return ForwardedEventResponse(ok=True)
