"""WhatsApp contact directory + phone validation.

Validation deliberately mirrors `apps/web/lib/whatsapp.ts`'s own rule
(same product, two runtimes) rather than inventing a stricter one: a
bare 10-digit Indian number gets "91" prepended, anything longer than
10 digits is assumed to already carry a country code and is used as-is,
anything else is rejected. This is not full E.164 validation -- it's
the same pragmatic rule already proven in this codebase.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.whatsapp_automation import WhatsAppContact


class InvalidPhoneNumberError(ValueError):
    pass


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return f"91{digits}"
    if len(digits) > 10:
        return digits
    raise InvalidPhoneNumberError(f"'{phone}' is not a usable WhatsApp number.")


async def create_contact(session: AsyncSession, *, name: str, phone: str) -> WhatsAppContact:
    normalized = normalize_phone(phone)
    existing = (
        await session.execute(
            select(WhatsAppContact).where(WhatsAppContact.phone_number == normalized)
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    contact = WhatsAppContact(name=name, phone_number=normalized, created_at=datetime.now(UTC))
    session.add(contact)
    await session.flush()
    return contact


async def set_opt_out(session: AsyncSession, contact_id: uuid.UUID, *, opted_in: bool) -> None:
    contact = await session.get(WhatsAppContact, contact_id)
    if contact is None:
        raise ValueError("Contact not found")
    contact.opted_in = opted_in
    contact.opted_out_at = None if opted_in else datetime.now(UTC)
