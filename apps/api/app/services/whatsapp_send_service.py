"""Sends one queued WhatsApp message and records the outcome. Enforces
the 24-hour customer service window for free-form (text/image) sends --
Meta: "When the window closes, you can only send pre-approved template
messages." A schedule composed as `text`/`image` whose window has
closed is never silently dropped and never silently bypassed -- it's
marked FAILED with an explicit reason, for an operator to resolve (e.g.
by re-composing the schedule as a template).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.whatsapp.client import WhatsAppClient
from app.integrations.whatsapp.config import WhatsAppConfig
from app.integrations.whatsapp.errors import WhatsAppError, WhatsAppWindowClosedError
from app.models.whatsapp_automation import (
    WhatsAppContact,
    WhatsAppCustomerWindow,
    WhatsAppQueuedMessage,
)

WINDOW_DURATION = timedelta(hours=24)


async def window_is_open(session: AsyncSession, contact_id: uuid.UUID) -> bool:
    row = (
        await session.execute(
            select(WhatsAppCustomerWindow).where(
                WhatsAppCustomerWindow.contact_id == contact_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    return datetime.now(UTC) - row.last_inbound_at < WINDOW_DURATION


async def send_queued_message(
    session: AsyncSession,
    message: WhatsAppQueuedMessage,
    *,
    config: WhatsAppConfig | None = None,
    client: WhatsAppClient | None = None,
) -> WhatsAppQueuedMessage:
    """Mutates `message` in place (status/provider_message_id/
    failure_reason/sent_at/retry_count) and returns it. Caller commits.
    """
    contact = await session.get(WhatsAppContact, message.contact_id)
    if contact is None:
        message.status = "FAILED"
        message.failure_reason = "Contact no longer exists."
        return message
    if not contact.opted_in:
        message.status = "FAILED"
        message.failure_reason = "Recipient has opted out."
        return message

    if message.message_type in ("text", "image") and not await window_is_open(
        session, message.contact_id
    ):
        message.status = "FAILED"
        message.failure_reason = (
            "24-hour customer service window is closed for this recipient; a free-form "
            f"{message.message_type} message cannot be sent. Re-compose this schedule as an "
            "approved template message instead."
        )
        message.retry_count += 1
        return message

    cfg = config or WhatsAppConfig.from_settings()
    own_client = client is None
    c = client or WhatsAppClient(cfg)
    try:
        message.status = "SENDING"
        payload = message.payload
        if message.message_type == "text":
            resp = await c.send_text(contact.phone_number, payload["body"])
        elif message.message_type == "template":
            resp = await c.send_template(
                contact.phone_number,
                template_name=payload["template_name"],
                language_code=payload.get("language_code", "en"),
                body_parameters=payload.get("body_parameters"),
                header_image_id=payload.get("header_image_id"),
                header_image_link=payload.get("header_image_link"),
            )
        elif message.message_type == "image":
            resp = await c.send_image(
                contact.phone_number,
                media_id=payload.get("media_id"),
                link=payload.get("media_link"),
                caption=payload.get("caption"),
            )
        else:
            raise WhatsAppError(f"Unknown message_type '{message.message_type}'")
        message.provider_message_id = resp["messages"][0]["id"]
        message.status = "SENT"
        message.sent_at = datetime.now(UTC)
    except WhatsAppWindowClosedError as exc:
        message.status = "FAILED"
        message.failure_reason = str(exc)
        message.retry_count += 1
    except Exception as exc:  # noqa: BLE001 - every failure must be recorded, never raised past here
        message.status = "FAILED" if message.retry_count + 1 >= message.max_retries else "QUEUED"
        message.failure_reason = str(exc)[:1000]
        message.retry_count += 1
    finally:
        if own_client:
            await c.aclose()
    return message
