"""FULFILLMENT role: a dedicated shipment-processing role, deliberately
narrower than OPERATIONS. These tests pin down the two-sided boundary the
spec requires:

  - A FULFILLMENT user CAN read orders + the confirmed-order queue and
    create/update shipments (which runs the internal InventoryService
    stock pre-check).
  - A FULFILLMENT user CANNOT log a telecaller call or confirm an order.
  - A TELECALLER user CANNOT update a shipment.

Every endpoint under test already existed before this role; nothing here
adds new API surface — it only verifies the new role's permission grid.
"""

from __future__ import annotations

import pytest
from app.db.session import get_db
from app.main import app
from app.models.enums import OrderStatus, ShipmentStatus
from app.models.shipment import Shipment
from scripts.seed import ROLE_PERMISSIONS
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import (
    bearer_client,
    make_customer,
    make_order,
    make_role,
    make_user,
)

pytestmark = pytest.mark.asyncio

FULFILLMENT_PERMISSIONS = [
    "orders.read",
    "shipments.read",
    "shipments.update",
    "inventory.read",
    "ndr.read",
    "ndr.update",
    "rto.read",
    "rto.update",
    "couriers.read",
]


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


async def _fulfillment_user(db_session: AsyncSession):
    role = await make_role(
        db_session, name="FULFILLMENT", permission_codes=FULFILLMENT_PERMISSIONS
    )
    return await make_user(db_session, email="fulfil@example.com", role=role)


async def _telecaller_user(db_session: AsyncSession):
    role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage", "orders.confirm"]
    )
    return await make_user(db_session, email="tc@example.com", role=role)


async def test_seed_fulfillment_role_permission_grid() -> None:
    """The seeded FULFILLMENT role is exactly the intended set — and
    critically never grants call-history or order-confirmation access.
    """
    granted = set(ROLE_PERMISSIONS["FULFILLMENT"])
    assert granted == set(FULFILLMENT_PERMISSIONS)
    for forbidden in ("calls.manage", "orders.confirm", "orders.cancel", "orders.update"):
        assert forbidden not in granted


async def test_fulfillment_can_read_confirmed_order_queue(db_session: AsyncSession) -> None:
    user = await _fulfillment_user(db_session)
    customer = await make_customer(db_session)
    order = await make_order(
        db_session, order_number="FUL-Q-1", customer=customer, status=OrderStatus.CONFIRMED
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/shipments/queue")
        assert response.status_code == 200
        assert str(order.id) in {row["order_id"] for row in response.json()["data"]}


async def test_fulfillment_can_read_summary_and_analytics(db_session: AsyncSession) -> None:
    user = await _fulfillment_user(db_session)
    async with bearer_client(app, get_db, db_session, user.id) as client:
        assert (await client.get("/api/v1/shipments/summary")).status_code == 200
        assert (await client.get("/api/v1/shipments/analytics")).status_code == 200


async def test_fulfillment_can_read_orders(db_session: AsyncSession) -> None:
    user = await _fulfillment_user(db_session)
    async with bearer_client(app, get_db, db_session, user.id) as client:
        assert (await client.get("/api/v1/orders")).status_code == 200


async def test_fulfillment_can_update_shipment(db_session: AsyncSession) -> None:
    user = await _fulfillment_user(db_session)
    customer = await make_customer(db_session)
    order = await make_order(
        db_session, order_number="FUL-SH-1", customer=customer, status=OrderStatus.CONFIRMED
    )
    shipment = Shipment(
        order_id=order.id, current_status=ShipmentStatus.PENDING, source_system="manual"
    )
    db_session.add(shipment)
    await db_session.commit()

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.patch(
            f"/api/v1/shipments/{shipment.id}", json={"current_status": "picked_up"}
        )
        assert response.status_code == 200
        assert response.json()["data"]["current_status"] == "picked_up"


async def test_fulfillment_cannot_confirm_order(db_session: AsyncSession) -> None:
    user = await _fulfillment_user(db_session)
    customer = await make_customer(db_session)
    order = await make_order(
        db_session, order_number="FUL-NC-1", customer=customer, status=OrderStatus.PENDING
    )
    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")
        assert response.status_code == 403


async def test_fulfillment_cannot_log_a_call(db_session: AsyncSession) -> None:
    user = await _fulfillment_user(db_session)
    customer = await make_customer(db_session)
    order = await make_order(
        db_session, order_number="FUL-NL-1", customer=customer, status=OrderStatus.PENDING
    )
    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls", json={"outcome": "connected"}
        )
        assert response.status_code == 403


async def test_telecaller_cannot_update_shipment(db_session: AsyncSession) -> None:
    user = await _telecaller_user(db_session)
    customer = await make_customer(db_session)
    order = await make_order(
        db_session, order_number="TC-SH-1", customer=customer, status=OrderStatus.CONFIRMED
    )
    shipment = Shipment(
        order_id=order.id, current_status=ShipmentStatus.PENDING, source_system="manual"
    )
    db_session.add(shipment)
    await db_session.commit()

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.patch(
            f"/api/v1/shipments/{shipment.id}", json={"current_status": "picked_up"}
        )
        assert response.status_code == 403
