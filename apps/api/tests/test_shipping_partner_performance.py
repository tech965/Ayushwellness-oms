"""OMS SOW Module 4 -- Shipping Partner Allocation & Performance Analysis.

Covers: allocation % summing to exactly 100, zero-denominator guards
(return None, never a misleading 0%), COD/Prepaid delivery success rate
correctness, RTO %/delivery-success-rate correctness against
hand-computed values, date-range filtering, and that unintegrated
partners (no Courier row) simply never appear rather than being shown
with fabricated figures -- via the live `/shipments/partner-allocation`
and `/shipments/partner-performance` endpoints.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.db.session import get_db
from app.main import app
from app.models.courier import Courier
from app.models.enums import OrderStatus, PaymentType, ShipmentStatus
from app.models.shipment import Shipment
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import bearer_client, make_order, make_role, make_user

pytestmark = pytest.mark.asyncio

_PERMS = ["shipments.read"]


@pytest.fixture(autouse=True)
def _clear_get_db_override():
    yield
    app.dependency_overrides.clear()


async def _make_reader(db_session: AsyncSession, *, email: str):
    role = await make_role(db_session, name=f"OPS-{email}", permission_codes=_PERMS)
    return await make_user(db_session, email=email, role=role)


async def _make_courier(db_session: AsyncSession, *, name: str, code: str) -> Courier:
    courier = Courier(name=name, code=code)
    db_session.add(courier)
    await db_session.commit()
    await db_session.refresh(courier)
    return courier


async def _add_shipment(
    db_session: AsyncSession,
    *,
    order,
    current_status: ShipmentStatus,
    courier_id: uuid.UUID | None,
    pickup_date: datetime | None = None,
    actual_delivery_date: datetime | None = None,
) -> Shipment:
    shipment = Shipment(
        order_id=order.id,
        current_status=current_status,
        courier_id=courier_id,
        pickup_date=pickup_date,
        actual_delivery_date=actual_delivery_date,
        source_system="manual",
    )
    db_session.add(shipment)
    await db_session.commit()
    await db_session.refresh(shipment)
    return shipment


async def _set_confirmed_at(db_session: AsyncSession, order, confirmed_at: datetime) -> None:
    order.confirmed_at = confirmed_at
    await db_session.commit()


# ---------------------------------------------------------------------------
# Section A: allocation dashboard
# ---------------------------------------------------------------------------


async def test_allocation_percentages_sum_to_exactly_100(db_session: AsyncSession) -> None:
    """3 couriers with an uneven split (7/3/1 of 11) rounds to fractions
    that would NOT sum to 100.00 under plain rounding -- the
    largest-remainder adjustment must still land on exactly 100.00.
    """
    user = await _make_reader(db_session, email="alloc1@example.com")
    now = datetime.now(UTC)
    courier_a = await _make_courier(db_session, name="Shiprocket", code="PA1-SR")
    courier_b = await _make_courier(db_session, name="NimbusPost", code="PA1-NP")
    courier_c = await _make_courier(db_session, name="Blue Dart", code="PA1-BD")

    for i in range(7):
        order = await make_order(db_session, order_number=f"PA1-A-{i}", order_datetime=now)
        await _add_shipment(
            db_session,
            order=order,
            current_status=ShipmentStatus.IN_TRANSIT,
            courier_id=courier_a.id,
        )
    for i in range(3):
        order = await make_order(db_session, order_number=f"PA1-B-{i}", order_datetime=now)
        await _add_shipment(
            db_session,
            order=order,
            current_status=ShipmentStatus.IN_TRANSIT,
            courier_id=courier_b.id,
        )
    order = await make_order(db_session, order_number="PA1-C-0", order_datetime=now)
    await _add_shipment(
        db_session, order=order, current_status=ShipmentStatus.IN_TRANSIT, courier_id=courier_c.id
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(
            "/api/v1/shipments/partner-allocation",
            params={
                "date_from": (now - timedelta(hours=1)).isoformat(),
                "date_to": (now + timedelta(hours=1)).isoformat(),
            },
        )
        assert response.status_code == 200
        data = response.json()["data"]

    assert data["total_orders_allocated"] == 11
    assert sum(p["allocation_percent"] for p in data["partners"]) == pytest.approx(100.0, abs=0.001)
    assert data["total_allocation_percent"] == pytest.approx(100.0, abs=0.001)
    by_name = {p["courier_name"]: p for p in data["partners"]}
    assert by_name["Shiprocket"]["orders_allocated"] == 7
    assert by_name["NimbusPost"]["orders_allocated"] == 3
    assert by_name["Blue Dart"]["orders_allocated"] == 1


async def test_order_with_no_shipment_is_not_allocated(db_session: AsyncSession) -> None:
    """An order with no Shipment row (no courier assigned yet) must not
    appear in any partner's allocation -- it isn't "allocated" to anyone.
    """
    user = await _make_reader(db_session, email="alloc2@example.com")
    now = datetime.now(UTC)
    courier = await _make_courier(db_session, name="Shiprocket", code="PA2-SR")
    order_a = await make_order(db_session, order_number="PA2-ALLOC", order_datetime=now)
    await _add_shipment(
        db_session, order=order_a, current_status=ShipmentStatus.PICKED_UP, courier_id=courier.id
    )
    await make_order(
        db_session, order_number="PA2-UNALLOC", status=OrderStatus.PENDING, order_datetime=now
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(
            "/api/v1/shipments/partner-allocation",
            params={
                "date_from": (now - timedelta(hours=1)).isoformat(),
                "date_to": (now + timedelta(hours=1)).isoformat(),
            },
        )
        data = response.json()["data"]

    assert data["total_orders_allocated"] == 1
    assert len(data["partners"]) == 1
    assert data["partners"][0]["orders_allocated"] == 1


async def test_unintegrated_partner_never_appears(db_session: AsyncSession) -> None:
    """A courier with zero allocated shipments (e.g. a would-be NimbusPost
    row that was never actually integrated/used) simply doesn't appear --
    no fabricated zero-filled row.
    """
    user = await _make_reader(db_session, email="alloc3@example.com")
    now = datetime.now(UTC)
    used = await _make_courier(db_session, name="Shiprocket", code="PA3-USED")
    await _make_courier(db_session, name="NimbusPost (unused)", code="PA3-UNUSED")
    order = await make_order(db_session, order_number="PA3-ORDER", order_datetime=now)
    await _add_shipment(
        db_session, order=order, current_status=ShipmentStatus.PICKED_UP, courier_id=used.id
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(
            "/api/v1/shipments/partner-allocation",
            params={
                "date_from": (now - timedelta(hours=1)).isoformat(),
                "date_to": (now + timedelta(hours=1)).isoformat(),
            },
        )
        data = response.json()["data"]

    names = {p["courier_name"] for p in data["partners"]}
    assert names == {"Shiprocket"}


# ---------------------------------------------------------------------------
# Section B: performance comparison
# ---------------------------------------------------------------------------


async def test_delivery_success_rate_and_rto_percentage(db_session: AsyncSession) -> None:
    """4 shipped orders for one courier: 2 delivered, 1 RTO-delivered, 1
    still in transit -> delivery_success_rate = 2/4 = 50%, rto_percentage
    = 1/4 = 25% (both against "total shipped", same denominator
    philosophy as Module 2's Delivery%/RTO%).
    """
    user = await _make_reader(db_session, email="perf1@example.com")
    now = datetime.now(UTC)
    courier = await _make_courier(db_session, name="Shiprocket", code="PB1-SR")

    o1 = await make_order(db_session, order_number="PB1-1", order_datetime=now)
    await _add_shipment(
        db_session,
        order=o1,
        current_status=ShipmentStatus.DELIVERED,
        courier_id=courier.id,
        pickup_date=now - timedelta(days=3),
        actual_delivery_date=now,
    )
    o2 = await make_order(db_session, order_number="PB1-2", order_datetime=now)
    await _add_shipment(
        db_session,
        order=o2,
        current_status=ShipmentStatus.DELIVERED,
        courier_id=courier.id,
        pickup_date=now - timedelta(days=3),
        actual_delivery_date=now,
    )
    o3 = await make_order(db_session, order_number="PB1-3", order_datetime=now)
    await _add_shipment(
        db_session, order=o3, current_status=ShipmentStatus.RTO_DELIVERED, courier_id=courier.id
    )
    o4 = await make_order(db_session, order_number="PB1-4", order_datetime=now)
    await _add_shipment(
        db_session, order=o4, current_status=ShipmentStatus.IN_TRANSIT, courier_id=courier.id
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(
            "/api/v1/shipments/partner-performance",
            params={
                "date_from": (now - timedelta(hours=1)).isoformat(),
                "date_to": (now + timedelta(hours=1)).isoformat(),
            },
        )
        data = response.json()["data"]

    row = data["partners"][0]
    assert row["total_allocated"] == 4
    assert row["total_picked_up"] == 4
    assert row["total_delivered"] == 2
    assert row["delivery_success_rate"] == pytest.approx(50.0, abs=0.01)
    assert row["rto_percentage"] == pytest.approx(25.0, abs=0.01)
    assert data["shipping_cost_available"] is False
    assert row["avg_shipping_cost_per_delivered_order"] is None
    assert row["avg_shipping_cost_per_rto_order"] is None


async def test_zero_denominator_returns_none_not_zero(db_session: AsyncSession) -> None:
    """A courier with only `pickup_pending`-stage allocation (nothing
    shipped yet) must report `None` for delivery_success_rate/
    rto_percentage, never a misleading `0.0`.
    """
    user = await _make_reader(db_session, email="perf2@example.com")
    now = datetime.now(UTC)
    courier = await _make_courier(db_session, name="Shiprocket", code="PB2-SR")
    order = await make_order(db_session, order_number="PB2-PENDING", order_datetime=now)
    await _add_shipment(
        db_session, order=order, current_status=ShipmentStatus.PENDING, courier_id=courier.id
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(
            "/api/v1/shipments/partner-performance",
            params={
                "date_from": (now - timedelta(hours=1)).isoformat(),
                "date_to": (now + timedelta(hours=1)).isoformat(),
            },
        )
        data = response.json()["data"]

    row = data["partners"][0]
    assert row["total_allocated"] == 1
    assert row["total_picked_up"] == 0
    assert row["delivery_success_rate"] is None
    assert row["rto_percentage"] is None
    assert row["cod_delivery_success_rate"] is None
    assert row["prepaid_delivery_success_rate"] is None


async def test_cod_and_prepaid_success_rates_computed_independently(
    db_session: AsyncSession,
) -> None:
    """1 COD order delivered, 1 COD order RTO'd (success rate 50%); 1
    Prepaid order delivered (success rate 100%) -- same courier, must not
    cross-contaminate between payment types.
    """
    user = await _make_reader(db_session, email="perf3@example.com")
    now = datetime.now(UTC)
    courier = await _make_courier(db_session, name="Shiprocket", code="PB3-SR")

    cod_delivered = await make_order(
        db_session, order_number="PB3-COD-OK", payment_type=PaymentType.COD, order_datetime=now
    )
    await _add_shipment(
        db_session,
        order=cod_delivered,
        current_status=ShipmentStatus.DELIVERED,
        courier_id=courier.id,
        pickup_date=now - timedelta(days=1),
        actual_delivery_date=now,
    )
    cod_rto = await make_order(
        db_session, order_number="PB3-COD-RTO", payment_type=PaymentType.COD, order_datetime=now
    )
    await _add_shipment(
        db_session,
        order=cod_rto,
        current_status=ShipmentStatus.RTO_DELIVERED,
        courier_id=courier.id,
    )

    prepaid_delivered = await make_order(
        db_session,
        order_number="PB3-PREPAID-OK",
        payment_type=PaymentType.PREPAID,
        order_datetime=now,
    )
    await _add_shipment(
        db_session,
        order=prepaid_delivered,
        current_status=ShipmentStatus.DELIVERED,
        courier_id=courier.id,
        pickup_date=now - timedelta(days=1),
        actual_delivery_date=now,
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(
            "/api/v1/shipments/partner-performance",
            params={
                "date_from": (now - timedelta(hours=1)).isoformat(),
                "date_to": (now + timedelta(hours=1)).isoformat(),
            },
        )
        data = response.json()["data"]

    row = data["partners"][0]
    assert row["cod_delivery_success_rate"] == pytest.approx(50.0, abs=0.01)
    assert row["prepaid_delivery_success_rate"] == pytest.approx(100.0, abs=0.01)


async def test_fulfilment_to_delivery_days(db_session: AsyncSession) -> None:
    """order creation/confirmation -> actual delivery = 5 days, distinct
    from the pickup->delivery leg (which would be shorter).
    """
    user = await _make_reader(db_session, email="perf4@example.com")
    now = datetime.now(UTC)
    courier = await _make_courier(db_session, name="Shiprocket", code="PB4-SR")
    order = await make_order(
        db_session, order_number="PB4-FTD", order_datetime=now - timedelta(days=5)
    )
    await _set_confirmed_at(db_session, order, now - timedelta(days=5))
    await _add_shipment(
        db_session,
        order=order,
        current_status=ShipmentStatus.DELIVERED,
        courier_id=courier.id,
        pickup_date=now - timedelta(days=2),
        actual_delivery_date=now,
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(
            "/api/v1/shipments/partner-performance",
            params={
                "date_from": (now - timedelta(days=6)).isoformat(),
                "date_to": (now + timedelta(hours=1)).isoformat(),
            },
        )
        data = response.json()["data"]

    row = data["partners"][0]
    assert row["avg_fulfilment_to_delivery_days"] == pytest.approx(5.0, abs=0.02)
    assert row["avg_delivery_days"] == pytest.approx(2.0, abs=0.02)


async def test_date_range_filter_excludes_orders_outside_window(db_session: AsyncSession) -> None:
    user = await _make_reader(db_session, email="perf5@example.com")
    now = datetime.now(UTC)
    courier = await _make_courier(db_session, name="Shiprocket", code="PB5-SR")
    in_window = await make_order(db_session, order_number="PB5-IN", order_datetime=now)
    await _add_shipment(
        db_session, order=in_window, current_status=ShipmentStatus.IN_TRANSIT, courier_id=courier.id
    )
    out_of_window = await make_order(
        db_session, order_number="PB5-OUT", order_datetime=now - timedelta(days=30)
    )
    await _add_shipment(
        db_session,
        order=out_of_window,
        current_status=ShipmentStatus.IN_TRANSIT,
        courier_id=courier.id,
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(
            "/api/v1/shipments/partner-performance",
            params={
                "date_from": (now - timedelta(hours=1)).isoformat(),
                "date_to": (now + timedelta(hours=1)).isoformat(),
            },
        )
        data = response.json()["data"]

    assert data["partners"][0]["total_allocated"] == 1
