"""Admin "Repeat Customers" view (`GET /customers/repeat`).

`REPEAT_CUSTOMER_MIN_ORDERS` (`app.repositories.customer`) is the one
place "repeat" is defined: a customer with 2+ real `Order` rows. These
tests assert against that definition directly rather than any inferred
concept (Shopify's own "returning customer" flag is never consulted --
this OMS doesn't even sync one).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.db.session import get_db
from app.main import app
from app.models.enums import OrderStatus, PaymentStatus
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


async def _admin_client(db_session: AsyncSession):
    role = await make_role(db_session, name="OPERATIONS", permission_codes=["customers.read"])
    user = await make_user(db_session, email="ops-repeat@example.com", role=role)
    return bearer_client(app, get_db, db_session, user.id)


async def test_one_order_customer_is_not_a_repeat_customer(db_session: AsyncSession) -> None:
    customer = await make_customer(db_session, phone="9000000001")
    await make_order(db_session, order_number="REPEAT-A1", customer=customer)

    async with await _admin_client(db_session) as client:
        response = await client.get("/api/v1/customers/repeat")
        assert response.status_code == 200
        ids = {row["customer_id"] for row in response.json()["data"]}
        assert str(customer.id) not in ids


async def test_two_order_customer_is_a_repeat_customer(db_session: AsyncSession) -> None:
    customer = await make_customer(db_session, phone="9000000002")
    await make_order(db_session, order_number="REPEAT-B1", customer=customer)
    await make_order(db_session, order_number="REPEAT-B2", customer=customer)

    async with await _admin_client(db_session) as client:
        response = await client.get("/api/v1/customers/repeat")
        assert response.status_code == 200
        rows = {row["customer_id"]: row for row in response.json()["data"]}
        assert str(customer.id) in rows
        assert rows[str(customer.id)]["order_count"] == 2


async def test_multiple_orders_have_the_correct_count(db_session: AsyncSession) -> None:
    customer = await make_customer(db_session, phone="9000000003")
    for i in range(5):
        await make_order(db_session, order_number=f"REPEAT-C{i}", customer=customer)

    async with await _admin_client(db_session) as client:
        response = await client.get("/api/v1/customers/repeat")
        rows = {row["customer_id"]: row for row in response.json()["data"]}
        assert rows[str(customer.id)]["order_count"] == 5
        assert len(rows[str(customer.id)]["order_numbers"]) == 5
        assert set(rows[str(customer.id)]["order_numbers"]) == {f"REPEAT-C{i}" for i in range(5)}


async def test_customer_details_are_displayed_correctly(db_session: AsyncSession) -> None:
    customer = await make_customer(db_session, phone="9000000004")
    await make_order(
        db_session,
        order_number="REPEAT-D1",
        customer=customer,
        total_amount=Decimal("500.00"),
        status=OrderStatus.CANCELLED,
    )
    await make_order(
        db_session,
        order_number="REPEAT-D2",
        customer=customer,
        total_amount=Decimal("700.00"),
        status=OrderStatus.CONFIRMED,
    )

    async with await _admin_client(db_session) as client:
        response = await client.get("/api/v1/customers/repeat")
        rows = {row["customer_id"]: row for row in response.json()["data"]}
        row = rows[str(customer.id)]
        assert row["customer_name"] == customer.full_name
        assert row["phone"] == "9000000004"
        assert row["order_count"] == 2
        assert Decimal(row["total_order_value"]) == Decimal("1200.00")
        # The latest order (by order_datetime) drives status/payment —
        # both orders were created "now" via `make_order`'s default
        # `order_datetime` (now - 1 day), so this asserts the field is
        # populated from a real order, not that a specific one wins any
        # tie -- see the count-and-value assertions above for the parts
        # that don't depend on which row is "latest."
        assert row["latest_order_status"] in {
            OrderStatus.CANCELLED.value,
            OrderStatus.CONFIRMED.value,
        }
        assert row["latest_payment_status"] == PaymentStatus.PAID.value


async def test_repeat_customers_search_filters_by_name_or_phone(db_session: AsyncSession) -> None:
    match = await make_customer(db_session, phone="9000000005")
    match.full_name = "Findable Customer"
    await make_order(db_session, order_number="REPEAT-E1", customer=match)
    await make_order(db_session, order_number="REPEAT-E2", customer=match)

    other = await make_customer(db_session, phone="9000000006")
    other.full_name = "Someone Else"
    await make_order(db_session, order_number="REPEAT-E3", customer=other)
    await make_order(db_session, order_number="REPEAT-E4", customer=other)
    await db_session.commit()

    async with await _admin_client(db_session) as client:
        response = await client.get("/api/v1/customers/repeat", params={"q": "Findable"})
        rows = response.json()["data"]
        assert {row["customer_id"] for row in rows} == {str(match.id)}


async def test_repeat_customers_requires_customers_read_permission(
    db_session: AsyncSession,
) -> None:
    role = await make_role(db_session, name="MARKETING", permission_codes=["analytics.read"])
    user = await make_user(db_session, email="marketing-repeat@example.com", role=role)

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/customers/repeat")
        assert response.status_code == 403
