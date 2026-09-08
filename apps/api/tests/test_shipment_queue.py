"""Shipment Queue — confirmed orders awaiting shipment processing. A
distinct, narrower view from the existing generic `GET /shipments` list
(already-processed shipments); see
`OrderRepository.shipment_queue_query`'s docstring for the exact
membership rule.
"""

from __future__ import annotations

import pytest
from app.db.session import get_db
from app.main import app
from app.models.enums import OrderStatus, ShipmentStatus
from app.models.shipment import Shipment
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import (
    bearer_client,
    make_customer,
    make_order,
    make_role,
    make_user,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clear_get_db_override():
    yield
    app.dependency_overrides.clear()


async def _make_ops_user(db_session: AsyncSession):
    role = await make_role(
        db_session, name="OPERATIONS", permission_codes=["shipments.read", "shipments.update"]
    )
    return await make_user(db_session, email="ops@queue.example.com", role=role)


async def test_confirmed_order_with_no_shipment_appears_in_queue(
    db_session: AsyncSession,
) -> None:
    ops = await _make_ops_user(db_session)
    customer = await make_customer(db_session)
    order = await make_order(
        db_session, order_number="QUEUE-001", customer=customer, status=OrderStatus.CONFIRMED
    )

    async with bearer_client(app, get_db, db_session, ops.id) as client:
        response = await client.get("/api/v1/shipments/queue")
        assert response.status_code == 200
        order_ids = {row["order_id"] for row in response.json()["data"]}
        assert str(order.id) in order_ids


async def test_pending_order_does_not_appear_in_queue(db_session: AsyncSession) -> None:
    ops = await _make_ops_user(db_session)
    customer = await make_customer(db_session)
    order = await make_order(
        db_session, order_number="QUEUE-002", customer=customer, status=OrderStatus.PENDING
    )

    async with bearer_client(app, get_db, db_session, ops.id) as client:
        response = await client.get("/api/v1/shipments/queue")
        order_ids = {row["order_id"] for row in response.json()["data"]}
        assert str(order.id) not in order_ids


async def test_confirmed_order_already_fulfilled_via_shopify_does_not_appear_in_queue(
    db_session: AsyncSession,
) -> None:
    """Regression test: production showed 52,317 orders "awaiting
    shipment" because a CONFIRMED order that was fulfilled directly
    through Shopify (never touching this OMS's own Shipment table at
    all) was incorrectly counted as still queued. `fulfillment_status`
    is the real signal for "was this actually shipped".
    """
    from app.models.enums import FulfillmentStatus

    ops = await _make_ops_user(db_session)
    customer = await make_customer(db_session)
    order = await make_order(
        db_session,
        order_number="QUEUE-FULFILLED-1",
        customer=customer,
        status=OrderStatus.CONFIRMED,
        fulfillment_status=FulfillmentStatus.FULFILLED,
    )

    async with bearer_client(app, get_db, db_session, ops.id) as client:
        response = await client.get("/api/v1/shipments/queue")
        order_ids = {row["order_id"] for row in response.json()["data"]}
        assert str(order.id) not in order_ids


async def test_order_with_shipment_still_pending_stays_in_queue(
    db_session: AsyncSession,
) -> None:
    ops = await _make_ops_user(db_session)
    customer = await make_customer(db_session)
    order = await make_order(
        db_session, order_number="QUEUE-003", customer=customer, status=OrderStatus.CONFIRMED
    )
    db_session.add(
        Shipment(order_id=order.id, current_status=ShipmentStatus.PENDING, source_system="manual")
    )
    await db_session.commit()

    async with bearer_client(app, get_db, db_session, ops.id) as client:
        response = await client.get("/api/v1/shipments/queue")
        rows_by_order = {row["order_id"]: row for row in response.json()["data"]}
        assert str(order.id) in rows_by_order
        assert rows_by_order[str(order.id)]["shipment_status"] == "pending"


async def test_order_with_shipment_picked_up_leaves_queue(db_session: AsyncSession) -> None:
    ops = await _make_ops_user(db_session)
    customer = await make_customer(db_session)
    order = await make_order(
        db_session, order_number="QUEUE-004", customer=customer, status=OrderStatus.CONFIRMED
    )
    db_session.add(
        Shipment(
            order_id=order.id, current_status=ShipmentStatus.PICKED_UP, source_system="manual"
        )
    )
    await db_session.commit()

    async with bearer_client(app, get_db, db_session, ops.id) as client:
        response = await client.get("/api/v1/shipments/queue")
        order_ids = {row["order_id"] for row in response.json()["data"]}
        assert str(order.id) not in order_ids


async def test_shipment_queue_empty_state(db_session: AsyncSession) -> None:
    ops = await _make_ops_user(db_session)
    async with bearer_client(app, get_db, db_session, ops.id) as client:
        response = await client.get("/api/v1/shipments/queue")
        assert response.status_code == 200
        assert response.json()["data"] == []
        assert response.json()["meta"]["total_items"] == 0


async def test_shipment_queue_requires_shipments_read_permission(
    db_session: AsyncSession,
) -> None:
    role = await make_role(db_session, name="NO_SHIPMENTS_ROLE", permission_codes=["orders.read"])
    user = await make_user(db_session, email="noperm@queue.example.com", role=role)
    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/shipments/queue")
        assert response.status_code == 403


async def test_shipment_summary_returns_real_counts(db_session: AsyncSession) -> None:
    ops = await _make_ops_user(db_session)
    customer = await make_customer(db_session)
    await make_order(
        db_session, order_number="QUEUE-SUM-1", customer=customer, status=OrderStatus.CONFIRMED
    )

    async with bearer_client(app, get_db, db_session, ops.id) as client:
        response = await client.get("/api/v1/shipments/summary")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["confirmed_awaiting_shipment"] >= 1
        assert data["total_shipments"] == 0


async def test_confirmation_to_shipment_rate_excludes_shopify_fulfilled_orders(
    db_session: AsyncSession,
) -> None:
    """Regression test: without excluding already-Shopify-fulfilled
    orders from the denominator, this rate compared an all-time
    historical count against a tiny OMS-only numerator and came out as
    ~1% in production -- meaningless. A CONFIRMED-and-already-fulfilled
    order must not dilute "ever confirmed" here: with one shipped order
    and one already-fulfilled (excluded) order, the rate must be 100%,
    not 50%.
    """
    from app.models.enums import FulfillmentStatus, ShipmentStatus
    from app.models.shipment import Shipment

    ops = await _make_ops_user(db_session)
    customer = await make_customer(db_session)
    await make_order(
        db_session,
        order_number="RATE-FULFILLED-1",
        customer=customer,
        status=OrderStatus.CONFIRMED,
        fulfillment_status=FulfillmentStatus.FULFILLED,
    )
    shipped_order = await make_order(
        db_session,
        order_number="RATE-SHIPPED-1",
        customer=customer,
        status=OrderStatus.CONFIRMED,
        fulfillment_status=FulfillmentStatus.UNFULFILLED,
    )
    db_session.add(
        Shipment(
            order_id=shipped_order.id,
            current_status=ShipmentStatus.IN_TRANSIT,
            source_system="manual",
        )
    )
    await db_session.commit()

    async with bearer_client(app, get_db, db_session, ops.id) as client:
        response = await client.get("/api/v1/shipments/analytics")
        assert response.status_code == 200
        assert response.json()["data"]["confirmation_to_shipment_rate"] == 100.0


async def test_shipment_analytics_empty_state_does_not_crash(db_session: AsyncSession) -> None:
    ops = await _make_ops_user(db_session)
    async with bearer_client(app, get_db, db_session, ops.id) as client:
        response = await client.get("/api/v1/shipments/analytics")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["confirmation_to_shipment_rate"] == 0.0
        assert data["daily_trend"] == []
        assert data["telecaller_stats"] == []


async def test_shipment_queue_filters_by_payment_type(db_session: AsyncSession) -> None:
    from app.models.enums import PaymentType

    ops = await _make_ops_user(db_session)
    customer = await make_customer(db_session)
    cod_order = await make_order(
        db_session,
        order_number="QUEUE-COD-1",
        customer=customer,
        status=OrderStatus.CONFIRMED,
        payment_type=PaymentType.COD,
    )
    prepaid_order = await make_order(
        db_session,
        order_number="QUEUE-PREPAID-1",
        customer=customer,
        status=OrderStatus.CONFIRMED,
        payment_type=PaymentType.PREPAID,
    )

    async with bearer_client(app, get_db, db_session, ops.id) as client:
        response = await client.get("/api/v1/shipments/queue", params={"payment_type": "cod"})
        order_ids = {row["order_id"] for row in response.json()["data"]}
        assert str(cod_order.id) in order_ids
        assert str(prepaid_order.id) not in order_ids
