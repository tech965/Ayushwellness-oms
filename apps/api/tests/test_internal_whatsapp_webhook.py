"""Internal Sales-CRM -> OMS WhatsApp webhook forwarding endpoint:
service-to-service auth, delivery-digest idempotency, transactional
rollback on failure, and parity with the public Meta-facing webhook's
processing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.core.config import settings
from app.models.webhook_delivery import WebhookDelivery
from app.models.whatsapp_automation import (
    WhatsAppContact,
    WhatsAppCustomerWindow,
    WhatsAppQueuedMessage,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

URL = "/api/v1/internal/whatsapp/forwarded-event"


@pytest.fixture(autouse=True)
def _forward_secret(monkeypatch):
    monkeypatch.setattr(settings, "SALES_CRM_FORWARD_SECRET", "test-forward-secret")
    yield


def _status_body(message_id: str, status: str) -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "statuses": [
                                {
                                    "id": message_id,
                                    "status": status,
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


async def _make_message(
    db_session: AsyncSession, provider_message_id: str
) -> WhatsAppQueuedMessage:
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
        idempotency_key=f"k-{provider_message_id}",
        scheduled_send_at=now,
        status="SENT",
        provider_message_id=provider_message_id,
        retry_count=0,
        max_retries=3,
        created_at=now,
        updated_at=now,
    )
    db_session.add(msg)
    await db_session.commit()
    return msg


# --- authentication ---------------------------------------------------


async def test_valid_bearer_token_is_accepted(db_session: AsyncSession, make_authenticated_client):
    client = await make_authenticated_client(db_session, permission_codes=[])
    async with client:
        response = await client.post(
            URL,
            json={"payload": {"entry": []}, "delivery_digest": "sha256:abc"},
            headers={"Authorization": "Bearer test-forward-secret"},
        )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "duplicate": False}


async def test_missing_authorization_header_is_rejected(
    db_session: AsyncSession, make_authenticated_client
):
    client = await make_authenticated_client(db_session, permission_codes=[])
    async with client:
        response = await client.post(
            URL, json={"payload": {"entry": []}, "delivery_digest": "sha256:abc"}
        )
    assert response.status_code == 401


async def test_wrong_bearer_token_is_rejected(db_session: AsyncSession, make_authenticated_client):
    client = await make_authenticated_client(db_session, permission_codes=[])
    async with client:
        response = await client.post(
            URL,
            json={"payload": {"entry": []}, "delivery_digest": "sha256:abc"},
            headers={"Authorization": "Bearer wrong-secret"},
        )
    assert response.status_code == 401


async def test_rejected_when_secret_not_configured(
    db_session: AsyncSession, make_authenticated_client, monkeypatch
):
    monkeypatch.setattr(settings, "SALES_CRM_FORWARD_SECRET", None)
    client = await make_authenticated_client(db_session, permission_codes=[])
    async with client:
        response = await client.post(
            URL,
            json={"payload": {"entry": []}, "delivery_digest": "sha256:abc"},
            headers={"Authorization": "Bearer anything"},
        )
    assert response.status_code == 401


async def test_endpoint_never_requires_meta_signature_header(
    db_session: AsyncSession, make_authenticated_client
):
    """No X-Hub-Signature-256 is sent at all -- this is the internal,
    bearer-authenticated path, not Meta's own scheme.
    """
    client = await make_authenticated_client(db_session, permission_codes=[])
    async with client:
        response = await client.post(
            URL,
            json={"payload": {"entry": []}, "delivery_digest": "sha256:no-sig-needed"},
            headers={"Authorization": "Bearer test-forward-secret"},
        )
    assert response.status_code == 200


# --- processing parity with the public webhook -------------------------


async def test_forwarded_status_update_applies_same_as_direct_meta(
    db_session: AsyncSession, make_authenticated_client
):
    msg = await _make_message(db_session, "wamid.FWD1")
    body = _status_body("wamid.FWD1", "delivered")
    client = await make_authenticated_client(db_session, permission_codes=[])
    async with client:
        response = await client.post(
            URL,
            json={"payload": body, "delivery_digest": "sha256:fwd-status-1"},
            headers={"Authorization": "Bearer test-forward-secret"},
        )
    assert response.status_code == 200
    await db_session.refresh(msg)
    assert msg.status == "DELIVERED"
    assert msg.delivered_at is not None


async def test_forwarded_inbound_message_opens_customer_window(
    db_session: AsyncSession, make_authenticated_client
):
    now = datetime.now(UTC)
    contact = WhatsAppContact(
        name="Bob", phone_number="919991111111", opted_in=True, created_at=now
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
                            "contacts": [{"profile": {"name": "Bob"}, "wa_id": "919991111111"}],
                            "messages": [
                                {
                                    "from": "919991111111",
                                    "id": "wamid.FWDIN",
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
    client = await make_authenticated_client(db_session, permission_codes=[])
    async with client:
        await client.post(
            URL,
            json={"payload": body, "delivery_digest": "sha256:fwd-inbound-1"},
            headers={"Authorization": "Bearer test-forward-secret"},
        )

    window = (
        await db_session.execute(
            select(WhatsAppCustomerWindow).where(WhatsAppCustomerWindow.contact_id == contact.id)
        )
    ).scalar_one_or_none()
    assert window is not None
    assert datetime.now(UTC) - window.last_inbound_at < timedelta(minutes=1)


# --- delivery-digest idempotency ---------------------------------------


async def test_duplicate_delivery_digest_is_a_safe_noop(
    db_session: AsyncSession, make_authenticated_client
):
    msg = await _make_message(db_session, "wamid.DUP1")
    body = _status_body("wamid.DUP1", "delivered")
    client = await make_authenticated_client(db_session, permission_codes=[])
    async with client:
        first = await client.post(
            URL,
            json={"payload": body, "delivery_digest": "sha256:dup-1"},
            headers={"Authorization": "Bearer test-forward-secret"},
        )
        assert first.json() == {"ok": True, "duplicate": False}

        second = await client.post(
            URL,
            json={"payload": body, "delivery_digest": "sha256:dup-1"},
            headers={"Authorization": "Bearer test-forward-secret"},
        )
        assert second.status_code == 200
        assert second.json() == {"ok": True, "duplicate": True}

    delivery_rows = (
        await db_session.execute(
            select(WebhookDelivery).where(WebhookDelivery.delivery_id == "sha256:dup-1")
        )
    ).scalars().all()
    assert len(delivery_rows) == 1  # not duplicated in the ledger either

    await db_session.refresh(msg)
    assert msg.status == "DELIVERED"  # applied exactly once


async def test_delivery_digest_shared_across_meta_and_forwarded_paths(
    db_session: AsyncSession, make_authenticated_client
):
    """The same digest arriving once via Sales-CRM's forward and once
    (hypothetically) directly from Meta must not double-apply -- both
    paths share the same WebhookDelivery ledger (provider='whatsapp').
    """
    import hashlib
    import json

    await _make_message(db_session, "wamid.SHARED1")
    body = _status_body("wamid.SHARED1", "delivered")
    raw = json.dumps(body).encode()
    digest = f"sha256:{hashlib.sha256(raw).hexdigest()}"

    client = await make_authenticated_client(db_session, permission_codes=[])
    async with client:
        # Forwarded first.
        fwd = await client.post(
            URL,
            json={"payload": body, "delivery_digest": digest},
            headers={"Authorization": "Bearer test-forward-secret"},
        )
        assert fwd.json()["duplicate"] is False

    delivery_rows = (
        await db_session.execute(
            select(WebhookDelivery).where(WebhookDelivery.delivery_id == digest)
        )
    ).scalars().all()
    assert len(delivery_rows) == 1
    assert delivery_rows[0].provider == "whatsapp"


# --- transactional rollback on failure ----------------------------------


async def test_transaction_rolls_back_on_processing_failure(
    db_session: AsyncSession, make_authenticated_client, monkeypatch
):
    """If processing raises partway through, the delivery-digest row
    must NOT persist either -- otherwise a genuine retry would be
    silently treated as already-handled.
    """
    import app.services.whatsapp_webhook_processor as processor_module

    def boom(*args, **kwargs):
        raise RuntimeError("simulated processing failure")

    monkeypatch.setattr(processor_module, "parse_webhook_payload", boom)

    client = await make_authenticated_client(db_session, permission_codes=[])
    async with client:
        response = await client.post(
            URL,
            json={"payload": {"entry": []}, "delivery_digest": "sha256:fail-1"},
            headers={"Authorization": "Bearer test-forward-secret"},
        )
    assert response.status_code == 500

    delivery_rows = (
        await db_session.execute(
            select(WebhookDelivery).where(WebhookDelivery.delivery_id == "sha256:fail-1")
        )
    ).scalars().all()
    assert len(delivery_rows) == 0  # rolled back, not half-committed
