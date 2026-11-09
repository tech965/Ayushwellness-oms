"""Live analytics, Excel export, gating of DB-derived views, and the
persistent webhook idempotency ledger (API-first mode).

Shopify is MOCKED. Nothing here is verified against a real store.
"""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO

import pytest
from app.core.config import settings
from app.db.session import get_db
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.integrations.shopify.live_webhooks import clear_live_handlers
from app.main import app
from app.models.enums import OrderStatus
from app.models.order_workflow import OrderWorkflow
from app.models.webhook_delivery import WebhookDelivery
from app.services.live_workflow_handlers import register_default_live_handlers
from app.services.order_ref import encode_shopify_order_ref
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import bearer_client, make_role, make_user
from tests.test_live_orders_workflow import _assert_no_provider_rows, _node
from tests.test_live_telecalling_workflow import RouterStub
from tests.test_shopify_live_mode import _count, _post

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _live(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "SHOPIFY_LIVE_MODE", True)
    monkeypatch.setattr(settings, "SHOPIFY_WEBHOOK_SECRET", "live-mode-secret")
    yield
    clear_adapters()
    clear_live_handlers()
    register_default_live_handlers()
    app.dependency_overrides.clear()


def _install(nodes: dict[int, dict]) -> RouterStub:
    stub = RouterStub(nodes)
    register_adapter(ShopifyAdapter(client=stub))
    return stub


async def _analyst(db_session: AsyncSession, perms: list[str]):
    role = await make_role(db_session, name="ANALYST", permission_codes=perms)
    return await make_user(db_session, email="analyst@example.com", role=role)


def _mixed_nodes() -> dict[int, dict]:
    nodes = {
        1: _node(1),  # prepaid, paid
        2: _node(2, gateway="Cash on Delivery (COD)", financial="PENDING"),
        3: _node(3),
    }
    nodes[3]["cancelledAt"] = "2026-10-05T00:00:00Z"
    nodes[2]["displayFulfillmentStatus"] = "FULFILLED"
    return nodes


# --- analytics ------------------------------------------------------------------------


async def test_live_summary_aggregates_orders_and_workflow(db_session: AsyncSession) -> None:
    user = await _analyst(db_session, ["analytics.read"])
    db_session.add(OrderWorkflow(external_order_id="1", status=OrderStatus.CONFIRMED))
    await db_session.commit()
    _install(_mixed_nodes())

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/analytics/live/summary")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["complete"] is True
    assert data["total_orders"] == 3
    assert data["cancelled_orders"] == 1
    assert float(data["revenue"]) == 998.0  # two non-cancelled orders x 499
    payment_types = {b["label"]: b["count"] for b in data["by_payment_type"]}
    assert payment_types == {"prepaid": 1, "cod": 1}
    fulfil = {b["label"]: b["count"] for b in data["by_fulfillment_status"]}
    assert fulfil == {"unfulfilled": 1, "fulfilled": 1}
    assert data["confirmed_orders"] == 1
    assert data["unassigned_orders"] == 3 and data["assigned_orders"] == 0
    assert data["top_products"] and data["daily"]
    assert any("Shiprocket" in note for note in data["unavailable"])  # limits stated, not hidden
    await _assert_no_provider_rows(db_session)


async def test_live_summary_flags_incomplete_totals(db_session: AsyncSession) -> None:
    user = await _analyst(db_session, ["analytics.read"])

    class Paged(RouterStub):
        async def execute(self, query, variables=None):  # noqa: ANN001
            result = await super().execute(query, variables)
            result["orders"]["pageInfo"] = {"hasNextPage": True, "endCursor": "more"}
            return result

    register_adapter(ShopifyAdapter(client=Paged({1: _node(1)})))

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/analytics/live/summary", params={"max_pages": 2})

    data = response.json()["data"]
    assert data["complete"] is False
    assert data["warnings"] and "incomplete" in data["warnings"][0]
    assert response.json()["message"].startswith("Incomplete")


async def test_db_derived_views_refuse_in_live_mode_instead_of_serving_stale_data(
    db_session: AsyncSession,
) -> None:
    user = await _analyst(db_session, ["analytics.read", "dashboard.read"])

    async with bearer_client(app, get_db, db_session, user.id) as client:
        analytics = await client.get("/api/v1/analytics/summary")
        dashboard = await client.get("/api/v1/dashboard/summary")

    assert analytics.status_code == 422 and "/analytics/live/summary" in analytics.text
    assert dashboard.status_code == 422


