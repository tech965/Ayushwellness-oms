"""API-first Shopify mode (`SHOPIFY_LIVE_MODE`): live reads and proof that
no Shopify-sourced record or payload reaches PostgreSQL.

External API is MOCKED (stub client) -- these tests do NOT verify against
a real Shopify store.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest
from app.core.config import settings
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.integrations.shopify.live import ShopifyLiveReader, build_orders_search
from app.integrations.shopify.live_webhooks import (
    clear_live_handlers,
    register_live_handler,
)
from app.models.customer import Customer
from app.models.enums import IntegrationStatus, IntegrationType
from app.models.integration import IntegrationCode, SyncJob, WebhookEvent
from app.models.order import Order, OrderEvent, OrderItem
from app.models.order_workflow import OrderWorkflow
from app.models.payment import Payment
from app.models.product import Product, ProductVariant
from app.repositories.integration import IntegrationRepository
from app.services.live_workflow_handlers import register_default_live_handlers
from app.tasks import sync_tasks
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_SECRET = "live-mode-secret"


class _StubClient:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def execute(self, query: str, variables: dict | None = None) -> dict:
        self.calls.append(variables or {})
        return self._responses.pop(0)


def _order_node(n: int) -> dict:
    return {
        "id": f"gid://shopify/Order/{n}",
        "name": f"#{1000 + n}",
        "createdAt": "2026-10-01T10:00:00Z",
        "updatedAt": "2026-10-01T11:00:00Z",
        "currencyCode": "INR",
        "displayFinancialStatus": "PAID",
        "displayFulfillmentStatus": "UNFULFILLED",
        "paymentGatewayNames": ["Cashfree"],
        "totalPriceSet": {"shopMoney": {"amount": "499.00"}},
        "lineItems": {"edges": []},
    }


def _orders_page(nodes: list[int], *, more: bool, cursor: str | None) -> dict:
    return {
        "orders": {
            "pageInfo": {"hasNextPage": more, "endCursor": cursor},
            "edges": [{"node": _order_node(n)} for n in nodes],
        }
    }


@pytest.fixture(autouse=True)
def _live_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "SHOPIFY_LIVE_MODE", True)
    monkeypatch.setattr(settings, "SHOPIFY_WEBHOOK_SECRET", _SECRET)
    yield
    clear_adapters()
    clear_live_handlers()
    register_default_live_handlers()


def _sign(body: bytes) -> str:
    return base64.b64encode(hmac.new(_SECRET.encode(), body, hashlib.sha256).digest()).decode()


async def _count(session: AsyncSession, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


# --- live reads ------------------------------------------------------------


async def test_list_orders_returns_normalized_page_without_raw_payload() -> None:
    stub = _StubClient([_orders_page([1, 2], more=True, cursor="c1")])
    reader = ShopifyLiveReader(ShopifyAdapter(client=stub))

    page = await reader.list_orders(limit=2)

    assert [o["order_number"] for o in page.items] == ["#1001", "#1002"]
    assert page.has_more is True and page.next_cursor == "c1"
    assert all("raw_external_payload" not in o for o in page.items)
    assert page.items[0]["payment_status"].value == "paid"


async def test_pagination_passes_cursor_and_search_filters_to_shopify() -> None:
    stub = _StubClient([_orders_page([3], more=False, cursor=None)])
    reader = ShopifyLiveReader(ShopifyAdapter(client=stub))

    page = await reader.list_orders(
        limit=25,
        cursor="c1",
        q="Asha",
        date_from=datetime(2026, 10, 1, tzinfo=UTC),
        date_to=datetime(2026, 10, 31, tzinfo=UTC),
    )

    assert page.has_more is False and page.next_cursor is None
    sent = stub.calls[0]
    assert sent["first"] == 25 and sent["after"] == "c1"
    assert "Asha" in sent["query"]
    assert "created_at:>='2026-10-01" in sent["query"]
    assert "created_at:<='2026-10-31" in sent["query"]


async def test_search_term_cannot_inject_extra_filters() -> None:
    q = build_orders_search(q='x" OR financial_status:paid \\')
    assert q is not None
    assert "\\" not in q
    assert q.count('"') == 2


async def test_iter_orders_flags_truncated_crawl_as_incomplete() -> None:
    stub = _StubClient(
        [
            _orders_page([1], more=True, cursor="c1"),
            _orders_page([2], more=True, cursor="c2"),
        ]
    )
    reader = ShopifyLiveReader(ShopifyAdapter(client=stub))

    result = await reader.iter_orders(max_pages=2)

    assert len(result.items) == 2
    assert result.incomplete is True and result.warnings


async def test_iter_orders_complete_when_exhausted() -> None:
    stub = _StubClient([_orders_page([1], more=False, cursor=None)])
    result = await ShopifyLiveReader(ShopifyAdapter(client=stub)).iter_orders()
    assert result.incomplete is False and not result.warnings


# --- write prevention ----------------------------------------------------------


async def _post(client: AsyncClient, body: dict, *, webhook_id: str, topic: str = "orders/create"):
    raw = json.dumps(body).encode()
    return await client.post(
        "/api/v1/webhooks/shopify",
        content=raw,
        headers={
            "X-Shopify-Topic": topic,
            "X-Shopify-Hmac-Sha256": _sign(raw),
            "X-Shopify-Webhook-Id": webhook_id,
            "Content-Type": "application/json",
        },
    )


async def test_live_webhook_persists_no_provider_data(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    body = {"id": 9001, "email": "private@example.com", "total_price": "10.00"}

    response = await _post(client, body, webhook_id="wh_live_1")

    assert response.status_code == 200
    assert response.json() == {"success": True}
    for model in (
        WebhookEvent,
        SyncJob,
        Order,
        OrderItem,
        OrderEvent,
        Customer,
        Payment,
        Product,
        ProductVariant,
    ):
        assert await _count(db_session, model) == 0, model.__name__
    # The only write is the OMS-owned workflow row: external id + default
    # state, with no trace of the payload.
    rows = (await db_session.execute(select(OrderWorkflow))).scalars().all()
    assert [r.external_order_id for r in rows] == ["9001"]
    assert "private@example.com" not in repr(
        {c.name: getattr(rows[0], c.name) for c in OrderWorkflow.__table__.columns}
    )


async def test_live_webhook_rejects_bad_signature(client: AsyncClient) -> None:
    raw = json.dumps({"id": 1}).encode()
    response = await client.post(
        "/api/v1/webhooks/shopify",
        content=raw,
        headers={
            "X-Shopify-Topic": "orders/create",
            "X-Shopify-Hmac-Sha256": "bad",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 401


async def test_live_webhook_duplicate_delivery_handled_once(client: AsyncClient) -> None:
    calls: list[str] = []

    async def handler(session, topic: str, payload: dict) -> None:
        calls.append(topic)

    register_live_handler("orders/create", handler)

    first = await _post(client, {"id": 1}, webhook_id="wh_dup")
    second = await _post(client, {"id": 1}, webhook_id="wh_dup")

    assert first.status_code == second.status_code == 200
    assert calls == ["orders/create"]


class _Ctx:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc) -> None:
        return None


async def test_scheduled_sync_enqueues_nothing_for_shopify_in_live_mode(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await IntegrationRepository(db_session).create(
        name="Shopify",
        code=IntegrationCode.SHOPIFY,
        type=IntegrationType.ECOMMERCE,
        status=IntegrationStatus.CONNECTED,
        enabled=True,
    )
    await db_session.commit()
    register_adapter(ShopifyAdapter(client=_StubClient([])))
    monkeypatch.setattr(sync_tasks, "AsyncSessionLocal", lambda: _Ctx(db_session))
    enqueued: list = []
    monkeypatch.setattr(sync_tasks.run_sync_task, "delay", lambda *a: enqueued.append(a))

    result = await sync_tasks._run_scheduled_sync()

    assert result == []
    assert enqueued == []

    # Control: the same setup does enqueue once live mode is off, so the
    # assertion above is proving the guard and not an inert fixture.
    monkeypatch.setattr(settings, "SHOPIFY_LIVE_MODE", False)
    assert await sync_tasks._run_scheduled_sync() != []
