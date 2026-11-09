"""API-first order listing + internal workflow (`SHOPIFY_LIVE_MODE`).

Covers: reversible order references, the live `GET /orders` merged with
`order_workflow`, assignment / bulk assignment / round-robin against live
orders, workflow webhook side effects, and database write prevention.

Shopify is MOCKED (stub client). Nothing here is verified against a real
Shopify store.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from app.core.config import settings
from app.db.session import get_db
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.integrations.shopify.live_webhooks import (
    clear_live_handlers,
    register_live_handler,
)
from app.main import app
from app.models.customer import Customer
from app.models.enums import AssignmentStatus, OrderStatus
from app.models.integration import SyncJob, WebhookEvent
from app.models.order import Order, OrderItem
from app.models.order_workflow import OrderWorkflow
from app.models.payment import Payment
from app.models.product import Product, ProductVariant
from app.models.telecalling import OrderAssignment
from app.services.live_workflow_handlers import register_default_live_handlers
from app.services.order_ref import (
    decode_shopify_order_ref,
    encode_shopify_order_ref,
    resolve_ref,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import bearer_client, make_role, make_user
from tests.test_shopify_live_mode import _count, _post, _StubClient

pytestmark = pytest.mark.asyncio

_PROVIDER_TABLES = (
    WebhookEvent,
    SyncJob,
    Order,
    OrderItem,
    Customer,
    Payment,
    Product,
    ProductVariant,
)


def _node(n: int, *, gateway: str = "Cashfree", financial: str = "PAID", tags=None) -> dict:
    return {
        "id": f"gid://shopify/Order/{n}",
        "name": f"#{1000 + n}",
        "createdAt": f"2026-10-{(n % 28) + 1:02d}T10:00:00Z",
        "updatedAt": f"2026-10-{(n % 28) + 1:02d}T11:00:00Z",
        "currencyCode": "INR",
        "displayFinancialStatus": financial,
        "displayFulfillmentStatus": "UNFULFILLED",
        "paymentGatewayNames": [gateway],
        "tags": tags or [],
        "totalPriceSet": {"shopMoney": {"amount": "499.00"}},
        "shippingAddress": {"name": f"Buyer {n}", "phone": "9000000000", "city": "Pune"},
        "lineItems": {
            "edges": [
                {
                    "node": {
                        "id": f"gid://shopify/LineItem/{n}",
                        "sku": f"SKU-{n}",
                        "title": f"Item {n}",
                        "quantity": 2,
                        "originalUnitPriceSet": {"shopMoney": {"amount": "249.50"}},
                        "discountedTotalSet": {"shopMoney": {"amount": "499.00"}},
                    }
                }
            ]
        },
    }


def _page(nodes: list[dict], *, more: bool = False, cursor: str | None = None) -> dict:
    return {
        "orders": {
            "pageInfo": {"hasNextPage": more, "endCursor": cursor},
            "edges": [{"node": n} for n in nodes],
        }
    }


@pytest.fixture(autouse=True)
def _live(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "SHOPIFY_LIVE_MODE", True)
    monkeypatch.setattr(settings, "SHOPIFY_WEBHOOK_SECRET", "live-mode-secret")
    yield
    clear_adapters()
    clear_live_handlers()
    register_default_live_handlers()
    app.dependency_overrides.clear()


def _install(stub: _StubClient) -> None:
    register_adapter(ShopifyAdapter(client=stub))


async def _reader(db_session: AsyncSession, perms: list[str]):
    role = await make_role(db_session, name="OPS", permission_codes=perms)
    return await make_user(db_session, email="ops@example.com", role=role)


async def _assert_no_provider_rows(db_session: AsyncSession) -> None:
    for model in _PROVIDER_TABLES:
        assert await _count(db_session, model) == 0, model.__name__


# --- references --------------------------------------------------------------


async def test_order_ref_round_trips_and_rejects_foreign_uuids() -> None:
    ref = encode_shopify_order_ref("7123456789012")
    assert decode_shopify_order_ref(ref) == "7123456789012"
    assert decode_shopify_order_ref(uuid.uuid4()) is None


async def test_resolve_ref_prefers_existing_workflow_id(db_session: AsyncSession) -> None:
    legacy_id = uuid.uuid4()
    db_session.add(OrderWorkflow(id=legacy_id, external_order_id="555"))
    await db_session.commit()

    by_legacy = await resolve_ref(db_session, legacy_id)
    by_encoded = await resolve_ref(db_session, encode_shopify_order_ref("555"))

    assert by_legacy.external_order_id == "555"
    assert by_encoded.ref_id == legacy_id  # existing row wins over the encoded id
    assert await resolve_ref(db_session, uuid.uuid4()) is None


# --- live listing ----------------------------------------------------------------


async def test_live_list_merges_workflow_state_and_writes_nothing(
    db_session: AsyncSession,
) -> None:
    ops = await _reader(db_session, ["orders.read"])
    legacy_id = uuid.uuid4()
    db_session.add(
        OrderWorkflow(
            id=legacy_id,
            external_order_id="1",
            status=OrderStatus.CONFIRMED,
            notes="call after 6pm",
            confirmed_by_telecaller_id=ops.id,
            confirmed_at=datetime(2026, 10, 2, tzinfo=UTC),
        )
    )
    await db_session.commit()
    _install(_StubClient([_page([_node(1), _node(2)])]))

    async with bearer_client(app, get_db, db_session, ops.id) as client:
        response = await client.get("/api/v1/orders")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    by_number = {o["order_number"]: o for o in data}
    merged = by_number["#1001"]
    assert merged["id"] == str(legacy_id)  # legacy reference preserved
    assert merged["status"] == "confirmed"
    assert merged["notes"] == "call after 6pm"
    assert merged["confirmed_by_telecaller_name"] == "Test User"
    assert merged["customer_name"] == "Buyer 1"
    assert merged["item_summary"] == "Item 1"
    assert merged["total_quantity"] == 2
    fresh = by_number["#1002"]
    assert fresh["id"] == str(encode_shopify_order_ref("2"))
    assert fresh["status"] == "pending"
    assert fresh["shopify_order_id"] == "2"
    await _assert_no_provider_rows(db_session)
    assert await _count(db_session, OrderWorkflow) == 1  # listing never creates rows


async def test_live_list_pushes_search_and_dates_to_shopify(db_session: AsyncSession) -> None:
    ops = await _reader(db_session, ["orders.read"])
    stub = _StubClient([_page([_node(3)])])
    _install(stub)

    async with bearer_client(app, get_db, db_session, ops.id) as client:
        response = await client.get(
            "/api/v1/orders",
            params={
                "q": "Asha",
                "date_from": "2026-10-01T00:00:00+00:00",
                "date_to": "2026-10-31T00:00:00+00:00",
            },
        )

    assert response.status_code == 200, response.text
    sent = stub.calls[0]["query"]
    assert (
        "Asha" in sent and "created_at:>='2026-10-01" in sent and "created_at:<='2026-10-31" in sent
    )


async def test_live_list_applies_in_memory_filters(db_session: AsyncSession) -> None:
    ops = await _reader(db_session, ["orders.read"])
    _install(
        _StubClient(
            [
                _page(
                    [
                        _node(1, gateway="Cash on Delivery (COD)", financial="PENDING"),
                        _node(2),
                        _node(3, gateway="Cash on Delivery (COD)", financial="PENDING"),
                    ]
                )
            ]
        )
    )

    async with bearer_client(app, get_db, db_session, ops.id) as client:
        response = await client.get("/api/v1/orders", params={"payment_type": "cod"})

    numbers = sorted(o["order_number"] for o in response.json()["data"])
    assert numbers == ["#1001", "#1003"]


async def test_live_list_pagination_emulates_offset_pages(db_session: AsyncSession) -> None:
    ops = await _reader(db_session, ["orders.read"])
    nodes = [_node(n) for n in range(1, 6)]
    _install(_StubClient([_page(nodes)]))

    async with bearer_client(app, get_db, db_session, ops.id) as client:
        response = await client.get("/api/v1/orders", params={"page": 2, "page_size": 2})

    body = response.json()
    assert [o["order_number"] for o in body["data"]] == ["#1003", "#1004"]
    assert body["meta"]["page"] == 2
    assert body["meta"]["has_more"] is True
    assert body["meta"]["total_is_estimate"] is True


async def test_live_list_flags_incomplete_crawl(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import live_order_service

    monkeypatch.setattr(live_order_service, "MAX_CRAWL_PAGES", 2)
    ops = await _reader(db_session, ["orders.read"])
    # Only matching orders are COD; none on the first 2 pages -> truncated.
    _install(
        _StubClient(
            [
                _page([_node(1)], more=True, cursor="c1"),
                _page([_node(2)], more=True, cursor="c2"),
            ]
        )
    )

    async with bearer_client(app, get_db, db_session, ops.id) as client:
        response = await client.get("/api/v1/orders", params={"payment_type": "cod"})

    meta = response.json()["meta"]
    assert response.json()["data"] == []
    assert meta["total_is_estimate"] is True
    assert meta["warnings"] and "incomplete" in meta["warnings"][0]


async def test_live_list_status_filter_uses_workflow_state(db_session: AsyncSession) -> None:
    ops = await _reader(db_session, ["orders.read"])
    db_session.add(OrderWorkflow(external_order_id="2", status=OrderStatus.CONFIRMED))
    await db_session.commit()
    _install(_StubClient([_page([_node(1), _node(2)])]))

    async with bearer_client(app, get_db, db_session, ops.id) as client:
        response = await client.get("/api/v1/orders", params={"status": "confirmed"})

    assert [o["order_number"] for o in response.json()["data"]] == ["#1002"]


async def test_live_list_confirmed_only_is_workflow_driven(db_session: AsyncSession) -> None:
    ops = await _reader(db_session, ["orders.read"])
    db_session.add_all(
        [
            OrderWorkflow(
                external_order_id="2",
                status=OrderStatus.CONFIRMED,
                confirmed_by_telecaller_id=ops.id,
                confirmed_at=datetime(2026, 10, 3, tzinfo=UTC),
            ),
            OrderWorkflow(external_order_id="9"),  # not confirmed
        ]
    )
    await db_session.commit()
    stub = _StubClient([_page([_node(2)])])
    _install(stub)

    async with bearer_client(app, get_db, db_session, ops.id) as client:
        response = await client.get("/api/v1/orders", params={"confirmed_only": "true"})

    body = response.json()
    assert [o["order_number"] for o in body["data"]] == ["#1002"]
    assert body["meta"]["total_items"] == 1
    assert "id:2" in stub.calls[0]["query"] and "id:9" not in stub.calls[0]["query"]


async def test_live_list_rejects_shiprocket_dependent_filters(db_session: AsyncSession) -> None:
    ops = await _reader(db_session, ["orders.read"])
    _install(_StubClient([]))

    async with bearer_client(app, get_db, db_session, ops.id) as client:
        response = await client.get("/api/v1/orders", params={"shipment_status": "delivered"})

    assert response.status_code == 422
    assert "shipment_status" in response.text


# --- assignment / bulk / round-robin ----------------------------------------------


async def _team(db_session: AsyncSession, telecallers: int):
    lead_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    tc_role = await make_role(db_session, name="TELECALLER", permission_codes=["calls.manage"])
    leader = await make_user(db_session, email="leader@example.com", role=lead_role)
    tcs = [
        await make_user(
            db_session, email=f"tc{i}@example.com", role=tc_role, team_leader_id=leader.id
        )
        for i in range(telecallers)
    ]
    return leader, tcs


async def test_bulk_round_robin_assignment_of_live_orders(db_session: AsyncSession) -> None:
    leader, tcs = await _team(db_session, 2)
    _install(_StubClient([_page([_node(n) for n in (1, 2, 3, 4)])]))
    refs = [str(encode_shopify_order_ref(str(n))) for n in (1, 2, 3, 4)]

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        response = await client.post(
            "/api/v1/team/orders/assign",
            json={
                "order_ids": refs,
                "mode": "equal",
                "telecaller_ids": [str(t.id) for t in tcs],
            },
        )

    assert response.status_code in (200, 201), response.text
    rows = (await db_session.execute(select(OrderAssignment))).scalars().all()
    assert len(rows) == 4
    assert sorted(str(r.order_id) for r in rows) == sorted(refs)
    per_telecaller = {}
    for r in rows:
        per_telecaller[r.assigned_to] = per_telecaller.get(r.assigned_to, 0) + 1
    assert sorted(per_telecaller.values()) == [2, 2]  # round-robin split
    assert all(r.assignment_status == AssignmentStatus.ACTIVE for r in rows)
    workflows = (await db_session.execute(select(OrderWorkflow))).scalars().all()
    assert sorted(w.external_order_id for w in workflows) == ["1", "2", "3", "4"]
    await _assert_no_provider_rows(db_session)


async def test_assignment_rejects_duplicate_and_unknown_live_orders(
    db_session: AsyncSession,
) -> None:
    leader, (tc,) = await _team(db_session, 1)
    stub = _StubClient([_page([_node(1)]), _page([_node(1)]), _page([])])
    _install(stub)
    ref1 = str(encode_shopify_order_ref("1"))
    ref_missing = str(encode_shopify_order_ref("404"))
    body = {"mode": "manual", "telecaller_id": str(tc.id)}

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        first = await client.post("/api/v1/team/orders/assign", json={**body, "order_ids": [ref1]})
        again = await client.post("/api/v1/team/orders/assign", json={**body, "order_ids": [ref1]})
        missing = await client.post(
            "/api/v1/team/orders/assign", json={**body, "order_ids": [ref_missing]}
        )

    assert first.status_code in (200, 201), first.text
    assert again.status_code == 409
    assert missing.status_code == 404
    assert await _count(db_session, OrderAssignment) == 1
    assert await _count(db_session, OrderWorkflow) == 1  # no phantom row for the missing order


async def test_backfilled_workflow_order_assigns_without_shopify_call(
    db_session: AsyncSession,
) -> None:
    leader, (tc,) = await _team(db_session, 1)
    legacy_id = uuid.uuid4()
    db_session.add(OrderWorkflow(id=legacy_id, external_order_id="77"))
    await db_session.commit()
    stub = _StubClient([])  # any Shopify call would pop from an empty list and fail
    _install(stub)

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        response = await client.post(
            "/api/v1/team/orders/assign",
            json={"order_ids": [str(legacy_id)], "mode": "manual", "telecaller_id": str(tc.id)},
        )

    assert response.status_code in (200, 201), response.text
    assert stub.calls == []
    row = (await db_session.execute(select(OrderAssignment))).scalar_one()
    assert row.order_id == legacy_id


# --- webhook workflow side effects --------------------------------------------------


async def test_webhook_order_create_is_idempotent_across_deliveries(
    db_session: AsyncSession, client
) -> None:
    first = await _post(client, {"id": 321}, webhook_id="d1")
    second = await _post(client, {"id": 321}, webhook_id="d2")  # distinct delivery, same order
    updated = await _post(client, {"id": 321}, webhook_id="d3", topic="orders/updated")

    assert first.status_code == second.status_code == updated.status_code == 200
    rows = (await db_session.execute(select(OrderWorkflow))).scalars().all()
    assert [r.external_order_id for r in rows] == ["321"]
    assert rows[0].id == encode_shopify_order_ref("321")
    await _assert_no_provider_rows(db_session)


async def test_webhook_cancel_updates_existing_workflow_only(
    db_session: AsyncSession, client
) -> None:
    db_session.add(OrderWorkflow(external_order_id="10", status=OrderStatus.PENDING))
    db_session.add(OrderWorkflow(external_order_id="11", status=OrderStatus.SHIPPED))
    await db_session.commit()

    await _post(client, {"id": 10}, webhook_id="c1", topic="orders/cancelled")
    await _post(client, {"id": 11}, webhook_id="c2", topic="orders/cancelled")  # illegal transition
    await _post(client, {"id": 12}, webhook_id="c3", topic="orders/cancelled")  # unknown order

    states = {
        w.external_order_id: w.status
        for w in (await db_session.execute(select(OrderWorkflow))).scalars().all()
    }
    assert states == {"10": OrderStatus.CANCELLED, "11": OrderStatus.SHIPPED}


async def test_webhook_handler_failure_returns_5xx_and_allows_redelivery(
    db_session: AsyncSession, client
) -> None:
    attempts: list[int] = []

    async def flaky(session, topic: str, payload: dict) -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("transient")

    register_live_handler("orders/create", flaky)

    failed = await _post(client, {"id": 1}, webhook_id="retry-me")
    assert failed.status_code == 500  # non-2xx, so Shopify will redeliver
    retry = await _post(client, {"id": 1}, webhook_id="retry-me")

    assert retry.status_code == 200
    assert len(attempts) == 2  # the failed delivery was not remembered as handled


async def test_webhook_payload_content_is_never_stored(db_session: AsyncSession, client) -> None:
    secret_text = "customer-secret-address-line"
    await _post(
        client,
        {"id": 888, "shipping_address": {"address1": secret_text}, "note": secret_text},
        webhook_id="p1",
    )

    row = (await db_session.execute(select(OrderWorkflow))).scalar_one()
    dumped = json.dumps(
        {c.name: str(getattr(row, c.name)) for c in OrderWorkflow.__table__.columns}
    )
    assert secret_text not in dumped
    total = (await db_session.execute(select(func.count()).select_from(WebhookEvent))).scalar_one()
    assert total == 0
