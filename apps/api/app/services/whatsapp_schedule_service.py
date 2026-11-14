"""Schedule CRUD + queueing: turns a `WhatsAppSchedule` + its recipients
into `WhatsAppQueuedMessage` rows for one send instant. A message is
never marked SENT merely by being queued -- queuing only ever creates
rows with `status="QUEUED"`; `whatsapp_send_service.send_queued_message`
is the only code path that can move a row past that.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.whatsapp_automation import (
    WhatsAppContact,
    WhatsAppQueuedMessage,
    WhatsAppRecipientGroupMember,
    WhatsAppSchedule,
    WhatsAppScheduleRecipient,
)


def build_idempotency_key(
    *, schedule_id: uuid.UUID | None, contact_id: uuid.UUID, send_slot: str
) -> str:
    """Deterministic per (schedule_or_adhoc, contact, send slot) -- a
    repeated queuer call for the same slot collides on the DB's unique
    constraint instead of creating a second send. `send_slot` is a
    caller-chosen stable string (e.g. an ISO date for a daily recurring
    schedule, or a full timestamp for a one-time one) -- never
    `datetime.now()`, which would defeat the whole point.
    """
    raw = f"{schedule_id or 'adhoc'}:{contact_id}:{send_slot}"
    return hashlib.sha256(raw.encode()).hexdigest()


async def resolve_recipients(
    session: AsyncSession, schedule_id: uuid.UUID
) -> list[WhatsAppContact]:
    rows = (
        await session.execute(
            select(WhatsAppScheduleRecipient).where(
                WhatsAppScheduleRecipient.schedule_id == schedule_id
            )
        )
    ).scalars().all()
    contact_ids: set[uuid.UUID] = set()
    for r in rows:
        if r.contact_id:
            contact_ids.add(r.contact_id)
        if r.group_id:
            members = (
                await session.execute(
                    select(WhatsAppRecipientGroupMember.contact_id).where(
                        WhatsAppRecipientGroupMember.group_id == r.group_id
                    )
                )
            ).scalars().all()
            contact_ids.update(members)
    if not contact_ids:
        return []
    contacts = (
        await session.execute(select(WhatsAppContact).where(WhatsAppContact.id.in_(contact_ids)))
    ).scalars().all()
    return [c for c in contacts if c.opted_in]


def _payload_for_schedule(schedule: WhatsAppSchedule) -> dict:
    if schedule.message_type == "text":
        return {"body": schedule.body_text or ""}
    if schedule.message_type == "template":
        return {
            "template_name": schedule.template_name,
            "language_code": schedule.template_language or "en",
            "header_image_id": schedule.media_id,
            "header_image_link": schedule.media_link,
        }
    if schedule.message_type == "image":
        return {
            "media_id": schedule.media_id,
            "media_link": schedule.media_link,
            "caption": schedule.body_text,
        }
    raise ValueError(f"Unknown message_type '{schedule.message_type}'")


async def queue_schedule(
    session: AsyncSession,
    schedule: WhatsAppSchedule,
    *,
    scheduled_send_at: datetime,
    send_slot: str,
    payload_override: dict | None = None,
) -> list[WhatsAppQueuedMessage]:
    """Creates one `WhatsAppQueuedMessage` per recipient for this send
    instant. Idempotent: a duplicate call for the same `send_slot`
    creates no duplicate rows (each insert that collides on the unique
    idempotency key is skipped, not retried as a new row).
    """
    if not schedule.is_enabled or schedule.is_paused:
        return []
    recipients = await resolve_recipients(session, schedule.id)
    payload = payload_override or _payload_for_schedule(schedule)
    created: list[WhatsAppQueuedMessage] = []
    now = datetime.now(UTC)
    for contact in recipients:
        key = build_idempotency_key(
            schedule_id=schedule.id, contact_id=contact.id, send_slot=send_slot
        )
        existing = (
            await session.execute(
                select(WhatsAppQueuedMessage).where(
                    WhatsAppQueuedMessage.idempotency_key == key
                )
            )
        ).scalar_one_or_none()
        if existing:
            continue
        msg = WhatsAppQueuedMessage(
            schedule_id=schedule.id,
            contact_id=contact.id,
            message_type=schedule.message_type,
            payload=payload,
            idempotency_key=key,
            scheduled_send_at=scheduled_send_at,
            status="QUEUED",
            retry_count=0,
            max_retries=3,
            created_at=now,
            updated_at=now,
        )
        session.add(msg)
        try:
            await session.flush()
        except IntegrityError:
            # Race with a concurrent queuer run for the same slot --
            # the unique constraint is the real guard; this is belt-
            # and-braces for the gap between the SELECT above and the
            # flush.
            await session.rollback()
            continue
        created.append(msg)
    return created


async def cancel_message(session: AsyncSession, message_id: uuid.UUID) -> WhatsAppQueuedMessage:
    msg = await session.get(WhatsAppQueuedMessage, message_id)
    if msg is None:
        raise ValueError("Queued message not found")
    if msg.status in ("SENT", "DELIVERED", "READ"):
        raise ValueError(f"Cannot cancel a message already {msg.status}.")
    msg.status = "CANCELLED"
    return msg
