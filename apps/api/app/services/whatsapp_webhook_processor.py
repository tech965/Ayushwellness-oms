"""Shared WhatsApp webhook payload processing -- status updates +
inbound-message 24h-window tracking. Used by both the public Meta-facing
webhook (`app.api.v1.webhooks.whatsapp`) and the internal Sales-CRM
forwarding endpoint (`app.api.v1.endpoints.internal_whatsapp`), so the
two paths can never drift out of sync. Extracted from what was
previously only in the public webhook's `receive()` handler.

Callers own the transaction (commit on success, rollback on failure) --
this module only adds pending writes to the given `AsyncSession`. That
matters for `delivery_digest` idempotency: if processing raises partway
through, the caller's rollback means the digest row never persists
either, so a genuine retry reprocesses the delivery instead of being
silently skipped as "already seen."
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.integrations.whatsapp.webhook_parser import parse_webhook_payload
from app.models.webhook_delivery import WebhookDelivery
from app.models.whatsapp_automation import (
    WhatsAppContact,
    WhatsAppCustomerWindow,
    WhatsAppQueuedMessage,
)

_WEBHOOK_DELIVERY_PROVIDER = "whatsapp"

logger = get_logger(__name__)

_STATUS_MAP = {
    "sent": "SENT",
    "delivered": "DELIVERED",
    "read": "READ",
    "failed": "FAILED",
}


class DuplicateDelivery(Exception):
    """Raised when `delivery_digest` was already recorded for a prior,
    successfully-processed delivery. Callers should treat this as a
    successful no-op (200), not an error -- it is Meta/Sales-CRM safely
    retrying something already applied.
    """

    def __init__(self, delivery_digest: str) -> None:
        self.delivery_digest = delivery_digest
        super().__init__(f"Delivery {delivery_digest} already processed")


async def process_whatsapp_webhook_payload(
    session: AsyncSession,
    payload: dict,
    *,
    delivery_digest: str | None = None,
    source: str = "meta",
) -> dict[str, int]:
    """Parses + stages (does not commit) writes for one WhatsApp webhook
    payload. When `delivery_digest` is given, raises `DuplicateDelivery`
    instead of reprocessing if this exact delivery was already recorded
    -- additive to, never a replacement for, the per-status/per-message
    `provider_message_id` dedup below (both apply). Reuses the existing
    `WebhookDelivery` ledger (`provider="whatsapp"`, `delivery_id` =
    the digest, `topic` = `source`) rather than a new table.
    """
    if delivery_digest:
        existing = (
            await session.execute(
                select(WebhookDelivery.id).where(
                    WebhookDelivery.provider == _WEBHOOK_DELIVERY_PROVIDER,
                    WebhookDelivery.delivery_id == delivery_digest,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise DuplicateDelivery(delivery_digest)
        session.add(
            WebhookDelivery(
                provider=_WEBHOOK_DELIVERY_PROVIDER,
                delivery_id=delivery_digest,
                topic=source,
            )
        )

    statuses, inbound = parse_webhook_payload(payload)

    status_count = 0
    for s in statuses:
        new_status = _STATUS_MAP.get(s.status)
        if not new_status:
            continue
        msg = (
            await session.execute(
                select(WhatsAppQueuedMessage).where(
                    WhatsAppQueuedMessage.provider_message_id == s.message_id
                )
            )
        ).scalar_one_or_none()
        if msg is None:
            continue
        # Never move a terminal state backwards (e.g. a late "sent"
        # callback arriving after "read" already landed).
        order = ["SENT", "DELIVERED", "READ"]
        if new_status in order and msg.status in order and (
            order.index(new_status) < order.index(msg.status)
        ):
            continue
        msg.status = new_status
        now = datetime.now(UTC)
        if new_status == "DELIVERED":
            msg.delivered_at = now
        elif new_status == "READ":
            msg.read_at = now
        elif new_status == "FAILED":
            msg.failure_reason = s.error_message or msg.failure_reason
        status_count += 1

    inbound_count = 0
    for m in inbound:
        contact = (
            await session.execute(
                select(WhatsAppContact).where(WhatsAppContact.phone_number == m.from_wa_id)
            )
        ).scalar_one_or_none()
        if contact is None:
            # Inbound from a number not in this app's own contact
            # directory -- nothing to open a window for (this feature
            # only messages its own known contacts), so there's no
            # window row to create. Logged, not dropped silently.
            logger.info("whatsapp_inbound_from_unknown_contact", wa_id=m.from_wa_id)
            continue
        window = (
            await session.execute(
                select(WhatsAppCustomerWindow).where(
                    WhatsAppCustomerWindow.contact_id == contact.id
                )
            )
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        if window is None:
            session.add(WhatsAppCustomerWindow(contact_id=contact.id, last_inbound_at=now))
        else:
            window.last_inbound_at = now
        inbound_count += 1

    return {"statuses_processed": status_count, "inbound_processed": inbound_count}
