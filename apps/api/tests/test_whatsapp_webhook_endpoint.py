"""WhatsApp webhook endpoint: GET verification handshake, POST signature
rejection, status update application, inbound message opening the 24h
window.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest
from app.core.config import settings
from app.models.whatsapp_automation import (
    WhatsAppContact,
    WhatsAppCustomerWindow,
    WhatsAppQueuedMessage,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _app_secret(monkeypatch):
    monkeypatch.setattr(settings, "META_APP_SECRET", "testsecret")
    monkeypatch.setattr(settings, "WHATSAPP_WEBHOOK_VERIFY_TOKEN", "verifytoken")
    yield


def _signed(body: dict) -> tuple[bytes, str]:
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(b"testsecret", raw, hashlib.sha256).hexdigest()
    return raw, sig


async def test_get_verification_handshake_succeeds(
    db_session: AsyncSession, make_authenticated_client
):
    client = await make_authenticated_client(db_session, permission_codes=[])
    async with client:
        response = await client.get(
            "/api/v1/webhooks/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "verifytoken",
                "hub.challenge": "challenge123",
            },
        )
    assert response.status_code == 200
    assert response.text == "challenge123"


async def test_get_verification_handshake_rejects_wrong_token(
    db_session: AsyncSession, make_authenticated_client
):
    client = await make_authenticated_client(db_session, permission_codes=[])
    async with client:
        response = await client.get(
            "/api/v1/webhooks/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong",
                "hub.challenge": "challenge123",
            },
        )
    assert response.status_code == 403


async def test_post_rejects_invalid_signature(db_session: AsyncSession, make_authenticated_client):
    client = await make_authenticated_client(db_session, permission_codes=[])
    body = {"entry": []}
    async with client:
        response = await client.post(
            "/api/v1/webhooks/whatsapp",
            content=json.dumps(body),
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=wrongsignature",
            },
        )
    assert response.status_code == 200  # always 200 so Meta doesn't retry forever
    assert response.json() == {"ok": False, "reason": "invalid_signature"}


async def test_status_update_moves_queued_message_to_delivered(
    db_session: AsyncSession, make_authenticated_client
):
    now = datetime.now(UTC)
    contact = WhatsAppContact(
        name="Alice", phone_number="919991234567", opted_in=True, created_at=now
    )
    db_session.add(contact)
    await db_session.flush()
    msg = WhatsAppQueuedMessage(
        contact_id=contact.id,
        message_type="text",
        payload={"body": "hi"},
        idempotency_key="k1",
        scheduled_send_at=now,
        status="SENT",
        provider_message_id="wamid.ABC",
        retry_count=0,
        max_retries=3,
        created_at=now,
        updated_at=now,
    )
    db_session.add(msg)
    await db_session.commit()

    body = {
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "statuses": [
                                {
                                    "id": "wamid.ABC",
                                    "status": "delivered",
                                    "recipient_id": "919991234567",
                                    "timestamp": "123",
                                }
                            ]
                        },
                    }
                ]
            }
        ]
    }
    raw, sig = _signed(body)
    client = await make_authenticated_client(db_session, permission_codes=[])
    async with client:
        response = await client.post(
            "/api/v1/webhooks/whatsapp",
            content=raw,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    await db_session.refresh(msg)
    assert msg.status == "DELIVERED"
    assert msg.delivered_at is not None


async def test_status_update_never_moves_backwards(
    db_session: AsyncSession, make_authenticated_client
):
    now = datetime.now(UTC)
    contact = WhatsAppContact(
        name="Alice", phone_number="919991234567", opted_in=True, created_at=now
    )
    db_session.add(contact)
    await db_session.flush()
    msg = WhatsAppQueuedMessage(
        contact_id=contact.id,
        message_type="text",
        payload={"body": "hi"},
        idempotency_key="k2",
        scheduled_send_at=now,
        status="READ",
        provider_message_id="wamid.XYZ",
        read_at=now,
        retry_count=0,
        max_retries=3,
        created_at=now,
        updated_at=now,
    )
    db_session.add(msg)
    await db_session.commit()

    body = {
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "statuses": [
                                {
                                    "id": "wamid.XYZ",
                                    "status": "sent",
                                    "recipient_id": "919991234567",
                                    "timestamp": "123",
                                }
                            ]
                        },
                    }
                ]
            }
        ]
    }
    raw, sig = _signed(body)
    client = await make_authenticated_client(db_session, permission_codes=[])
    async with client:
        await client.post(
            "/api/v1/webhooks/whatsapp",
            content=raw,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )
    await db_session.refresh(msg)
    assert msg.status == "READ"  # late "sent" callback did not regress it


async def test_inbound_message_opens_customer_window(
    db_session: AsyncSession, make_authenticated_client
):
    now = datetime.now(UTC)
    contact = WhatsAppContact(
        name="Alice", phone_number="919991234567", opted_in=True, created_at=now
    )
    db_session.add(contact)
    await db_session.commit()

    body = {
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "contacts": [
                                {"profile": {"name": "Alice"}, "wa_id": "919991234567"}
                            ],
                            "messages": [
                                {
                                    "from": "919991234567",
                                    "id": "wamid.IN",
                                    "timestamp": "123",
                                    "type": "text",
                                    "text": {"body": "Hi"},
                                }
                            ],
                        },
                    }
                ]
            }
        ]
    }
    raw, sig = _signed(body)
    client = await make_authenticated_client(db_session, permission_codes=[])
    async with client:
        await client.post(
            "/api/v1/webhooks/whatsapp",
            content=raw,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )

    window = (
        await db_session.execute(
            select(WhatsAppCustomerWindow).where(
                WhatsAppCustomerWindow.contact_id == contact.id
            )
        )
    ).scalar_one_or_none()
    assert window is not None
    assert datetime.now(UTC) - window.last_inbound_at < timedelta(minutes=1)
