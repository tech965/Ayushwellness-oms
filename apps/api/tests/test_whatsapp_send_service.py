"""`whatsapp_send_service.send_queued_message` -- enforces the 24h
customer service window for free-form sends, never bypasses it, never
marks opted-out recipients as sent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.integrations.whatsapp.client import WhatsAppClient
from app.integrations.whatsapp.config import WhatsAppConfig
from app.models.whatsapp_automation import (
    WhatsAppContact,
    WhatsAppCustomerWindow,
    WhatsAppQueuedMessage,
)
from app.services.whatsapp_send_service import send_queued_message, window_is_open
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

CFG = WhatsAppConfig(
    access_token="tok",
    phone_number_id="106540352242922",
    business_account_id="waba",
    api_version="v21.0",
    webhook_verify_token="v",
    app_secret="s",
)


def fake_client() -> WhatsAppClient:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"messaging_product": "whatsapp", "messages": [{"id": "wamid.OK"}]}
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WhatsAppClient(CFG, http_client=http)


async def _contact(session: AsyncSession, *, opted_in=True) -> WhatsAppContact:
    c = WhatsAppContact(
        name="Alice", phone_number="919991111111", opted_in=opted_in, created_at=datetime.now(UTC)
    )
    session.add(c)
    await session.flush()
    return c


async def _message(
    session: AsyncSession, contact: WhatsAppContact, *, message_type: str, payload: dict
):
    now = datetime.now(UTC)
    m = WhatsAppQueuedMessage(
        contact_id=contact.id,
        message_type=message_type,
        payload=payload,
        idempotency_key=f"test-{contact.id}-{message_type}-{now.timestamp()}",
        scheduled_send_at=now,
        status="QUEUED",
        retry_count=0,
        max_retries=3,
        created_at=now,
        updated_at=now,
    )
    session.add(m)
    await session.flush()
    return m


async def test_window_closed_blocks_text_send_never_bypassed(db_session: AsyncSession):
    contact = await _contact(db_session)
    msg = await _message(db_session, contact, message_type="text", payload={"body": "hi"})
    await db_session.commit()

    result = await send_queued_message(db_session, msg, config=CFG, client=fake_client())
    assert result.status == "FAILED"
    assert "window is closed" in result.failure_reason.lower()


async def test_window_open_allows_text_send(db_session: AsyncSession):
    contact = await _contact(db_session)
    db_session.add(
        WhatsAppCustomerWindow(contact_id=contact.id, last_inbound_at=datetime.now(UTC))
    )
    msg = await _message(db_session, contact, message_type="text", payload={"body": "hi"})
    await db_session.commit()

    result = await send_queued_message(db_session, msg, config=CFG, client=fake_client())
    assert result.status == "SENT"
    assert result.provider_message_id == "wamid.OK"
    assert result.sent_at is not None


async def test_window_expired_after_24h_blocks_send(db_session: AsyncSession):
    contact = await _contact(db_session)
    db_session.add(
        WhatsAppCustomerWindow(
            contact_id=contact.id, last_inbound_at=datetime.now(UTC) - timedelta(hours=25)
        )
    )
    msg = await _message(db_session, contact, message_type="text", payload={"body": "hi"})
    await db_session.commit()

    assert await window_is_open(db_session, contact.id) is False
    result = await send_queued_message(db_session, msg, config=CFG, client=fake_client())
    assert result.status == "FAILED"


async def test_template_send_allowed_regardless_of_window(db_session: AsyncSession):
    contact = await _contact(db_session)
    msg = await _message(
        db_session,
        contact,
        message_type="template",
        payload={"template_name": "daily_report", "language_code": "en"},
    )
    await db_session.commit()

    result = await send_queued_message(db_session, msg, config=CFG, client=fake_client())
    assert result.status == "SENT"


async def test_opted_out_contact_never_sent(db_session: AsyncSession):
    contact = await _contact(db_session, opted_in=False)
    msg = await _message(
        db_session,
        contact,
        message_type="template",
        payload={"template_name": "daily_report", "language_code": "en"},
    )
    await db_session.commit()

    result = await send_queued_message(db_session, msg, config=CFG, client=fake_client())
    assert result.status == "FAILED"
    assert "opted out" in result.failure_reason.lower()


async def test_failed_send_increments_retry_count(db_session: AsyncSession):
    contact = await _contact(db_session)
    db_session.add(
        WhatsAppCustomerWindow(contact_id=contact.id, last_inbound_at=datetime.now(UTC))
    )
    msg = await _message(db_session, contact, message_type="text", payload={"body": "hi"})
    await db_session.commit()

    def failing_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(failing_handler))
    client = WhatsAppClient(CFG, http_client=http)

    result = await send_queued_message(db_session, msg, config=CFG, client=client)
    assert result.retry_count == 1
    assert result.status == "QUEUED"  # under max_retries=3, eligible for another attempt
