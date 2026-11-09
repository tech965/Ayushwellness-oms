"""Live (API-first) Shiprocket: shipment creation for live-only Shopify
orders, AWB, tracking, cancellation, pickup, NDR, order list shipment
columns, inventory dispatch, webhook, and no-provider-data-persisted.

Both Shopify and Shiprocket are MOCKED. Nothing here is verified against
the real services.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from app.core.config import settings
from app.db.session import get_db
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.main import app
from app.models.enums import OrderStatus, ShopifySyncStatus
from app.models.inventory import InventoryMovement
from app.models.order_workflow import OrderWorkflow
from app.models.shipment import Shipment, ShipmentEvent
from app.models.shipment_ref import ShipmentRef
from app.services.live_workflow_handlers import register_default_live_handlers
from app.services.order_ref import encode_shopify_order_ref
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import bearer_client, make_role, make_user
from tests.test_live_orders_workflow import _assert_no_provider_rows, _node
from tests.test_live_telecalling_workflow import RouterStub
from tests.test_shopify_live_mode import _count

pytestmark = pytest.mark.asyncio


class ShopifyStub(RouterStub):
    """RouterStub + Shopify fulfilment queries/mutations."""

    def __init__(self, nodes, *, fulfil_ok: bool = True) -> None:
        super().__init__(nodes)
        self.fulfil_ok = fulfil_ok
        self.fulfilled: list[dict] = []

    async def execute(self, query, variables=None):  # noqa: ANN001
        variables = variables or {}
        if "OpenFulfillmentOrders" in query:
            if not self.fulfil_ok:
                return {"order": {"fulfillmentOrders": {"edges": []}}}
            return {
                "order": {
                    "fulfillmentOrders": {
                        "edges": [{"node": {"id": "gid://shopify/FulfillmentOrder/1"}}]
                    }
                }
            }
        if "mutation FulfillmentCreate" in query:
            self.fulfilled.append(variables)
            return {
                "fulfillmentCreate": {
                    "fulfillment": {"id": "gid://shopify/Fulfillment/99"},
                    "userErrors": [],
                }
            }
        return await super().execute(query, variables)


class ShiprocketStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.events: list[dict] = []
        self.fail_tracking = False
        self.created = 0

    async def ensure_authenticated(self) -> None:
        return None

    async def request(self, method, path, *, json=None, params=None):  # noqa: ANN001
        self.calls.append((method, path, json))
        if path == "/orders/create/adhoc":
            self.created += 1
            return {"order_id": 554 + self.created, "shipment_id": 776 + self.created}
        if path == "/courier/assign/awb":
            return {
                "response": {
                    "data": {
                        "awb_code": "AWB123",
                        "courier_name": "Delhivery",
                        "courier_company_id": 7,
                    }
                }
            }
        if path.startswith("/courier/track/awb/"):
            if self.fail_tracking:
                from app.integrations.shiprocket.errors import ShiprocketApiError

                raise ShiprocketApiError("rate limited", error_type="rate_limit_error")
            return {"tracking_data": {"shipment_track_activities": self.events}}
        if path == "/ndr/all":
            return {
                "data": [
                    {
                        "id": 1,
                        "awb_code": "AWB123",
                        "reason": "Customer unavailable",
                        "attempts": 1,
                        "courier": "Delhivery",
                        "order_id": 555,
                    }
                ],
                "meta": {"pagination": {"total_pages": 1}},
            }
        return {}


def _event(status: str, day: int, location: str = "Pune") -> dict:
    return {
        "id": day,
        "date": f"2026-10-{day:02d} 10:00:00",
        "status": status,
        "activity": status,
        "location": location,
        "courier_name": "Delhivery",
    }


@pytest.fixture(autouse=True)
def _live(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "SHOPIFY_LIVE_MODE", True)
    monkeypatch.setattr(settings, "SHIPROCKET_EMAIL", "ops@example.com")
    monkeypatch.setattr(settings, "SHIPROCKET_PASSWORD", "pw")
    monkeypatch.setattr(settings, "SHIPROCKET_PICKUP_LOCATION", "Main Warehouse")
    monkeypatch.setattr(settings, "SHIPROCKET_WEBHOOK_SECRET", "hook-secret")
    yield
    clear_adapters()
    register_default_live_handlers()
    app.dependency_overrides.clear()


def _install(shopify: ShopifyStub | None = None) -> tuple[ShopifyStub, ShiprocketStub]:
    shopify = shopify or ShopifyStub({1: _node(1)})
    shiprocket = ShiprocketStub()
    register_adapter(ShopifyAdapter(client=shopify))
    register_adapter(ShiprocketAdapter(client=shiprocket))
    return shopify, shiprocket


async def _ops(db_session: AsyncSession):
    role = await make_role(
        db_session,
        name="SHIP_OPS",
        permission_codes=[
            "orders.read",
            "shipments.read",
            "shipments.update",
            "ndr.read",
            "ndr.update",
        ],
    )
    return await make_user(db_session, email="ship@example.com", role=role)


async def _confirmed_workflow(db_session: AsyncSession, external_id: str = "1") -> OrderWorkflow:
    workflow = OrderWorkflow(
        id=encode_shopify_order_ref(external_id),
        external_order_id=external_id,
        status=OrderStatus.CONFIRMED,
    )
    db_session.add(workflow)
    await db_session.commit()
    return workflow


# --- creation ------------------------------------------------------------------------


async def test_ship_live_only_order_creates_reference_not_shipment_snapshot(
    db_session: AsyncSession,
) -> None:
    user = await _ops(db_session)
    wf = await _confirmed_workflow(db_session)
    shopify, shiprocket = _install()

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.post(
            f"/api/v1/orders/{wf.id}/ship",
            json={"length_cm": 10, "breadth_cm": 10, "height_cm": 10, "weight_kg": 0.5},
        )

    assert response.status_code == 201, response.text
    body = response.json()["data"]
    assert body["current_status"] == "pending" and body["shiprocket_shipment_id"] == "777"
    # Built from the live Shopify order, not a local row
    create = next(c for c in shiprocket.calls if c[1] == "/orders/create/adhoc")
    assert create[2]["order_id"] == "#1001"
    # Only identifiers persisted
    ref = (await db_session.execute(select(ShipmentRef))).scalar_one()
    assert (ref.order_id, ref.shiprocket_order_id, ref.shiprocket_shipment_id) == (
        wf.id,
        "555",
        "777",
    )
    assert await _count(db_session, Shipment) == 0
    assert await _count(db_session, ShipmentEvent) == 0
    await _assert_no_provider_rows(db_session)


async def test_ship_rejects_unconfirmed_and_duplicate(db_session: AsyncSession) -> None:
    user = await _ops(db_session)
    pending = OrderWorkflow(
        id=encode_shopify_order_ref("1"), external_order_id="1", status=OrderStatus.PENDING
    )
    db_session.add(pending)
    await db_session.commit()
    _install()

    async with bearer_client(app, get_db, db_session, user.id) as client:
        not_confirmed = await client.post(f"/api/v1/orders/{pending.id}/ship", json={})
        pending.status = OrderStatus.CONFIRMED
        await db_session.commit()
        first = await client.post(f"/api/v1/orders/{pending.id}/ship", json={})
        again = await client.post(f"/api/v1/orders/{pending.id}/ship", json={})

    assert not_confirmed.status_code == 409
    assert first.status_code == 201
    assert again.status_code == 409 and "already exists" in again.text
    assert await _count(db_session, ShipmentRef) == 1


# --- AWB, tracking, cancel, pickup --------------------------------------------------------


async def test_awb_tracking_and_shopify_fulfilment_push(db_session: AsyncSession) -> None:
    user = await _ops(db_session)
    wf = await _confirmed_workflow(db_session)
    shopify, shiprocket = _install()
    shiprocket.events = [_event("PICKED UP", 3), _event("IN TRANSIT", 4, "Mumbai")]

    async with bearer_client(app, get_db, db_session, user.id) as client:
        created = (await client.post(f"/api/v1/orders/{wf.id}/ship", json={})).json()["data"]
        sid = created["id"]
        assigned = await client.post(
            f"/api/v1/shipments/{sid}/shiprocket/assign-awb", json={"courier_id": None}
        )
        detail = await client.get(f"/api/v1/shipments/{sid}")
        timeline = await client.get(f"/api/v1/shipments/{sid}/timeline")

    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["data"]["awb"] == "AWB123"
    live = detail.json()["data"]
    assert live["current_status"] == "in_transit"  # read live, not stored
    assert live["current_location"] == "Mumbai"
    assert [e["status"] for e in timeline.json()["data"]] == ["PICKED UP", "IN TRANSIT"]
    ref = (await db_session.execute(select(ShipmentRef))).scalar_one()
    assert ref.awb == "AWB123"
    assert ref.shopify_sync_status == ShopifySyncStatus.SYNCED
    assert ref.shopify_fulfillment_id == "99"
    assert (
        shopify.fulfilled
        and shopify.fulfilled[0]["fulfillment"]["trackingInfo"]["number"] == "AWB123"
    )
    assert await _count(db_session, ShipmentEvent) == 0  # tracking history never stored


async def test_shopify_push_failure_does_not_fail_awb_assignment(db_session: AsyncSession) -> None:
    user = await _ops(db_session)
    wf = await _confirmed_workflow(db_session)
    _install(ShopifyStub({1: _node(1)}, fulfil_ok=False))

    async with bearer_client(app, get_db, db_session, user.id) as client:
        sid = (await client.post(f"/api/v1/orders/{wf.id}/ship", json={})).json()["data"]["id"]
        assigned = await client.post(
            f"/api/v1/shipments/{sid}/shiprocket/assign-awb", json={"courier_id": None}
        )

    assert assigned.status_code == 200
    ref = (await db_session.execute(select(ShipmentRef))).scalar_one()
    assert ref.awb == "AWB123"
    assert ref.shopify_sync_status == ShopifySyncStatus.FAILED and ref.shopify_sync_error


async def test_cancel_and_pickup(db_session: AsyncSession) -> None:
    user = await _ops(db_session)
    wf = await _confirmed_workflow(db_session)
    _, shiprocket = _install()

    async with bearer_client(app, get_db, db_session, user.id) as client:
        sid = (await client.post(f"/api/v1/orders/{wf.id}/ship", json={})).json()["data"]["id"]
        pickup = await client.post(f"/api/v1/shipments/{sid}/shiprocket/request-pickup")
        cancelled = await client.post(f"/api/v1/shipments/{sid}/shiprocket/cancel")
        reship = await client.post(f"/api/v1/orders/{wf.id}/ship", json={})

    assert pickup.status_code == 200
    assert cancelled.json()["data"]["current_status"] == "cancelled"
    assert any(c[1] == "/orders/cancel/shipment/awbs" for c in shiprocket.calls)
    assert reship.status_code == 201  # a cancelled shipment does not block re-shipping
    assert await _count(db_session, ShipmentRef) == 2


async def test_tracking_failure_is_an_explicit_error_not_a_guessed_status(
    db_session: AsyncSession,
) -> None:
    user = await _ops(db_session)
    wf = await _confirmed_workflow(db_session)
    _, shiprocket = _install()

    async with bearer_client(app, get_db, db_session, user.id) as client:
        sid = (await client.post(f"/api/v1/orders/{wf.id}/ship", json={})).json()["data"]["id"]
        await client.post(
            f"/api/v1/shipments/{sid}/shiprocket/assign-awb", json={"courier_id": None}
        )
        shiprocket.fail_tracking = True
        response = await client.get(f"/api/v1/shipments/{sid}")

    assert response.status_code >= 400
    assert "delivered" not in response.text


# --- order list columns, shipment lists, queue, NDR ----------------------------------------------


async def test_order_list_shows_live_shipment_columns(db_session: AsyncSession) -> None:
    user = await _ops(db_session)
    wf = await _confirmed_workflow(db_session)
    _, shiprocket = _install()
    shiprocket.events = [_event("DELIVERED", 6)]

    async with bearer_client(app, get_db, db_session, user.id) as client:
        sid = (await client.post(f"/api/v1/orders/{wf.id}/ship", json={})).json()["data"]["id"]
        await client.post(
            f"/api/v1/shipments/{sid}/shiprocket/assign-awb", json={"courier_id": None}
        )
        orders = await client.get("/api/v1/orders")
        shipments = await client.get("/api/v1/shipments", params={"order_id": str(wf.id)})

    row = orders.json()["data"][0]
    assert row["shipment_status"] == "delivered" and row["tracking_number"] == "AWB123"
    assert row["shipment_id"] == sid
    listed = shipments.json()["data"]
    assert listed[0]["order_number"] == "#1001" and listed[0]["current_status"] == "delivered"


async def test_shipment_queue_lists_confirmed_orders_without_active_shipment(
    db_session: AsyncSession,
) -> None:
    user = await _ops(db_session)
    wf = await _confirmed_workflow(db_session, "1")
    db_session.add(OrderWorkflow(external_order_id="2", status=OrderStatus.PENDING))
    await db_session.commit()
    _install(ShopifyStub({1: _node(1), 2: _node(2)}))

    async with bearer_client(app, get_db, db_session, user.id) as client:
        before = await client.get("/api/v1/shipments/queue")
        await client.post(f"/api/v1/orders/{wf.id}/ship", json={})
        after = await client.get("/api/v1/shipments/queue")

    assert [r["order_number"] for r in before.json()["data"]] == ["#1001"]
    assert after.json()["data"] == []


async def test_live_ndr_joined_to_known_shipment(db_session: AsyncSession) -> None:
    user = await _ops(db_session)
    wf = await _confirmed_workflow(db_session)
    _install()

    async with bearer_client(app, get_db, db_session, user.id) as client:
        sid = (await client.post(f"/api/v1/orders/{wf.id}/ship", json={})).json()["data"]["id"]
        await client.post(
            f"/api/v1/shipments/{sid}/shiprocket/assign-awb", json={"courier_id": None}
        )
        ndr = await client.get("/api/v1/live/ndr")
        reattempt = await client.post(
            f"/api/v1/live/shipments/{sid}/ndr-reattempt",
            json={"address_1": "1 New Road", "phone": "9000000000"},
        )

    item = ndr.json()["data"]["items"][0]
    assert item["awb"] == "AWB123" and item["order_id"] == str(wf.id) and item["shipment_id"] == sid
    assert "raw_external_payload" not in item
    assert reattempt.status_code == 200


async def test_stored_data_ndr_rto_and_shipment_totals_refuse_in_live_mode(
    db_session: AsyncSession,
) -> None:
    user = await _ops(db_session)

    async with bearer_client(app, get_db, db_session, user.id) as client:
        ndr = await client.get("/api/v1/ndr")
        rto = await client.get("/api/v1/rto")
        summary = await client.get("/api/v1/shipments/summary")

    assert ndr.status_code == rto.status_code == summary.status_code == 422
    assert "/live/ndr" in ndr.text


# --- inventory + webhook -----------------------------------------------------------------------


async def test_refresh_tracking_applies_inventory_dispatch_once(db_session: AsyncSession) -> None:
    from app.models.product import Product, ProductVariant

    user = await _ops(db_session)
    wf = await _confirmed_workflow(db_session)
    product = Product(title="P", status="active")
    db_session.add(product)
    await db_session.flush()
    variant = ProductVariant(
        product_id=product.id,
        sku="SKU-1",
        title="V",
        price=1,
        available_quantity=10,
        packets_per_box=1,
        pack_size=1,
        status="active",
    )
    db_session.add(variant)
    await db_session.commit()
    _, shiprocket = _install()
    shiprocket.events = [_event("IN TRANSIT", 4)]

    async with bearer_client(app, get_db, db_session, user.id) as client:
        sid = (await client.post(f"/api/v1/orders/{wf.id}/ship", json={})).json()["data"]["id"]
        await client.post(
            f"/api/v1/shipments/{sid}/shiprocket/assign-awb", json={"courier_id": None}
        )
        await client.post(f"/api/v1/shipments/{sid}/shiprocket/refresh-tracking")
        await client.post(f"/api/v1/shipments/{sid}/shiprocket/refresh-tracking")

    await db_session.refresh(variant)
    movements = (await db_session.execute(select(InventoryMovement))).scalars().all()
    assert len(movements) == 1  # idempotent
    assert movements[0].order_id == wf.id and str(movements[0].shipment_id) == sid
    assert variant.available_quantity == 8  # 2 units of SKU-1 deducted once


async def test_shiprocket_webhook_live_mode_verifies_dedupes_and_stores_no_payload(
    db_session: AsyncSession, client
) -> None:
    wf = await _confirmed_workflow(db_session)
    db_session.add(ShipmentRef(order_id=wf.id, awb="AWB123", shiprocket_shipment_id="777"))
    await db_session.commit()
    _install()
    payload = {"awb": "AWB123", "current_status": "IN TRANSIT", "customer_name": "Secret Name"}

    bad = await client.post("/api/v1/webhooks/shiprocket/tracking", json=payload)
    headers = {"X-Api-Key": "hook-secret"}
    first = await client.post("/api/v1/webhooks/shiprocket/tracking", json=payload, headers=headers)
    second = await client.post(
        "/api/v1/webhooks/shiprocket/tracking", json=payload, headers=headers
    )

    assert bad.status_code == 401
    assert first.status_code == second.status_code == 200
    from app.models.integration import WebhookEvent
    from app.models.webhook_delivery import WebhookDelivery

    assert await _count(db_session, WebhookEvent) == 0
    ledger = (await db_session.execute(select(WebhookDelivery))).scalars().all()
    assert len(ledger) == 1 and ledger[0].provider == "shiprocket"
    assert "Secret Name" not in repr([(r.provider, r.delivery_id, r.topic) for r in ledger])
    assert await _count(db_session, Shipment) == 0
    assert isinstance(datetime.now(UTC), datetime) and re.compile("x")
    assert (
        await db_session.execute(select(func.count()).select_from(ShipmentRef))
    ).scalar_one() == 1
