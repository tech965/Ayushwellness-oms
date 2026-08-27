"""Shopify webhook endpoint: HMAC verification, idempotent ingestion,
and processing through the same `ENTITY_UPSERT_HANDLERS` dispatch table
`SyncService` uses.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from decimal import Decimal

import pytest
from app.core.config import settings
from app.integrations.entity_sync import ENTITY_UPSERT_HANDLERS
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.integrations.shopify.webhooks import verify_webhook_hmac
from app.models.enums import IntegrationStatus, IntegrationType, OrderStatus, PaymentStatus
from app.models.integration import IntegrationCode, WebhookEvent
from app.models.order import Order
from app.repositories.customer import CustomerRepository
from app.repositories.integration import IntegrationRepository
from app.services.webhook_service import WebhookService
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_SECRET = "test-shopify-webhook-secret"


@pytest.fixture(autouse=True)
def _configure_webhook_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "SHOPIFY_WEBHOOK_SECRET", _SECRET)
    yield
    clear_adapters()


def _sign(body: bytes, secret: str = _SECRET) -> str:
    return base64.b64encode(hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()).decode(
        "utf-8"
    )


async def _make_shopify_integration(session: AsyncSession):
    integration = await IntegrationRepository(session).create(
        name="Shopify",
        code=IntegrationCode.SHOPIFY,
        type=IntegrationType.ECOMMERCE,
        status=IntegrationStatus.DISCONNECTED,
        enabled=True,
    )
    await session.commit()
    return integration


# 21. Webhook signature validation
async def test_verify_webhook_hmac_accepts_correct_signature() -> None:
    body = b'{"id": 1}'
    signature = _sign(body)
    assert verify_webhook_hmac(raw_body=body, signature_header=signature, secret=_SECRET) is True


async def test_verify_webhook_hmac_rejects_tampered_body() -> None:
    body = b'{"id": 1}'
    signature = _sign(body)
    tampered = b'{"id": 2}'
    assert (
        verify_webhook_hmac(raw_body=tampered, signature_header=signature, secret=_SECRET) is False
    )


async def test_verify_webhook_hmac_rejects_missing_header_or_secret() -> None:
    body = b'{"id": 1}'
    assert verify_webhook_hmac(raw_body=body, signature_header=None, secret=_SECRET) is False
    assert verify_webhook_hmac(raw_body=body, signature_header=_sign(body), secret="") is False


async def test_valid_webhook_signature_is_accepted(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_shopify_integration(db_session)
    body = json.dumps({"id": 123456, "email": "customer@example.com"}).encode()

    response = await client.post(
        "/api/v1/webhooks/shopify",
        content=body,
        headers={
            "X-Shopify-Topic": "customers/update",
            "X-Shopify-Hmac-Sha256": _sign(body),
            "X-Shopify-Webhook-Id": "wh_1",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True


# 22. Invalid webhook rejection
async def test_invalid_webhook_signature_is_rejected(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_shopify_integration(db_session)
    body = json.dumps({"id": 1}).encode()

    response = await client.post(
        "/api/v1/webhooks/shopify",
        content=body,
        headers={
            "X-Shopify-Topic": "orders/create",
            "X-Shopify-Hmac-Sha256": "not-a-valid-signature",
        },
    )

    assert response.status_code == 401

    total = await db_session.execute(select(func.count()).select_from(WebhookEvent))
    assert total.scalar_one() == 0


async def test_missing_signature_header_is_rejected(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_shopify_integration(db_session)
    response = await client.post(
        "/api/v1/webhooks/shopify",
        content=b'{"id": 1}',
        headers={"X-Shopify-Topic": "orders/create"},
    )
    assert response.status_code == 401


# 24. Duplicate webhook handling
async def test_duplicate_webhook_delivery_creates_only_one_event(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_shopify_integration(db_session)
    body = json.dumps({"id": "gid://shopify/Customer/600", "email": "dup@example.com"}).encode()
    headers = {
        "X-Shopify-Topic": "customers/update",
        "X-Shopify-Hmac-Sha256": _sign(body),
        "X-Shopify-Webhook-Id": "wh_dup",
    }

    first = await client.post("/api/v1/webhooks/shopify", content=body, headers=headers)
    second = await client.post("/api/v1/webhooks/shopify", content=body, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200

    total = await db_session.execute(select(func.count()).select_from(WebhookEvent))
    assert total.scalar_one() == 1


# 23. Webhook processing
async def test_webhook_processing_persists_the_normalized_customer(
    db_session: AsyncSession,
) -> None:
    """Exercises the same pipeline `app.tasks.webhook_processing` runs
    (WebhookService -> adapter.process_webhook -> ENTITY_UPSERT_HANDLERS)
    directly against the test's session, since the Celery task itself
    opens its own production session factory and isn't unit-testable
    against an isolated in-memory database.

    Payload is REST-shaped (snake_case, plain int id) — the actual shape
    a Shopify webhook delivers, not the GraphQL node shape the pull-sync
    adapter fetches. Round 4 found this distinction matters: the old
    version of this test used a hand-crafted GraphQL-shaped payload,
    which is why the REST-shape crash (see `webhook_shapes.py`) went
    undetected until it was checked against a real webhook delivery.
    """
    register_adapter(ShopifyAdapter(client=None))
    integration = await _make_shopify_integration(db_session)

    payload = {
        "id": 500,
        "first_name": "Sam",
        "last_name": "Reyes",
        "email": "sam@example.com",
        "phone": None,
        "state": "enabled",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "default_address": None,
        "addresses": [],
    }

    webhook_service = WebhookService(db_session)
    event, created = await webhook_service.ingest(
        integration_id=integration.id,
        event_type="customers/create",
        payload=payload,
        external_event_id="wh_evt_1",
    )
    assert created is True

    await webhook_service.mark_processing(event.id)
    from app.integrations.registry import get_adapter

    adapter = get_adapter(integration.code)
    result = await adapter.process_webhook(event.event_type, event.payload)
    handler = ENTITY_UPSERT_HANDLERS[result["entity_type"]]
    await handler(db_session, result["normalized"])
    await webhook_service.mark_processed(event.id)

    customer = await CustomerRepository(db_session).get_by_source_external_id(
        source_system="shopify", external_id="500"
    )
    assert customer is not None
    assert customer.email == "sam@example.com"

    refreshed_event = await webhook_service.get_event(event.id)
    assert refreshed_event.status == "processed"
    assert refreshed_event.processed_at is not None


# Round 4 — full order lifecycle through the real webhook->processing
# pipeline, using REST-shaped payloads (the real Shopify shape) end to
# end: orders/create, orders/updated, orders/cancelled must all resolve
# to the SAME OMS order (same external_id), never create a second one.
async def test_order_webhook_lifecycle_create_update_cancel_touches_exactly_one_order(
    db_session: AsyncSession,
) -> None:
    from app.repositories.order import OrderRepository

    register_adapter(ShopifyAdapter(client=None))
    integration = await _make_shopify_integration(db_session)
    adapter = ShopifyAdapter(client=None)

    async def _deliver(topic: str, payload: dict, webhook_id: str) -> None:
        webhook_service = WebhookService(db_session)
        event, created = await webhook_service.ingest(
            integration_id=integration.id,
            event_type=topic,
            payload=payload,
            external_event_id=webhook_id,
        )
        assert created is True
        await webhook_service.mark_processing(event.id)
        result = await adapter.process_webhook(event.event_type, event.payload)
        handler = ENTITY_UPSERT_HANDLERS[result["entity_type"]]
        await handler(db_session, result["normalized"])
        await webhook_service.mark_processed(event.id)

    base_order = {
        "id": 900123,
        "name": "#WEBHOOK-LIFECYCLE-1",
        "created_at": "2026-08-27T10:00:00+05:30",
        "updated_at": "2026-08-27T10:00:00+05:30",
        "cancelled_at": None,
        "currency": "INR",
        "financial_status": "pending",
        "fulfillment_status": None,
        "subtotal_price": "500.00",
        "total_tax": "0.00",
        "total_discounts": "0.00",
        "total_price": "500.00",
        "payment_gateway_names": ["cash on delivery (COD)"],
        "customer": {},
        "line_items": [
            {"id": 1, "sku": "SKU-1", "title": "Item", "quantity": 1, "price": "500.00",
             "total_discount": "0.00", "variant_id": None},
        ],
        "shipping_lines": [],
        "shipping_address": None,
        "billing_address": None,
    }

    # 1. orders/create
    await _deliver("orders/create", base_order, "wh_lifecycle_create")
    orders_repo = OrderRepository(db_session)
    order = await orders_repo.get_by_source_external_id(
        source_system="shopify", external_id="900123"
    )
    assert order is not None
    assert order.total_amount == Decimal("500.00")
    assert order.status != OrderStatus.CANCELLED
    order_pk = order.id

    total_after_create = await db_session.execute(
        select(func.count()).select_from(Order).where(Order.external_id == "900123")
    )
    assert total_after_create.scalar_one() == 1

    # 2. orders/updated — financial status changes, order count must NOT increase
    updated_order = {
        **base_order,
        "financial_status": "paid",
        "updated_at": "2026-08-27T10:05:00+05:30",
    }
    await _deliver("orders/updated", updated_order, "wh_lifecycle_update")

    total_after_update = await db_session.execute(
        select(func.count()).select_from(Order).where(Order.external_id == "900123")
    )
    assert total_after_update.scalar_one() == 1
    order = await orders_repo.get_by_source_external_id(
        source_system="shopify", external_id="900123"
    )
    assert order.id == order_pk  # same row, not a new one
    assert order.payment_status == PaymentStatus.PAID

    # 3. orders/cancelled — must update the SAME order, not create a second one
    cancelled_order = {
        **base_order,
        "cancelled_at": "2026-08-27T10:10:00+05:30",
        "updated_at": "2026-08-27T10:10:00+05:30",
    }
    await _deliver("orders/cancelled", cancelled_order, "wh_lifecycle_cancel")

    total_after_cancel = await db_session.execute(
        select(func.count()).select_from(Order).where(Order.external_id == "900123")
    )
    assert total_after_cancel.scalar_one() == 1
    order = await orders_repo.get_by_source_external_id(
        source_system="shopify", external_id="900123"
    )
    assert order.id == order_pk
    assert order.status == OrderStatus.CANCELLED


async def test_product_webhook_payload_normalizes_without_crashing(
    db_session: AsyncSession,
) -> None:
    """REST-shaped `variants`/`options` (flat, `option1`/`option2`, not
    `selectedOptions`) — the same shape gap that broke orders/customers.
    """
    from app.repositories.product import ProductRepository

    register_adapter(ShopifyAdapter(client=None))
    integration = await _make_shopify_integration(db_session)
    adapter = ShopifyAdapter(client=None)

    payload = {
        "id": 700555,
        "title": "Herbal Masala",
        "body_html": "<p>desc</p>",
        "vendor": "Aayush",
        "product_type": "Wellness",
        "tags": "ayurveda, wellness",
        "status": "active",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "options": [{"name": "Size", "position": 1, "values": ["60"]}],
        "variants": [
            {"id": 8001, "sku": "AW-HM-PN-60", "title": "60", "price": "649.00",
             "compare_at_price": None, "inventory_quantity": 10, "weight": 0.5,
             "barcode": None, "option1": "60"},
        ],
    }

    webhook_service = WebhookService(db_session)
    event, created = await webhook_service.ingest(
        integration_id=integration.id,
        event_type="products/create",
        payload=payload,
        external_event_id="wh_product_1",
    )
    assert created is True
    result = await adapter.process_webhook(event.event_type, event.payload)
    handler = ENTITY_UPSERT_HANDLERS[result["entity_type"]]
    await handler(db_session, result["normalized"])

    product = await ProductRepository(db_session).get_by_source_external_id(
        source_system="shopify", external_id="700555"
    )
    assert product is not None
    assert product.title == "Herbal Masala"


async def test_webhook_events_endpoint_never_returns_raw_payload(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    integration = await _make_shopify_integration(db_session)
    await WebhookService(db_session).ingest(
        integration_id=integration.id,
        event_type="orders/create",
        payload={"id": "1", "sensitive": "should-not-leak"},
        external_event_id="wh_evt_2",
    )

    async with await make_authenticated_client(
        db_session, permission_codes=["webhooks.read"]
    ) as authed_client:
        response = await authed_client.get("/api/v1/webhook-events")
        assert response.status_code == 200
        assert "should-not-leak" not in response.text
