"""`GET /orders?confirmed_only=true` -- backs the "Confirmed by Telecaller"
page: a history/audit view of every order `confirmed_by_telecaller_id IS
NOT NULL`, independent of current order/fulfillment/shipment status.
Deliberately distinct from the shipment queue's "needs shipment now"
business rule (`tests/test_shipment_queue.py`) -- these tests specifically
assert an order STAYS visible here after it stops needing shipment, and
that an order which reached CONFIRMED WITHOUT a Telecaller (the
auto-confirmed-on-payment path -- see `OrderService.upsert_synced_order`/
`CashfreePaymentService._apply_order_state`) never appears.
"""

from __future__ import annotations

import pytest
from app.db.session import get_db
from app.main import app
from app.models.enums import FulfillmentStatus, OrderStatus
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


async def _setup(db_session: AsyncSession):
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage", "orders.confirm"]
    )
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    fulfillment_role = await make_role(
        db_session, name="FULFILLMENT", permission_codes=["orders.read", "shipments.read"]
    )
    leader = await make_user(db_session, email="leader@ctc.example.com", role=team_leader_role)
    telecaller = await make_user(
        db_session,
        email="tc@ctc.example.com",
        name="Rahul Sharma",
        role=telecaller_role,
        team_leader_id=leader.id,
    )
    fulfillment_user = await make_user(
        db_session, email="fulfillment@ctc.example.com", role=fulfillment_role
    )
    customer = await make_customer(db_session)
    return leader, telecaller, fulfillment_user, customer


async def _assign_and_confirm(leader_client, tc_client, order_id: str, telecaller_id: str) -> None:
    response = await leader_client.post(
        "/api/v1/team/orders/assign",
        json={"order_ids": [order_id], "mode": "manual", "telecaller_id": telecaller_id},
    )
    assert response.status_code == 201
    response = await tc_client.post(f"/api/v1/telecaller/orders/{order_id}/confirm")
    assert response.status_code == 200


async def test_unconfirmed_order_does_not_appear(db_session: AsyncSession) -> None:
    _leader, _tc, fulfillment_user, customer = await _setup(db_session)
    await make_order(
        db_session, order_number="CTC-PENDING", customer=customer, status=OrderStatus.PENDING
    )

    async with bearer_client(app, get_db, db_session, fulfillment_user.id) as client:
        response = await client.get("/api/v1/orders", params={"confirmed_only": "true"})
        assert response.status_code == 200
        order_numbers = {row["order_number"] for row in response.json()["data"]}
        assert "CTC-PENDING" not in order_numbers


