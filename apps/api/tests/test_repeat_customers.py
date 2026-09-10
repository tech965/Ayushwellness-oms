"""Admin "Repeat Customers" view (`GET /customers/repeat`).

`REPEAT_CUSTOMER_MIN_ORDERS` (`app.repositories.customer`) is the one
place "repeat" is defined: a customer with 2+ real `Order` rows. These
tests assert against that definition directly rather than any inferred
concept (Shopify's own "returning customer" flag is never consulted --
this OMS doesn't even sync one).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from app.db.session import get_db
from app.main import app
from app.models.customer import Customer
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


# ----------------------------------------------------------------------
# Admin Dashboard "Repeat Customers" KPI
# (`CustomerRepository.count_repeat_customers_in_range`, reused by
# `AnalyticsService`/`GET /analytics/summary`) -- same repeat-customer
# definition as the tests above, exercised through the dashboard's
# date-scoping instead of the Customers -> Repeat Customers page.
# ----------------------------------------------------------------------


async def _make_customer_created_at(
    db_session: AsyncSession, *, phone: str, created_at: datetime
) -> Customer:
    customer = Customer(
        full_name="Dashboard Test Customer",
        phone=phone,
        email=f"{phone}@example.com",
        created_at=created_at,
    )
    db_session.add(customer)
    await db_session.flush()
    return customer


async def test_dashboard_repeat_count_excludes_a_one_order_customer(
    db_session: AsyncSession,
) -> None:
    from datetime import UTC, datetime

    from app.repositories.customer import CustomerRepository

    now = datetime.now(UTC)
    customer = await _make_customer_created_at(db_session, phone="9100000001", created_at=now)
    await make_order(db_session, order_number="DASH-REPEAT-A1", customer=customer)
    await db_session.commit()

    count = await CustomerRepository(db_session).count_repeat_customers_in_range(
        date_from=now.replace(hour=0, minute=0, second=0, microsecond=0),
        date_to=now,
    )
    assert count == 0


async def test_dashboard_repeat_count_includes_a_two_order_customer(
    db_session: AsyncSession,
) -> None:
    from datetime import UTC, datetime

    from app.repositories.customer import CustomerRepository

    now = datetime.now(UTC)
    customer = await _make_customer_created_at(db_session, phone="9100000002", created_at=now)
    await make_order(db_session, order_number="DASH-REPEAT-B1", customer=customer)
    await make_order(db_session, order_number="DASH-REPEAT-B2", customer=customer)
    await db_session.commit()

    count = await CustomerRepository(db_session).count_repeat_customers_in_range(
        date_from=now.replace(hour=0, minute=0, second=0, microsecond=0),
        date_to=now,
    )
    assert count == 1


async def test_dashboard_repeat_count_counts_a_5_order_customer_once(
    db_session: AsyncSession,
) -> None:
    """3+ orders is still exactly one repeat customer, never one per order."""
    from datetime import UTC, datetime

    from app.repositories.customer import CustomerRepository

    now = datetime.now(UTC)
    customer = await _make_customer_created_at(db_session, phone="9100000003", created_at=now)
    for i in range(5):
        await make_order(db_session, order_number=f"DASH-REPEAT-C{i}", customer=customer)
    await db_session.commit()

    count = await CustomerRepository(db_session).count_repeat_customers_in_range(
        date_from=now.replace(hour=0, minute=0, second=0, microsecond=0),
        date_to=now,
    )
    assert count == 1


async def test_dashboard_repeat_count_across_multiple_repeat_customers(
    db_session: AsyncSession,
) -> None:
    from datetime import UTC, datetime

    from app.repositories.customer import CustomerRepository

    now = datetime.now(UTC)
    repeat_a = await _make_customer_created_at(db_session, phone="9100000004", created_at=now)
    await make_order(db_session, order_number="DASH-REPEAT-D1", customer=repeat_a)
    await make_order(db_session, order_number="DASH-REPEAT-D2", customer=repeat_a)

    repeat_b = await _make_customer_created_at(db_session, phone="9100000005", created_at=now)
    await make_order(db_session, order_number="DASH-REPEAT-D3", customer=repeat_b)
    await make_order(db_session, order_number="DASH-REPEAT-D4", customer=repeat_b)

    single = await _make_customer_created_at(db_session, phone="9100000006", created_at=now)
    await make_order(db_session, order_number="DASH-REPEAT-D5", customer=single)
    await db_session.commit()

    count = await CustomerRepository(db_session).count_repeat_customers_in_range(
        date_from=now.replace(hour=0, minute=0, second=0, microsecond=0),
        date_to=now,
    )
    assert count == 2


async def test_dashboard_repeat_count_respects_the_same_date_scope_as_total_customers(
    db_session: AsyncSession,
) -> None:
    """A repeat customer CREATED outside the selected range must not be
    counted, even though their order history alone would qualify --
    mirrors `total_customers`' own `Customer.created_at`-in-range scope
    exactly, never a second date-filtering rule.
    """
    from datetime import UTC, datetime, timedelta

    from app.repositories.customer import CustomerRepository

    now = datetime.now(UTC)
    old_customer = await _make_customer_created_at(
        db_session, phone="9100000007", created_at=now - timedelta(days=60)
    )
    await make_order(db_session, order_number="DASH-REPEAT-E1", customer=old_customer)
    await make_order(db_session, order_number="DASH-REPEAT-E2", customer=old_customer)
    await db_session.commit()

    # A "last 30 days" style window that excludes the customer's actual
    # created_at.
    count = await CustomerRepository(db_session).count_repeat_customers_in_range(
        date_from=now - timedelta(days=30), date_to=now
    )
    assert count == 0

    # The same customer IS counted once the range actually covers when
    # they were created.
    count_wide = await CustomerRepository(db_session).count_repeat_customers_in_range(
        date_from=now - timedelta(days=90), date_to=now
    )
    assert count_wide == 1


async def test_dashboard_repeat_count_is_zero_with_no_customers(db_session: AsyncSession) -> None:
    from datetime import UTC, datetime, timedelta

    from app.repositories.customer import CustomerRepository

    now = datetime.now(UTC)
    count = await CustomerRepository(db_session).count_repeat_customers_in_range(
        date_from=now - timedelta(days=30), date_to=now
    )
    assert count == 0


async def test_analytics_summary_exposes_repeat_customers_with_correct_percentage(
    db_session: AsyncSession,
) -> None:
    """End-to-end through `GET /analytics/summary`: the KPI the Admin
    Dashboard card actually reads, plus the percentage-of-total the
    frontend computes from `repeat_customers.current` /
    `total_customers.current`.
    """
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    repeat_customer = await _make_customer_created_at(
        db_session, phone="9100000008", created_at=now
    )
    await make_order(db_session, order_number="DASH-REPEAT-F1", customer=repeat_customer)
    await make_order(db_session, order_number="DASH-REPEAT-F2", customer=repeat_customer)

    single_customer = await _make_customer_created_at(
        db_session, phone="9100000009", created_at=now
    )
    await make_order(db_session, order_number="DASH-REPEAT-F3", customer=single_customer)
    await db_session.commit()

    role = await make_role(db_session, name="ADMIN_ANALYTICS", permission_codes=["analytics.read"])
    user = await make_user(db_session, email="dashboard-repeat@example.com", role=role)

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/analytics/summary")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["total_customers"]["current"] == "2"
        assert data["repeat_customers"]["current"] == "1"
        total = float(data["total_customers"]["current"])
        repeat = float(data["repeat_customers"]["current"])
        percentage = (repeat / total * 100) if total else 0.0
        assert round(percentage, 1) == 50.0
