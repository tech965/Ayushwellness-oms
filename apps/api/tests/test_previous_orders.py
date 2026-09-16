"""Requirement 4 (review meeting): the Telecaller order-detail page must
show the customer's previous orders -- product/variant, quantity, order
date, status -- scoped to that one customer, without exposing another
customer's or another telecaller's data, and without an N+1 query per
previous order.

`GET /telecaller/orders/{order_id}/previous-orders`
(`TelecallingService.get_previous_orders_for_assigned_order`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.db.session import get_db
from app.main import app
from app.models.order import OrderItem
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
        db_session, name="TELECALLER", permission_codes=["calls.manage"]
    )
    leader = await make_user(db_session, email="leader@prev-orders.example.com", is_superuser=True)
    telecaller = await make_user(
        db_session,
        email="tc@prev-orders.example.com",
        role=telecaller_role,
        team_leader_id=leader.id,
    )
    other_telecaller = await make_user(
        db_session,
        email="tc2@prev-orders.example.com",
        role=telecaller_role,
        team_leader_id=leader.id,
    )
    return leader, telecaller, other_telecaller


async def _assign(client, order_id: str, telecaller_id: str) -> None:
    response = await client.post(
        "/api/v1/team/orders/assign",
        json={"order_ids": [order_id], "mode": "manual", "telecaller_id": telecaller_id},
    )
    assert response.status_code == 201


async def _add_item(
    db_session: AsyncSession, *, order, sku: str, product_name: str, quantity: int
) -> None:
    item = OrderItem(
        order_id=order.id,
        sku=sku,
        product_name=product_name,
        quantity=quantity,
        unit_price=Decimal("199.00"),
        total_amount=Decimal("199.00") * quantity,
    )
    db_session.add(item)
    await db_session.commit()


async def test_no_previous_orders_returns_a_clean_empty_list(db_session: AsyncSession) -> None:
    leader, telecaller, _other = await _setup(db_session)
    customer = await make_customer(db_session)
    order = await make_order(db_session, order_number="PREV-A-CURRENT", customer=customer)

    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}/previous-orders")

    assert response.status_code == 200
    assert response.json()["data"] == []


async def test_previous_orders_returns_latest_first_with_items_and_excludes_current_order(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, _other = await _setup(db_session)
    customer = await make_customer(db_session)
    now = datetime.now(UTC)

    oldest = await make_order(
        db_session,
        order_number="PREV-B-OLDEST",
        customer=customer,
        order_datetime=now - timedelta(days=30),
    )
    await _add_item(
        db_session, order=oldest, sku="AW-HM-PN-60", product_name="Paan Masala 60", quantity=1
    )

    newest_previous = await make_order(
        db_session,
        order_number="PREV-B-NEWEST-PREVIOUS",
        customer=customer,
        order_datetime=now - timedelta(days=1),
    )
    await _add_item(
        db_session,
        order=newest_previous,
        sku="AW-HM-PN-120",
        product_name="Paan Masala 120",
        quantity=2,
    )

    current = await make_order(
        db_session, order_number="PREV-B-CURRENT", customer=customer, order_datetime=now
    )

    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(current.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.get(f"/api/v1/telecaller/orders/{current.id}/previous-orders")

    assert response.status_code == 200
    data = response.json()["data"]

    order_numbers = [row["order_number"] for row in data]
    assert order_numbers == ["PREV-B-NEWEST-PREVIOUS", "PREV-B-OLDEST"]  # latest first
    # The order being viewed is never included in its own history.
    assert current.order_number not in order_numbers

    newest_row = data[0]
    assert newest_row["items"] == [
        {"sku": "AW-HM-PN-120", "product_name": "Paan Masala 120", "quantity": 2}
    ]


async def test_previous_orders_denies_a_different_telecallers_order(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, other_telecaller = await _setup(db_session)
    customer = await make_customer(db_session)
    order = await make_order(db_session, order_number="PREV-C-CURRENT", customer=customer)

    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, other_telecaller.id) as other_client:
        response = await other_client.get(f"/api/v1/telecaller/orders/{order.id}/previous-orders")

    assert response.status_code == 403


async def test_previous_orders_empty_for_an_order_with_no_linked_customer(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, _other = await _setup(db_session)
    order = await make_order(db_session, order_number="PREV-D-CURRENT", customer=None)

    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}/previous-orders")

    assert response.status_code == 200
    assert response.json()["data"] == []