async def test_db_derived_views_still_work_in_legacy_mode(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SHOPIFY_LIVE_MODE", False)
    user = await _analyst(db_session, ["analytics.read"])

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/analytics/summary")

    assert response.status_code == 200, response.text


# --- Excel export -------------------------------------------------------------------------


async def test_live_export_builds_workbook_from_live_orders(db_session: AsyncSession) -> None:
    user = await _analyst(db_session, ["orders.read"])
    nodes = _mixed_nodes()
    nodes[1]["email"] = "buyer1@example.com"
    _install(nodes)

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/orders/export", params={"payment_type": "cod"})

    assert response.status_code == 200, response.text
    assert "X-Export-Incomplete" not in response.headers
    sheet = load_workbook(BytesIO(response.content))["Orders"]
    rows = list(sheet.iter_rows(values_only=True))
    assert rows[0][0] == "Order Number"
    assert [r[0] for r in rows[1:]] == ["#1002"]  # only the COD order
    await _assert_no_provider_rows(db_session)


async def test_live_export_marks_truncated_workbook_incomplete(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.v1.endpoints import orders as orders_endpoint

    monkeypatch.setattr(orders_endpoint, "EXPORT_MAX_PAGES", 1)
    user = await _analyst(db_session, ["orders.read"])

    class Paged(RouterStub):
        async def execute(self, query, variables=None):  # noqa: ANN001
            result = await super().execute(query, variables)
            result["orders"]["pageInfo"] = {"hasNextPage": True, "endCursor": "more"}
            return result

    register_adapter(ShopifyAdapter(client=Paged({1: _node(1)})))

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/orders/export")

    assert response.headers.get("X-Export-Incomplete") == "true"
    workbook = load_workbook(BytesIO(response.content))
    assert workbook.sheetnames[0] == "Export notes"
    assert "INCOMPLETE" in workbook["Export notes"]["A1"].value


async def test_live_export_rejects_shiprocket_dependent_filter(db_session: AsyncSession) -> None:
    user = await _analyst(db_session, ["orders.read"])

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(
            "/api/v1/orders/export", params={"shipment_status": "delivered"}
        )

    assert response.status_code == 422


# --- persistent webhook idempotency -----------------------------------------------------------


async def test_duplicate_delivery_is_detected_from_the_database_not_memory(
    db_session: AsyncSession, client
) -> None:
    first = await _post(client, {"id": 4001}, webhook_id="dup-1")
    # Simulated restart: nothing in process memory survives; only the ledger does.
    second = await _post(client, {"id": 4001}, webhook_id="dup-1")

    assert first.status_code == second.status_code == 200
    ledger = (await db_session.execute(select(WebhookDelivery))).scalars().all()
    assert [(r.provider, r.delivery_id, r.topic) for r in ledger] == [
        ("shopify", "dup-1", "orders/create")
    ]


async def test_delivery_ledger_stores_no_payload(db_session: AsyncSession, client) -> None:
    await _post(client, {"id": 4002, "email": "private@example.com"}, webhook_id="p-1")

    columns = {c.name for c in WebhookDelivery.__table__.columns}
    assert columns == {"id", "provider", "delivery_id", "topic", "received_at"}
    row = (await db_session.execute(select(WebhookDelivery))).scalar_one()
    assert "private@example.com" not in repr([getattr(row, c) for c in columns])


async def test_out_of_order_cancel_then_create_converges(db_session: AsyncSession, client) -> None:
    """`orders/cancelled` arrives before `orders/create`: the cancel finds no
    row (no-op), the create then makes one -- and the live status still
    reads cancelled from Shopify, so no state is lost."""
    _install({5: {**_node(5), "cancelledAt": "2026-10-05T00:00:00Z"}})
    await _post(client, {"id": 5}, webhook_id="o-1", topic="orders/cancelled")
    await _post(client, {"id": 5}, webhook_id="o-2", topic="orders/create")

    workflow = (await db_session.execute(select(OrderWorkflow))).scalar_one()
    assert workflow.external_order_id == "5"

    user = await _analyst(db_session, ["orders.read"])
    async with bearer_client(app, get_db, db_session, user.id) as api:
        detail = await api.get(f"/api/v1/orders/{encode_shopify_order_ref('5')}")
    assert detail.json()["data"]["status"] == "cancelled"
    assert await _count(db_session, WebhookDelivery) == 2


async def test_failed_delivery_releases_its_claim_for_retry(
    db_session: AsyncSession, client
) -> None:
    from app.integrations.shopify.live_webhooks import register_live_handler

    calls: list[int] = []

    async def flaky(session, topic, payload):  # noqa: ANN001
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")

    register_live_handler("orders/create", flaky)

    failed = await _post(client, {"id": 9}, webhook_id="r-1")
    retried = await _post(client, {"id": 9}, webhook_id="r-1")

    assert failed.status_code == 500
    assert retried.status_code == 200
    assert len(calls) == 2
    assert await _count(db_session, WebhookDelivery) == 1
    assert isinstance(datetime.now(UTC), datetime)
