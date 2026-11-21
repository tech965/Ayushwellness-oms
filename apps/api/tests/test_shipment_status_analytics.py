"""OMS SOW Module 2 — unified 12-status classification + analytics.

Covers: the pure classifier for every bucket (including the 3 documented
judgment calls), the live SQL aggregation path end-to-end through
`OrderRepository`, and the `/shipments/status-analytics*` endpoints
(percentage math, filters, drill-down pagination, history, export).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.db.session import get_db
from app.main import app
from app.models.courier import Courier
from app.models.enums import (
    FulfillmentStatus,
    OrderStatus,
    PaymentType,
    RTOStatus,
    ShipmentStatus,
)
from app.models.order import OrderItem
from app.models.shipment import Shipment
from app.repositories.order import OrderRepository
from app.schemas.common import PageParams
from app.services.shipment_status import classify_unified_status
from app.services.shipment_status_analytics_service import ShipmentStatusAnalyticsService
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import bearer_client, make_order, make_role, make_user

pytestmark = pytest.mark.asyncio

_PERMS = ["shipments.read"]


@pytest.fixture(autouse=True)
def _clear_get_db_override():
    yield
    app.dependency_overrides.clear()


async def _make_shipments_reader(db_session: AsyncSession, *, email: str):
    role = await make_role(db_session, name=f"OPS-{email}", permission_codes=_PERMS)
    return await make_user(db_session, email=email, role=role)


async def _add_shipment(
    db_session: AsyncSession,
    *,
    order,
    current_status: ShipmentStatus,
    rto_status: RTOStatus | None = None,
    courier_id: uuid.UUID | None = None,
    awb: str | None = None,
) -> Shipment:
    shipment = Shipment(
        order_id=order.id,
        current_status=current_status,
        rto_status=rto_status,
        courier_id=courier_id,
        awb=awb,
        source_system="manual",
    )
    db_session.add(shipment)
    await db_session.commit()
    await db_session.refresh(shipment)
    return shipment


# ---------------------------------------------------------------------------
# 1. Pure classifier — every bucket, plus the documented judgment calls.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("order_status", "fulfillment_status", "shipment_status", "rto_status", "expected"),
    [
        (OrderStatus.PENDING, FulfillmentStatus.UNFULFILLED, None, None, "order_placed"),
        (OrderStatus.CONFIRMED, FulfillmentStatus.UNFULFILLED, None, None, "ready_to_ship"),
        (OrderStatus.PROCESSING, FulfillmentStatus.UNFULFILLED, None, None, "ready_to_ship"),
        (OrderStatus.PACKED, FulfillmentStatus.UNFULFILLED, None, None, "ready_to_ship"),
        (
            OrderStatus.CONFIRMED,
            FulfillmentStatus.UNFULFILLED,
            ShipmentStatus.PENDING,
            None,
            "pickup_pending",
        ),
        (
            OrderStatus.CONFIRMED,
            FulfillmentStatus.UNFULFILLED,
            ShipmentStatus.PICKED_UP,
            None,
            "picked_up",
        ),
        (
            OrderStatus.CONFIRMED,
            FulfillmentStatus.UNFULFILLED,
            ShipmentStatus.IN_TRANSIT,
            None,
            "in_transit",
        ),
        (
            OrderStatus.CONFIRMED,
            FulfillmentStatus.UNFULFILLED,
            ShipmentStatus.OUT_FOR_DELIVERY,
            None,
            "out_for_delivery",
        ),
        (
            OrderStatus.CONFIRMED,
            FulfillmentStatus.UNFULFILLED,
            ShipmentStatus.DELIVERED,
            None,
            "delivered",
        ),
        (
            OrderStatus.CONFIRMED,
            FulfillmentStatus.UNFULFILLED,
            ShipmentStatus.NDR,
            None,
            "ndr",
        ),
        (
            OrderStatus.CONFIRMED,
            FulfillmentStatus.UNFULFILLED,
            ShipmentStatus.RTO_INITIATED,
            None,
            "rto_initiated",
        ),
        (
            OrderStatus.CONFIRMED,
            FulfillmentStatus.UNFULFILLED,
            ShipmentStatus.RTO_INITIATED,
            RTOStatus.INITIATED,
            "rto_initiated",
        ),
        (
            OrderStatus.CONFIRMED,
            FulfillmentStatus.UNFULFILLED,
            ShipmentStatus.RTO_INITIATED,
            RTOStatus.IN_TRANSIT,
            "rto_in_transit",
        ),
        (
            OrderStatus.CONFIRMED,
            FulfillmentStatus.UNFULFILLED,
            ShipmentStatus.RTO_DELIVERED,
            None,
            "rto_delivered",
        ),
        (
            OrderStatus.CONFIRMED,
            FulfillmentStatus.UNFULFILLED,
            ShipmentStatus.RTO_DELIVERED,
            RTOStatus.RECEIVED,
            "rto_delivered",
        ),
        (
            OrderStatus.CONFIRMED,
            FulfillmentStatus.UNFULFILLED,
            ShipmentStatus.CANCELLED,
            None,
            "cancelled",
        ),
        (OrderStatus.CANCELLED, FulfillmentStatus.UNFULFILLED, None, None, "cancelled"),
        # Judgment call (2): Shopify-fulfilled, no local Shipment row -> its own
        # bucket (no tracking = no delivery confirmation), never `delivered`.
        (OrderStatus.SHIPPED, FulfillmentStatus.FULFILLED, None, None, "fulfilled_tracking_unavailable"),
        (OrderStatus.DELIVERED, FulfillmentStatus.FULFILLED, None, None, "fulfilled_tracking_unavailable"),
        (OrderStatus.SHIPPED, FulfillmentStatus.UNFULFILLED, None, None, "ready_to_ship"),
        # Regression: Shopify marks the order FULFILLED while `Order.status` is
        # still CONFIRMED/PROCESSING/PACKED and no local Shipment exists --
        # it must NOT be reported as awaiting shipment.
        (OrderStatus.CONFIRMED, FulfillmentStatus.FULFILLED, None, None, "fulfilled_tracking_unavailable"),
        (OrderStatus.PROCESSING, FulfillmentStatus.FULFILLED, None, None, "fulfilled_tracking_unavailable"),
        (OrderStatus.PACKED, FulfillmentStatus.FULFILLED, None, None, "fulfilled_tracking_unavailable"),
        # ...while genuinely unfulfilled (or only partially fulfilled)
        # confirmed orders stay ready_to_ship.
        (OrderStatus.CONFIRMED, FulfillmentStatus.PARTIAL, None, None, "ready_to_ship"),
        (OrderStatus.PACKED, FulfillmentStatus.PARTIAL, None, None, "ready_to_ship"),
        # A live local shipment still wins over Shopify's fulfilment flag.
        (
            OrderStatus.CONFIRMED,
            FulfillmentStatus.FULFILLED,
            ShipmentStatus.PENDING,
            None,
            "pickup_pending",
        ),
        # Judgment call (3): live shipment wins over a stale/impossible order status.
        (
            OrderStatus.PENDING,
            FulfillmentStatus.UNFULFILLED,
            ShipmentStatus.PICKED_UP,
            None,
            "picked_up",
        ),
    ],
)
def test_classify_unified_status_covers_every_bucket(
    order_status, fulfillment_status, shipment_status, rto_status, expected
):
    result = classify_unified_status(
        order_status=order_status.value,
        fulfillment_status=fulfillment_status.value,
        shipment_current_status=shipment_status.value if shipment_status else None,
        shipment_rto_status=rto_status.value if rto_status else None,
    )
    assert result == expected


def test_all_12_sow_statuses_are_reachable():
    from app.services.shipment_status import UNIFIED_STATUSES

    assert len(UNIFIED_STATUSES) == 13
    assert set(UNIFIED_STATUSES) == {
        "order_placed",
        "ready_to_ship",
        "pickup_pending",
        "picked_up",
        "in_transit",
        "out_for_delivery",
        "delivered",
        "rto_initiated",
        "rto_in_transit",
        "rto_delivered",
        "ndr",
        "cancelled",
        "fulfilled_tracking_unavailable",
    }


# ---------------------------------------------------------------------------
# 2. Live SQL aggregation (OrderRepository.unified_status_counts) — seeds
#    one order per bucket and asserts the real query classifies each
#    correctly, proving the SQL `case()` mirrors the Python classifier.
# ---------------------------------------------------------------------------


async def _seed_one_of_each_bucket(db_session: AsyncSession) -> dict[str, uuid.UUID]:
    order_ids: dict[str, uuid.UUID] = {}

    order_placed = await make_order(
        db_session, order_number="SA-PLACED", status=OrderStatus.PENDING
    )
    order_ids["order_placed"] = order_placed.id

    ready = await make_order(db_session, order_number="SA-READY", status=OrderStatus.CONFIRMED)
    order_ids["ready_to_ship"] = ready.id

    pickup_pending_order = await make_order(
        db_session, order_number="SA-PICKUP-PENDING", status=OrderStatus.CONFIRMED
    )
    await _add_shipment(
        db_session, order=pickup_pending_order, current_status=ShipmentStatus.PENDING
    )
    order_ids["pickup_pending"] = pickup_pending_order.id

    untracked = await make_order(
        db_session,
        order_number="SA-FULFILLED-NO-TRACKING",
        status=OrderStatus.CONFIRMED,
        fulfillment_status=FulfillmentStatus.FULFILLED,
    )
    order_ids["fulfilled_tracking_unavailable"] = untracked.id

    bucket_to_shipment_status = {
        "picked_up": (ShipmentStatus.PICKED_UP, None),
        "in_transit": (ShipmentStatus.IN_TRANSIT, None),
        "out_for_delivery": (ShipmentStatus.OUT_FOR_DELIVERY, None),
        "delivered": (ShipmentStatus.DELIVERED, None),
        "rto_initiated": (ShipmentStatus.RTO_INITIATED, None),
        "rto_in_transit": (ShipmentStatus.RTO_INITIATED, RTOStatus.IN_TRANSIT),
        "rto_delivered": (ShipmentStatus.RTO_DELIVERED, None),
        "ndr": (ShipmentStatus.NDR, None),
        "cancelled": (ShipmentStatus.CANCELLED, None),
    }
    for bucket, (shipment_status, rto_status) in bucket_to_shipment_status.items():
        order = await make_order(
            db_session, order_number=f"SA-{bucket.upper()}", status=OrderStatus.CONFIRMED
        )
        await _add_shipment(
            db_session, order=order, current_status=shipment_status, rto_status=rto_status
        )
        order_ids[bucket] = order.id

    return order_ids


async def test_unified_status_counts_classifies_one_order_per_bucket(
    db_session: AsyncSession,
) -> None:
    await _seed_one_of_each_bucket(db_session)
    counts = await OrderRepository(db_session).unified_status_counts()
    for bucket, count in counts.items():
        assert count == 1, f"expected exactly 1 order in {bucket!r}, got {count}"


async def test_sql_classifies_shopify_fulfilled_orders_without_shipment_as_tracking_unavailable(
    db_session: AsyncSession,
) -> None:
    """Regression: confirmed + Shopify-FULFILLED + no local Shipment row used
    to be counted `ready_to_ship` (pending shipment). The SQL case() must
    agree with the Python classifier: they are `fulfilled_tracking_unavailable`
    (never `delivered`); confirmed but genuinely unfulfilled (and partially fulfilled) orders stay `ready_to_ship`.
    """
    await make_order(
        db_session,
        order_number="SA-SHOPIFY-FULFILLED",
        status=OrderStatus.CONFIRMED,
        fulfillment_status=FulfillmentStatus.FULFILLED,
    )
    await make_order(
        db_session,
        order_number="SA-REALLY-UNFULFILLED",
        status=OrderStatus.CONFIRMED,
        fulfillment_status=FulfillmentStatus.UNFULFILLED,
    )
    await make_order(
        db_session,
        order_number="SA-PARTIAL",
        status=OrderStatus.PACKED,
        fulfillment_status=FulfillmentStatus.PARTIAL,
    )
    counts = await OrderRepository(db_session).unified_status_counts()
    assert counts["fulfilled_tracking_unavailable"] == 1
    assert counts["delivered"] == 0
    assert counts["ready_to_ship"] == 2


# ---------------------------------------------------------------------------
# 3. Endpoints — percentages, filters, drill-down, history, export.
# ---------------------------------------------------------------------------


async def test_status_analytics_percentage_math(db_session: AsyncSession) -> None:
    """2 pre-shipped-stage orders (ready_to_ship, pickup_pending --
    eligible, not yet shipped) + 4 shipped-or-later orders (picked_up,
    in_transit, delivered, rto_delivered) -> hand-computed expected
    rates.
    """
    user = await _make_shipments_reader(db_session, email="pct@example.com")

    await make_order(db_session, order_number="PCT-READY", status=OrderStatus.CONFIRMED)
    pending_order = await make_order(
        db_session, order_number="PCT-PENDING", status=OrderStatus.CONFIRMED
    )
    await _add_shipment(db_session, order=pending_order, current_status=ShipmentStatus.PENDING)

    picked = await make_order(db_session, order_number="PCT-PICKED", status=OrderStatus.CONFIRMED)
    await _add_shipment(db_session, order=picked, current_status=ShipmentStatus.PICKED_UP)

    transit = await make_order(
        db_session, order_number="PCT-TRANSIT", status=OrderStatus.CONFIRMED
    )
    await _add_shipment(db_session, order=transit, current_status=ShipmentStatus.IN_TRANSIT)

    delivered = await make_order(
        db_session, order_number="PCT-DELIVERED", status=OrderStatus.CONFIRMED
    )
    await _add_shipment(db_session, order=delivered, current_status=ShipmentStatus.DELIVERED)

    rto = await make_order(db_session, order_number="PCT-RTO", status=OrderStatus.CONFIRMED)
    await _add_shipment(db_session, order=rto, current_status=ShipmentStatus.RTO_DELIVERED)

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/shipments/status-analytics")
        assert response.status_code == 200
        data = response.json()["data"]

    # eligible = everything except order_placed/cancelled = 6 orders.
    # shipped-or-later = picked_up + in_transit + delivered + rto_delivered = 4.
    assert data["total_eligible_orders"] == 6
    assert data["total_shipped_orders"] == 4
    assert data["pickup_rate"] == pytest.approx(1 / 6 * 100, abs=0.01)
    assert data["in_transit_rate"] == pytest.approx(1 / 4 * 100, abs=0.01)
    assert data["delivery_rate"] == pytest.approx(1 / 4 * 100, abs=0.01)
    assert data["rto_rate"] == pytest.approx(1 / 4 * 100, abs=0.01)
    breakdown = {row["status"]: row["count"] for row in data["status_breakdown"]}
    assert breakdown["picked_up"] == 1
    assert breakdown["rto_delivered"] == 1
    assert data["total_orders"] == 6


async def test_status_analytics_courier_filter(db_session: AsyncSession) -> None:
    user = await _make_shipments_reader(db_session, email="courier@example.com")
    courier_a = Courier(name="Shiprocket", code="SR-TEST")
    courier_b = Courier(name="Other", code="OTH-TEST")
    db_session.add_all([courier_a, courier_b])
    await db_session.commit()

    order_a = await make_order(db_session, order_number="CF-A", status=OrderStatus.CONFIRMED)
    await _add_shipment(
        db_session,
        order=order_a,
        current_status=ShipmentStatus.PICKED_UP,
        courier_id=courier_a.id,
    )
    order_b = await make_order(db_session, order_number="CF-B", status=OrderStatus.CONFIRMED)
    await _add_shipment(
        db_session,
        order=order_b,
        current_status=ShipmentStatus.PICKED_UP,
        courier_id=courier_b.id,
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(
            "/api/v1/shipments/status-analytics", params={"courier_id": str(courier_a.id)}
        )
        assert response.status_code == 200
        data = response.json()["data"]
        breakdown = {row["status"]: row["count"] for row in data["status_breakdown"]}
        assert breakdown["picked_up"] == 1
        assert data["total_orders"] == 1


async def test_status_analytics_payment_type_filter(db_session: AsyncSession) -> None:
    user = await _make_shipments_reader(db_session, email="pay@example.com")
    await make_order(
        db_session, order_number="PAY-COD", status=OrderStatus.CONFIRMED, payment_type=PaymentType.COD
    )
    await make_order(
        db_session,
        order_number="PAY-PREPAID",
        status=OrderStatus.CONFIRMED,
        payment_type=PaymentType.PREPAID,
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(
            "/api/v1/shipments/status-analytics", params={"payment_type": "cod"}
        )
        assert response.status_code == 200
        assert response.json()["data"]["total_orders"] == 1


async def test_status_analytics_sku_filter(db_session: AsyncSession) -> None:
    user = await _make_shipments_reader(db_session, email="sku@example.com")
    order_with_sku = await make_order(
        db_session, order_number="SKU-MATCH", status=OrderStatus.CONFIRMED
    )
    db_session.add(
        OrderItem(
            order_id=order_with_sku.id,
            sku="AW-HM-PN-60",
            product_name="Paan Flavour",
            quantity=1,
            unit_price=Decimal("299.00"),
            total_amount=Decimal("299.00"),
        )
    )
    await db_session.commit()
    await make_order(db_session, order_number="SKU-NOMATCH", status=OrderStatus.CONFIRMED)

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(
            "/api/v1/shipments/status-analytics", params={"sku": "AW-HM-PN-60"}
        )
        assert response.status_code == 200
        assert response.json()["data"]["total_orders"] == 1


async def test_status_analytics_date_range_filter(db_session: AsyncSession) -> None:
    user = await _make_shipments_reader(db_session, email="daterange@example.com")
    now = datetime.now(UTC)
    await make_order(
        db_session,
        order_number="DATE-OLD",
        status=OrderStatus.CONFIRMED,
        order_datetime=now - timedelta(days=60),
    )
    await make_order(
        db_session,
        order_number="DATE-RECENT",
        status=OrderStatus.CONFIRMED,
        order_datetime=now - timedelta(days=1),
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(
            "/api/v1/shipments/status-analytics",
            params={"date_from": (now - timedelta(days=7)).isoformat()},
        )
        assert response.status_code == 200
        assert response.json()["data"]["total_orders"] == 1


async def test_status_drilldown_pagination(db_session: AsyncSession) -> None:
    user = await _make_shipments_reader(db_session, email="drill@example.com")
    for i in range(3):
        await make_order(db_session, order_number=f"DRILL-{i}", status=OrderStatus.CONFIRMED)

    async with bearer_client(app, get_db, db_session, user.id) as client:
        page1 = await client.get(
            "/api/v1/shipments/status-analytics/orders",
            params={"unified_status": "ready_to_ship", "page": 1, "page_size": 2},
        )
        assert page1.status_code == 200
        body1 = page1.json()
        assert body1["meta"]["total_items"] == 3
        assert len(body1["data"]) == 2

        page2 = await client.get(
            "/api/v1/shipments/status-analytics/orders",
            params={"unified_status": "ready_to_ship", "page": 2, "page_size": 2},
        )
        assert len(page2.json()["data"]) == 1

        for row in body1["data"]:
            assert row["unified_status"] == "ready_to_ship"


async def test_status_drilldown_rejects_unknown_status(db_session: AsyncSession) -> None:
    user = await _make_shipments_reader(db_session, email="badstatus@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(
            "/api/v1/shipments/status-analytics/orders",
            params={"unified_status": "not_a_real_status"},
        )
        assert response.status_code == 422


async def test_status_history_daily_buckets(db_session: AsyncSession) -> None:
    user = await _make_shipments_reader(db_session, email="history@example.com")
    now = datetime.now(UTC)
    await make_order(
        db_session, order_number="HIST-1", status=OrderStatus.PENDING, order_datetime=now
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/shipments/status-analytics/history")
        assert response.status_code == 200
        daily = response.json()["data"]["daily"]
        assert len(daily) >= 1
        today_point = daily[-1]
        assert today_point["order_placed"] >= 1


async def test_status_history_export_returns_xlsx(db_session: AsyncSession) -> None:
    user = await _make_shipments_reader(db_session, email="export@example.com")
    await make_order(db_session, order_number="EXPORT-1", status=OrderStatus.PENDING)

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/shipments/status-analytics/history/export")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument"
        )
        assert len(response.content) > 0


# ---------------------------------------------------------------------------
# 4. Service-level unit test for the export workbook shape (no HTTP).
# ---------------------------------------------------------------------------


async def test_export_xlsx_has_header_row_with_all_13_statuses(
    db_session: AsyncSession,
) -> None:
    from openpyxl import load_workbook
    from io import BytesIO

    service = ShipmentStatusAnalyticsService(db_session)
    now = datetime.now(UTC)
    content = await service.export_status_history_xlsx(
        date_from=now - timedelta(days=1), date_to=now
    )
    wb = load_workbook(BytesIO(content))
    ws = wb.active
    header = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
    assert header[0] == "Date"
    assert "order_placed" in header
    assert "cancelled" in header
    assert "fulfilled_tracking_unavailable" in header
    assert len(header) == 1 + 13


async def test_tracking_unavailable_orders_never_inflate_delivery_or_pickup_kpis(
    db_session: AsyncSession,
) -> None:
    """KPI regression: 3 Shopify-fulfilled orders with no shipment row, next
    to 1 really-delivered and 1 really-picked-up shipment and 1 genuinely
    unfulfilled confirmed order. The tracking-unavailable orders must sit
    outside BOTH denominators (eligible-for-pickup and shipped) and never
    count as delivered, so Delivery % stays 1/2, not 4/5 or 100%.
    """
    user = await _make_shipments_reader(db_session, email="kpi-tu@example.com")
    for i in range(3):
        await make_order(
            db_session,
            order_number=f"KPI-TU-{i}",
            status=OrderStatus.CONFIRMED,
            fulfillment_status=FulfillmentStatus.FULFILLED,
        )
    delivered = await make_order(db_session, order_number="KPI-DEL", status=OrderStatus.CONFIRMED)
    await _add_shipment(db_session, order=delivered, current_status=ShipmentStatus.DELIVERED)
    picked = await make_order(db_session, order_number="KPI-PICK", status=OrderStatus.CONFIRMED)
    await _add_shipment(db_session, order=picked, current_status=ShipmentStatus.PICKED_UP)
    await make_order(db_session, order_number="KPI-READY", status=OrderStatus.CONFIRMED)

    async with bearer_client(app, get_db, db_session, user.id) as client:
        data = (await client.get("/api/v1/shipments/status-analytics")).json()["data"]

        breakdown = {row["status"]: row["count"] for row in data["status_breakdown"]}
        assert len(data["status_breakdown"]) == 13
        assert breakdown["fulfilled_tracking_unavailable"] == 3
        assert breakdown["delivered"] == 1
        assert breakdown["ready_to_ship"] == 1
        assert data["total_orders"] == 6
        # eligible = ready_to_ship + picked_up + delivered (NOT the 3 untracked).
        assert data["total_eligible_orders"] == 3
        # shipped = picked_up + delivered.
        assert data["total_shipped_orders"] == 2
        assert data["delivery_rate"] == pytest.approx(50.0, abs=0.01)
        assert data["pickup_rate"] == pytest.approx(1 / 3 * 100, abs=0.01)

        # Status filter / drilldown for the new bucket returns exactly those orders.
        drill = await client.get(
            "/api/v1/shipments/status-analytics/orders",
            params={"unified_status": "fulfilled_tracking_unavailable", "page": 1, "page_size": 10},
        )
        assert drill.status_code == 200
        numbers = sorted(row["order_number"] for row in drill.json()["data"])
        assert numbers == ["KPI-TU-0", "KPI-TU-1", "KPI-TU-2"]
        assert drill.json()["meta"]["total_items"] == 3

        # ...and the Delivered drilldown does NOT include them.
        delivered_drill = await client.get(
            "/api/v1/shipments/status-analytics/orders",
            params={"unified_status": "delivered", "page": 1, "page_size": 10},
        )
        assert [r["order_number"] for r in delivered_drill.json()["data"]] == ["KPI-DEL"]

        # Date-wise history carries the new column.
        daily = (await client.get("/api/v1/shipments/status-analytics/history")).json()["data"][
            "daily"
        ]
        assert sum(point["fulfilled_tracking_unavailable"] for point in daily) == 3
