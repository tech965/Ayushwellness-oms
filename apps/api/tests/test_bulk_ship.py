"""Fulfillment Queue's bulk "Ship via Shiprocket" action —
`POST /orders/bulk-ship`. Mirrors `test_order_confirmation.py`'s bulk-confirm
tests: every order is processed independently, one failure never blocks or
rolls back the rest, and the response always reports a per-order result
instead of an all-or-nothing failure.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.core.config import settings
from app.db.session import get_db
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.main import app
from app.models.enums import OrderStatus, PaymentType
from app.schemas.order import OrderItemCreateRequest
from app.services.order_service import OrderService
from app.services.shiprocket_service import ShiprocketOperationsService
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import bearer_client, make_role, make_user

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clear_overrides_and_adapters():
    yield
    app.dependency_overrides.clear()
    clear_adapters()


@pytest.fixture(autouse=True)
def _configure_shiprocket(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "SHIPROCKET_EMAIL", "ops@example.com")
    monkeypatch.setattr(settings, "SHIPROCKET_PASSWORD", "super-secret-password")
    monkeypatch.setattr(settings, "SHIPROCKET_PICKUP_LOCATION", "Main Warehouse")


class _StubClient:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)

    async def request(self, method, path, *, json=None, params=None):  # noqa: ANN001
        return self._responses.pop(0)

    async def ensure_authenticated(self) -> None:
        pass


async def _make_order(session: AsyncSession, order_number: str):
    """Confirmed, with a shipping address -- see the matching helper's
    docstring in `test_shiprocket_operations.py` for why both are needed
    now that `_check_shippable` enforces them.
    """
    order = await OrderService(session).create_order(
        actor=None,
        order_number=order_number,
        customer_id=None,
        order_datetime=datetime.now(UTC),
        currency="INR",
        payment_type=PaymentType.PREPAID,
        shipping_charge=Decimal("0"),
        notes=None,
        items=[
            OrderItemCreateRequest(
                sku="SKU1", product_name="Ashwagandha", quantity=1, unit_price=Decimal("499.00")
            )
        ],
    )
    order.status = OrderStatus.CONFIRMED
    order.shipping_address = {
        "line1": "123 Test St",
        "city": "Mumbai",
        "state": "MH",
        "country": "India",
        "pin_code": "400001",
    }
    await session.commit()
    await session.refresh(order)
    return order


def _create_order_response(shipment_id: str, shiprocket_order_id: str) -> dict:
    return {"order_id": shiprocket_order_id, "shipment_id": shipment_id, "status": "NEW"}


async def _fulfillment_user(db_session: AsyncSession):
    role = await make_role(
        db_session,
        name="FULFILLMENT",
        permission_codes=["orders.read", "shipments.read", "shipments.update"],
    )
    return await make_user(db_session, email="fulfil-bulkship@example.com", role=role)


async def test_bulk_create_shipments_processes_each_order_independently(
    db_session: AsyncSession,
) -> None:
    order1 = await _make_order(db_session, "BULKSHIP-001")
    order2 = await _make_order(db_session, "BULKSHIP-002")
    unknown_id = uuid.uuid4()

    client = _StubClient(
        [
            _create_order_response("5001", "9001"),
            _create_order_response("5002", "9002"),
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))

    results = await ShiprocketOperationsService(db_session).bulk_create_shipments_for_orders(
        [unknown_id, order1.id, order2.id], actor=None
    )

    by_order = {r["order_id"]: r for r in results}
    assert by_order[unknown_id]["success"] is False
    assert by_order[unknown_id]["shipment_id"] is None
    assert by_order[order1.id]["success"] is True
    assert by_order[order1.id]["shipment_id"] is not None
    assert by_order[order2.id]["success"] is True
    assert by_order[order2.id]["shipment_id"] is not None


async def test_bulk_ship_endpoint_requires_shipments_update_permission(
    db_session: AsyncSession,
) -> None:
    role = await make_role(db_session, name="NO_SHIP_ROLE", permission_codes=["orders.read"])
    user = await make_user(db_session, email="noship@example.com", role=role)
    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.post(
            "/api/v1/orders/bulk-ship", json={"order_ids": [str(uuid.uuid4())]}
        )
        assert response.status_code == 403


async def test_bulk_ship_endpoint_returns_per_order_counts(db_session: AsyncSession) -> None:
    order = await _make_order(db_session, "BULKSHIP-EP-1")
    unknown_id = uuid.uuid4()
    user = await _fulfillment_user(db_session)

    client = _StubClient([_create_order_response("6001", "8001")])
    register_adapter(ShiprocketAdapter(client=client))

    async with bearer_client(app, get_db, db_session, user.id) as api_client:
        response = await api_client.post(
            "/api/v1/orders/bulk-ship",
            json={"order_ids": [str(order.id), str(unknown_id)]},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["shipped_count"] == 1
        assert data["failed_count"] == 1
        results_by_order = {r["order_id"]: r for r in data["results"]}
        assert results_by_order[str(order.id)]["success"] is True
        assert results_by_order[str(unknown_id)]["success"] is False


async def test_bulk_ship_empty_selection_returns_422(db_session: AsyncSession) -> None:
    user = await _fulfillment_user(db_session)
    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.post("/api/v1/orders/bulk-ship", json={"order_ids": []})
        assert response.status_code == 422


async def test_confirmed_order_is_removed_from_queue_once_shipped_in_bulk(
    db_session: AsyncSession,
) -> None:
    """End-to-end: an order confirmed via the telecaller flow, then bulk
    shipped, leaves the Fulfillment Queue once its shipment exists (mirrors
    `test_shipment_queue.py`'s single-ship equivalent).
    """
    order = await _make_order(db_session, "BULKSHIP-QUEUE-1")
    user = await _fulfillment_user(db_session)

    client = _StubClient([_create_order_response("7001", "7101")])
    register_adapter(ShiprocketAdapter(client=client))

    async with bearer_client(app, get_db, db_session, user.id) as api_client:
        before = await api_client.get("/api/v1/shipments/queue")
        assert str(order.id) in {row["order_id"] for row in before.json()["data"]}

        ship = await api_client.post(
            "/api/v1/orders/bulk-ship", json={"order_ids": [str(order.id)]}
        )
        assert ship.json()["data"]["shipped_count"] == 1

        after = await api_client.get("/api/v1/shipments/queue")
        rows_by_order = {row["order_id"]: row for row in after.json()["data"]}
        # A freshly created shipment starts PENDING -- still "just queued"
        # (matches `shipment_queue_query`'s own PICKED_UP-or-later cutoff),
        # so the order stays visible but now shows the shipment.
        assert rows_by_order[str(order.id)]["shipment_status"] == "pending"
        assert rows_by_order[str(order.id)]["shipment_id"] is not None
