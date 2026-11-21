"""OMS SOW Module 3 — Fulfilment & Delivery Performance Dashboard.

Covers: fulfilment-time calc (confirmed_at vs order_datetime clock start),
pending->24h/48h bucketing, delivery-time calc with hand-computed
expected values, exclusion of cancelled/RTO shipments from "successfully
delivered" averages, missing-timestamp handling, COD/prepaid/courier/zone
grouping, and date-range filtering -- via the live
`/shipments/fulfilment-performance*` and `/shipments/delivery-performance*`
endpoints.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.db.session import get_db
from app.main import app
from app.models.courier import Courier
from app.models.enums import FulfillmentStatus, OrderStatus, PaymentType, ShipmentStatus
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


async def _add_shipment(
    db_session: AsyncSession,
    *,
    order,
    current_status: ShipmentStatus,
    pickup_date: datetime | None = None,
    actual_delivery_date: datetime | None = None,
    courier_id: uuid.UUID | None = None,
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


async def _set_confirmed_at(db_session: AsyncSession, order, confirmed_at: datetime | None) -> None:
    order.confirmed_at = confirmed_at
    await db_session.commit()


async def _set_state(db_session: AsyncSession, order, state: str | None) -> None:
    order.shipping_address = {"state": state} if state else None
    await db_session.commit()


# ---------------------------------------------------------------------------
# Section A: fulfilment time + pending ageing
# ---------------------------------------------------------------------------


async def test_fulfilment_time_uses_confirmed_at_when_present(db_session: AsyncSession) -> None:
    """confirmed_at is the clock start when set, NOT order_datetime."""
    user = await _make_reader(db_session, email="fa1@example.com")
    now = datetime.now(UTC)
    order = await make_order(
        db_session,
        order_number="FA-CONFIRMED-AT",
        status=OrderStatus.CONFIRMED,
        order_datetime=now - timedelta(days=5),
    )
    await _set_confirmed_at(db_session, order, now - timedelta(days=2))
    await _add_shipment(db_session, order=order, current_status=ShipmentStatus.PICKED_UP, pickup_date=now)

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/shipments/fulfilment-performance")
        assert response.status_code == 200
        data = response.json()["data"]

    # 2 days from confirmed_at, NOT 5 days from order_datetime.
    assert data["avg_fulfilment_days"] == pytest.approx(2.0, abs=0.02)
    assert data["fulfilled_order_count"] == 1


async def test_fulfilment_time_falls_back_to_order_datetime(db_session: AsyncSession) -> None:
    """confirmed_at NULL (e.g. Shopify auto-confirmed) -> order_datetime
    is the fallback clock start, not excluded as a data gap.
    """
    user = await _make_reader(db_session, email="fa2@example.com")
    now = datetime.now(UTC)
    order = await make_order(
        db_session,
        order_number="FA-FALLBACK",
        status=OrderStatus.CONFIRMED,
        order_datetime=now - timedelta(days=3),
    )
    await _add_shipment(db_session, order=order, current_status=ShipmentStatus.PICKED_UP, pickup_date=now)

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/shipments/fulfilment-performance")
        data = response.json()["data"]

    assert data["avg_fulfilment_days"] == pytest.approx(3.0, abs=0.02)


async def test_missing_pickup_timestamp_excluded_and_counted(db_session: AsyncSession) -> None:
    """A shipment already past pickup_pending (in_transit) but with a
    NULL `pickup_date` is a genuine data gap -- excluded from the
    average, counted separately, never imputed.
    """
    user = await _make_reader(db_session, email="fa3@example.com")
    order = await make_order(db_session, order_number="FA-GAP", status=OrderStatus.CONFIRMED)
    await _add_shipment(db_session, order=order, current_status=ShipmentStatus.IN_TRANSIT, pickup_date=None)

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/shipments/fulfilment-performance")
        data = response.json()["data"]

    assert data["fulfilled_order_count"] == 0
    assert data["avg_fulfilment_days"] is None
    assert data["missing_pickup_timestamp_count"] == 1


async def test_pending_fulfilment_24h_48h_buckets(db_session: AsyncSession) -> None:
    user = await _make_reader(db_session, email="fa4@example.com")
    now = datetime.now(UTC)

    fresh = await make_order(db_session, order_number="FA-FRESH", status=OrderStatus.CONFIRMED)
    await _set_confirmed_at(db_session, fresh, now - timedelta(hours=1))

    over24 = await make_order(db_session, order_number="FA-OVER24", status=OrderStatus.CONFIRMED)
    await _set_confirmed_at(db_session, over24, now - timedelta(hours=30))

    over48 = await make_order(db_session, order_number="FA-OVER48", status=OrderStatus.CONFIRMED)
    await _add_shipment(db_session, order=over48, current_status=ShipmentStatus.PENDING)
    await _set_confirmed_at(db_session, over48, now - timedelta(hours=60))

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/shipments/fulfilment-performance")
        data = response.json()["data"]

    assert data["pending_fulfilment_count"] == 3
    assert data["pending_over_24h_count"] == 2
    assert data["pending_over_48h_count"] == 1


async def test_shopify_fulfilled_orders_without_shipment_are_not_pending(
    db_session: AsyncSession,
) -> None:
    """Regression: a CONFIRMED order Shopify already marked FULFILLED (no
    local Shipment row) is not "pending fulfilment" and not a missing-pickup
    data gap; a genuinely unfulfilled confirmed order still is pending.
    """
    user = await _make_reader(db_session, email="fa-shopify@example.com")
    now = datetime.now(UTC)

    fulfilled = await make_order(
        db_session,
        order_number="FA-SHOPIFY-FULFILLED",
        status=OrderStatus.CONFIRMED,
        fulfillment_status=FulfillmentStatus.FULFILLED,
    )
    await _set_confirmed_at(db_session, fulfilled, now - timedelta(hours=100))

    unfulfilled = await make_order(
        db_session,
        order_number="FA-REALLY-PENDING",
        status=OrderStatus.CONFIRMED,
        fulfillment_status=FulfillmentStatus.UNFULFILLED,
    )
    await _set_confirmed_at(db_session, unfulfilled, now - timedelta(hours=100))

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/shipments/fulfilment-performance")
        data = response.json()["data"]

    assert data["pending_fulfilment_count"] == 1
    assert data["pending_over_24h_count"] == 1
    assert data["pending_over_48h_count"] == 1
    assert data["missing_pickup_timestamp_count"] == 0


async def test_order_placed_not_counted_as_pending_fulfilment(db_session: AsyncSession) -> None:
    """Unconfirmed orders (order_placed) aren't "pending fulfilment" --
    there's nothing for the fulfilment team to act on yet.
    """
    user = await _make_reader(db_session, email="fa5@example.com")
    await make_order(db_session, order_number="FA-UNCONFIRMED", status=OrderStatus.PENDING)

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/shipments/fulfilment-performance")
        data = response.json()["data"]

    assert data["pending_fulfilment_count"] == 0


async def test_team_warehouse_attribution_reported_unavailable(db_session: AsyncSession) -> None:
    user = await _make_reader(db_session, email="fa6@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/shipments/fulfilment-performance")
        data = response.json()["data"]

    assert data["team_warehouse_attribution_available"] is False
    assert data["team_warehouse_breakdown"] == []
    assert "warehouse" in data["team_warehouse_attribution_note"].lower()


async def test_fulfilment_history_buckets_by_pickup_day(db_session: AsyncSession) -> None:
    user = await _make_reader(db_session, email="fa7@example.com")
    now = datetime.now(UTC)
    order = await make_order(db_session, order_number="FA-HIST", status=OrderStatus.CONFIRMED)
    await _set_confirmed_at(db_session, order, now - timedelta(days=1))
    await _add_shipment(db_session, order=order, current_status=ShipmentStatus.PICKED_UP, pickup_date=now)

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/shipments/fulfilment-performance/history")
        assert response.status_code == 200
        daily = response.json()["data"]["daily"]

    assert len(daily) == 1
    assert daily[0]["fulfilled_order_count"] == 1
    assert daily[0]["avg_fulfilment_days"] == pytest.approx(1.0, abs=0.02)


# ---------------------------------------------------------------------------
# Section B: delivery performance
# ---------------------------------------------------------------------------


async def test_delivery_days_hand_computed(db_session: AsyncSession) -> None:
    user = await _make_reader(db_session, email="db1@example.com")
    now = datetime.now(UTC)
    order = await make_order(db_session, order_number="DB-BASIC", status=OrderStatus.CONFIRMED)
    await _add_shipment(
        db_session,
        order=order,
        current_status=ShipmentStatus.DELIVERED,
        pickup_date=now - timedelta(days=4),
        actual_delivery_date=now,
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/shipments/delivery-performance")
        assert response.status_code == 200
        data = response.json()["data"]

    assert data["overall_avg_delivery_days"] == pytest.approx(4.0, abs=0.02)
    assert data["delivered_order_count"] == 1


async def test_rto_and_cancelled_excluded_from_delivery_average(db_session: AsyncSession) -> None:
    """An RTO_DELIVERED or CANCELLED shipment never reached the customer
    -- must not pollute the "successfully delivered" average, even if it
    happens to carry pickup/delivery-ish timestamps.
    """
    user = await _make_reader(db_session, email="db2@example.com")
    now = datetime.now(UTC)

    rto_order = await make_order(db_session, order_number="DB-RTO", status=OrderStatus.CONFIRMED)
    await _add_shipment(
        db_session,
        order=rto_order,
        current_status=ShipmentStatus.RTO_DELIVERED,
        pickup_date=now - timedelta(days=10),
        actual_delivery_date=now,
    )

    cancelled_order = await make_order(db_session, order_number="DB-CANCELLED", status=OrderStatus.CONFIRMED)
    await _add_shipment(
        db_session,
        order=cancelled_order,
        current_status=ShipmentStatus.CANCELLED,
        pickup_date=now - timedelta(days=1),
    )

    delivered_order = await make_order(db_session, order_number="DB-REAL", status=OrderStatus.CONFIRMED)
    await _add_shipment(
        db_session,
        order=delivered_order,
        current_status=ShipmentStatus.DELIVERED,
        pickup_date=now - timedelta(days=2),
        actual_delivery_date=now,
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/shipments/delivery-performance")
        data = response.json()["data"]

    assert data["delivered_order_count"] == 1
    assert data["overall_avg_delivery_days"] == pytest.approx(2.0, abs=0.02)


async def test_missing_delivery_timestamp_excluded_and_counted(db_session: AsyncSession) -> None:
    user = await _make_reader(db_session, email="db3@example.com")
    order = await make_order(db_session, order_number="DB-GAP", status=OrderStatus.CONFIRMED)
    await _add_shipment(
        db_session,
        order=order,
        current_status=ShipmentStatus.DELIVERED,
        pickup_date=None,
        actual_delivery_date=datetime.now(UTC),
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/shipments/delivery-performance")
        data = response.json()["data"]

    assert data["delivered_order_count"] == 0
    assert data["overall_avg_delivery_days"] is None
    assert data["missing_timestamp_count"] == 1


async def test_delivery_performance_by_courier(db_session: AsyncSession) -> None:
    user = await _make_reader(db_session, email="db4@example.com")
    now = datetime.now(UTC)
    courier_a = Courier(name="Shiprocket", code="SR-DB-TEST")
    courier_b = Courier(name="Other Partner", code="OTH-DB-TEST")
    db_session.add_all([courier_a, courier_b])
    await db_session.commit()

    order_a = await make_order(db_session, order_number="DB-CA", status=OrderStatus.CONFIRMED)
    await _add_shipment(
        db_session,
        order=order_a,
        current_status=ShipmentStatus.DELIVERED,
        pickup_date=now - timedelta(days=2),
        actual_delivery_date=now,
        courier_id=courier_a.id,
    )
    order_b = await make_order(db_session, order_number="DB-CB", status=OrderStatus.CONFIRMED)
    await _add_shipment(
        db_session,
        order=order_b,
        current_status=ShipmentStatus.DELIVERED,
        pickup_date=now - timedelta(days=6),
        actual_delivery_date=now,
        courier_id=courier_b.id,
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(f"/api/v1/shipments/delivery-performance?courier_id={courier_a.id}")
        data = response.json()["data"]

    assert data["delivered_order_count"] == 1
    assert data["by_courier"] == [
        {
            "courier_id": str(courier_a.id),
            "courier_name": "Shiprocket",
            "avg_delivery_days": pytest.approx(2.0, abs=0.02),
            "delivered_order_count": 1,
        }
    ]


async def test_delivery_performance_cod_vs_prepaid(db_session: AsyncSession) -> None:
    user = await _make_reader(db_session, email="db5@example.com")
    now = datetime.now(UTC)

    cod_order = await make_order(
        db_session, order_number="DB-COD", status=OrderStatus.CONFIRMED, payment_type=PaymentType.COD
    )
    await _add_shipment(
        db_session,
        order=cod_order,
        current_status=ShipmentStatus.DELIVERED,
        pickup_date=now - timedelta(days=3),
        actual_delivery_date=now,
    )
    prepaid_order = await make_order(
        db_session, order_number="DB-PREPAID", status=OrderStatus.CONFIRMED, payment_type=PaymentType.PREPAID
    )
    await _add_shipment(
        db_session,
        order=prepaid_order,
        current_status=ShipmentStatus.DELIVERED,
        pickup_date=now - timedelta(days=1),
        actual_delivery_date=now,
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/shipments/delivery-performance")
        data = response.json()["data"]

    assert data["cod_avg_delivery_days"] == pytest.approx(3.0, abs=0.02)
    assert data["cod_delivered_order_count"] == 1
    assert data["prepaid_avg_delivery_days"] == pytest.approx(1.0, abs=0.02)
    assert data["prepaid_delivered_order_count"] == 1


async def test_delivery_performance_by_zone_normalizes_state(db_session: AsyncSession) -> None:
    """Zone grouping reuses `normalize_state` -- "Orissa" and "Odisha"
    must land in the same zone bucket.
    """
    user = await _make_reader(db_session, email="db6@example.com")
    now = datetime.now(UTC)

    order_a = await make_order(db_session, order_number="DB-ZONE-A", status=OrderStatus.CONFIRMED)
    await _set_state(db_session, order_a, "Orissa")
    await _add_shipment(
        db_session,
        order=order_a,
        current_status=ShipmentStatus.DELIVERED,
        pickup_date=now - timedelta(days=2),
        actual_delivery_date=now,
    )
    order_b = await make_order(db_session, order_number="DB-ZONE-B", status=OrderStatus.CONFIRMED)
    await _set_state(db_session, order_b, "Odisha")
    await _add_shipment(
        db_session,
        order=order_b,
        current_status=ShipmentStatus.DELIVERED,
        pickup_date=now - timedelta(days=4),
        actual_delivery_date=now,
    )
    order_unmapped = await make_order(db_session, order_number="DB-ZONE-UNK", status=OrderStatus.CONFIRMED)
    await _set_state(db_session, order_unmapped, "Not A Real Place")
    await _add_shipment(
        db_session,
        order=order_unmapped,
        current_status=ShipmentStatus.DELIVERED,
        pickup_date=now - timedelta(days=1),
        actual_delivery_date=now,
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/shipments/delivery-performance")
        data = response.json()["data"]

    assert data["zone_is_state_based"] is True
    odisha = next(z for z in data["by_zone"] if z["zone"] == "Odisha")
    assert odisha["delivered_order_count"] == 2
    assert odisha["avg_delivery_days"] == pytest.approx(3.0, abs=0.02)
    assert data["unmapped_zone_order_count"] == 1


async def test_delivery_performance_zone_filter(db_session: AsyncSession) -> None:
    user = await _make_reader(db_session, email="db7@example.com")
    now = datetime.now(UTC)

    order_mh = await make_order(db_session, order_number="DB-MH", status=OrderStatus.CONFIRMED)
    await _set_state(db_session, order_mh, "Maharashtra")
    await _add_shipment(
        db_session,
        order=order_mh,
        current_status=ShipmentStatus.DELIVERED,
        pickup_date=now - timedelta(days=2),
        actual_delivery_date=now,
    )
    order_dl = await make_order(db_session, order_number="DB-DL", status=OrderStatus.CONFIRMED)
    await _set_state(db_session, order_dl, "Delhi")
    await _add_shipment(
        db_session,
        order=order_dl,
        current_status=ShipmentStatus.DELIVERED,
        pickup_date=now - timedelta(days=5),
        actual_delivery_date=now,
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/shipments/delivery-performance?zone=Maharashtra")
        data = response.json()["data"]

    assert data["delivered_order_count"] == 1
    assert data["overall_avg_delivery_days"] == pytest.approx(2.0, abs=0.02)


async def test_delivery_performance_date_range_filter(db_session: AsyncSession) -> None:
    user = await _make_reader(db_session, email="db8@example.com")
    now = datetime.now(UTC)

    in_range = await make_order(
        db_session, order_number="DB-INRANGE", status=OrderStatus.CONFIRMED, order_datetime=now - timedelta(days=1)
    )
    await _add_shipment(
        db_session,
        order=in_range,
        current_status=ShipmentStatus.DELIVERED,
        pickup_date=now - timedelta(days=1),
        actual_delivery_date=now,
    )
    out_of_range = await make_order(
        db_session, order_number="DB-OUTRANGE", status=OrderStatus.CONFIRMED, order_datetime=now - timedelta(days=30)
    )
    await _add_shipment(
        db_session,
        order=out_of_range,
        current_status=ShipmentStatus.DELIVERED,
        pickup_date=now - timedelta(days=30),
        actual_delivery_date=now - timedelta(days=29),
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(
            "/api/v1/shipments/delivery-performance",
            params={"date_from": (now - timedelta(days=5)).isoformat()},
        )
        assert response.status_code == 200
        data = response.json()["data"]

    assert data["delivered_order_count"] == 1


async def test_delivery_performance_history_buckets_by_delivery_day(db_session: AsyncSession) -> None:
    user = await _make_reader(db_session, email="db9@example.com")
    now = datetime.now(UTC)
    order = await make_order(db_session, order_number="DB-HIST", status=OrderStatus.CONFIRMED)
    await _add_shipment(
        db_session,
        order=order,
        current_status=ShipmentStatus.DELIVERED,
        pickup_date=now - timedelta(days=3),
        actual_delivery_date=now,
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/shipments/delivery-performance/history")
        assert response.status_code == 200
        daily = response.json()["data"]["daily"]

    assert len(daily) == 1
    assert daily[0]["delivered_order_count"] == 1
    assert daily[0]["avg_delivery_days"] == pytest.approx(3.0, abs=0.02)


async def test_tracking_unavailable_orders_do_not_affect_delivery_performance(
    db_session: AsyncSession,
) -> None:
    """Delivery calculations only ever use real DELIVERED shipments with both
    timestamps: Shopify-fulfilled orders with no shipment row add nothing to
    the delivered count, the averages or the data-gap counter.
    """
    user = await _make_reader(db_session, email="dp-tu@example.com")
    now = datetime.now(UTC)
    for i in range(2):
        await make_order(
            db_session,
            order_number=f"DP-TU-{i}",
            status=OrderStatus.CONFIRMED,
            fulfillment_status=FulfillmentStatus.FULFILLED,
        )
    real = await make_order(db_session, order_number="DP-REAL", status=OrderStatus.CONFIRMED)
    await _add_shipment(
        db_session,
        order=real,
        current_status=ShipmentStatus.DELIVERED,
        pickup_date=now - timedelta(days=4),
        actual_delivery_date=now - timedelta(days=1),
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        delivery = (await client.get("/api/v1/shipments/delivery-performance")).json()["data"]
        fulfilment = (await client.get("/api/v1/shipments/fulfilment-performance")).json()["data"]

    assert delivery["delivered_order_count"] == 1
    assert delivery["overall_avg_delivery_days"] == pytest.approx(3.0, abs=0.01)
    assert delivery["missing_timestamp_count"] == 0
    # Not pending, not a missing-pickup data gap either.
    assert fulfilment["pending_fulfilment_count"] == 0
    assert fulfilment["missing_pickup_timestamp_count"] == 0