async def test_telecaller_confirmed_order_appears_immediately_with_name_and_time(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, fulfillment_user, customer = await _setup(db_session)
    order = await make_order(
        db_session, order_number="CTC-CONFIRMED", customer=customer, status=OrderStatus.PENDING
    )

    async with (
        bearer_client(app, get_db, db_session, leader.id) as leader_client,
        bearer_client(app, get_db, db_session, telecaller.id) as tc_client,
    ):
        await _assign_and_confirm(leader_client, tc_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, fulfillment_user.id) as client:
        response = await client.get("/api/v1/orders", params={"confirmed_only": "true"})
        assert response.status_code == 200
        rows = {row["order_number"]: row for row in response.json()["data"]}
        assert "CTC-CONFIRMED" in rows
        row = rows["CTC-CONFIRMED"]
        assert row["confirmed_by_telecaller_name"] == "Rahul Sharma"
        assert row["confirmed_at"] is not None
        assert row["confirmed_by_telecaller_id"] == str(telecaller.id)


async def test_auto_confirmed_order_without_a_telecaller_never_appears(
    db_session: AsyncSession,
) -> None:
    """Root cause of the "Confirmed By: -" rows on Orders Need Shipment:
    `Order.status` can reach CONFIRMED without any Telecaller involvement
    (a prepaid order already paid at Shopify checkout, or paid live via
    Cashfree -- see the module docstring). Such an order is correctly
    absent from this page: `confirmed_by_telecaller_id IS NOT NULL` is
    the one and only membership rule, never `status == CONFIRMED` alone.
    """
    _leader, _tc, fulfillment_user, customer = await _setup(db_session)
    await make_order(
        db_session,
        order_number="CTC-AUTO-CONFIRMED",
        customer=customer,
        status=OrderStatus.CONFIRMED,  # reached CONFIRMED, but never via a Telecaller
    )

    async with bearer_client(app, get_db, db_session, fulfillment_user.id) as client:
        response = await client.get("/api/v1/orders", params={"confirmed_only": "true"})
        assert response.status_code == 200
        order_numbers = {row["order_number"] for row in response.json()["data"]}
        assert "CTC-AUTO-CONFIRMED" not in order_numbers

        # And the inverse -- it DOES show up in the general, unfiltered list.
        response = await client.get("/api/v1/orders")
        order_numbers = {row["order_number"] for row in response.json()["data"]}
        assert "CTC-AUTO-CONFIRMED" in order_numbers


async def test_confirmed_order_stays_visible_after_it_no_longer_needs_shipment(
    db_session: AsyncSession,
) -> None:
    """Distinct from the shipment queue's business rule
    (`OrderRepository.shipment_queue_query`): once fulfilled, an order
    drops out of "Orders Need Shipment" but must stay in "Confirmed by
    Telecaller" -- this is a history view, not a to-do queue.
    """
    leader, telecaller, fulfillment_user, customer = await _setup(db_session)
    order = await make_order(
        db_session, order_number="CTC-FULFILLED", customer=customer, status=OrderStatus.PENDING
    )

    async with (
        bearer_client(app, get_db, db_session, leader.id) as leader_client,
        bearer_client(app, get_db, db_session, telecaller.id) as tc_client,
    ):
        await _assign_and_confirm(leader_client, tc_client, str(order.id), str(telecaller.id))

    order.fulfillment_status = FulfillmentStatus.FULFILLED
    await db_session.commit()

    async with bearer_client(app, get_db, db_session, fulfillment_user.id) as client:
        # Gone from the shipment queue (needs-shipment business rule).
        queue_response = await client.get("/api/v1/shipments/queue")
        assert queue_response.status_code == 200
        queue_order_ids = {row["order_id"] for row in queue_response.json()["data"]}
        assert str(order.id) not in queue_order_ids

        # Still present in the Telecaller-confirmed history view.
        confirmed_response = await client.get(
            "/api/v1/orders", params={"confirmed_only": "true"}
        )
        assert confirmed_response.status_code == 200
        confirmed_order_numbers = {
            row["order_number"] for row in confirmed_response.json()["data"]
        }
        assert "CTC-FULFILLED" in confirmed_order_numbers


async def test_telecaller_id_filter_narrows_results(db_session: AsyncSession) -> None:
    leader, telecaller, fulfillment_user, customer = await _setup(db_session)
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage", "orders.confirm"]
    )
    other_telecaller = await make_user(
        db_session,
        email="tc2@ctc.example.com",
        name="Priya Nair",
        role=telecaller_role,
        team_leader_id=leader.id,
    )
    order_a = await make_order(
        db_session, order_number="CTC-A", customer=customer, status=OrderStatus.PENDING
    )
    order_b = await make_order(
        db_session, order_number="CTC-B", customer=customer, status=OrderStatus.PENDING
    )

    async with (
        bearer_client(app, get_db, db_session, leader.id) as leader_client,
        bearer_client(app, get_db, db_session, telecaller.id) as tc_client,
        bearer_client(app, get_db, db_session, other_telecaller.id) as tc2_client,
    ):
        await _assign_and_confirm(leader_client, tc_client, str(order_a.id), str(telecaller.id))
        await _assign_and_confirm(
            leader_client, tc2_client, str(order_b.id), str(other_telecaller.id)
        )

    async with bearer_client(app, get_db, db_session, fulfillment_user.id) as client:
        response = await client.get(
            "/api/v1/orders",
            params={"confirmed_only": "true", "telecaller_id": str(telecaller.id)},
        )
        assert response.status_code == 200
        order_numbers = {row["order_number"] for row in response.json()["data"]}
        assert order_numbers == {"CTC-A"}
