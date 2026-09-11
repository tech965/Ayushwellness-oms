from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.models.courier import Courier
from app.models.enums import (
    FulfillmentStatus,
    NDRStatus,
    OrderStatus,
    PaymentStatus,
    PaymentType,
    RefundStatus,
    ReturnStatus,
    RTOStatus,
    ShipmentDelayStatus,
    ShipmentStatus,
)
from app.models.ndr import NDR
from app.models.product import Product
from app.models.refund import Refund
from app.models.returns import Return
from app.models.rto import RTO
from app.models.shipment import Shipment
from app.repositories.order import OrderRepository
from app.schemas.order import OrderItemCreateRequest
from app.services.analytics_service import AnalyticsService
from app.services.order_service import OrderService
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_ANALYTICS_PERMS = ["analytics.read", "orders.read", "orders.create"]


async def _create_order(auth_client, order_number: str) -> None:
    payload = {
        "order_number": order_number,
        "payment_type": "prepaid",
        "shipping_charge": "0",
        "items": [
            {
                "sku": "SKU-1",
                "product_name": "Ashwagandha 60ct",
                "quantity": 1,
                "unit_price": "649.00",
            }
        ],
    }
    response = await auth_client.post("/api/v1/orders", json=payload)
    assert response.status_code == 201


async def test_analytics_summary_reflects_orders_in_range(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=_ANALYTICS_PERMS
    ) as auth_client:
        await _create_order(auth_client, "OMS-A-1")
        await _create_order(auth_client, "OMS-A-2")

        response = await auth_client.get("/api/v1/analytics/summary")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["total_orders"]["current"] == "2"
        assert data["total_revenue"]["current"] == "1298.00"
        # No orders in the prior period -> undefined %, not a crash.
        assert data["total_orders"]["change_pct"] is None


async def test_analytics_summary_reports_pending_orders_and_payment_type_values(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """Regression coverage for the dashboard's "Order Breakdown" drill-down
    (Total Orders -> COD/Prepaid/Pending/Fulfilled/Unfulfilled counts +
    values): `pending_orders` must reflect `Order.status == PENDING` (the
    OMS workflow status every new order starts in — NOT
    `Order.payment_status`, a different enum that happens to share the
    string "pending"), and `cod_value`/`prepaid_value` must be the summed
    order value per payment type, not just a count.
    """
    async with await make_authenticated_client(
        db_session, permission_codes=[*_ANALYTICS_PERMS, "orders.update"]
    ) as auth_client:
        # Stays PENDING (the default on creation).
        await _create_order(auth_client, "OMS-PEND-A")

        # Explicitly moved off PENDING -> must not be counted as pending.
        confirmed = await auth_client.post(
            "/api/v1/orders",
            json={
                "order_number": "OMS-CONF-A",
                "payment_type": "cod",
                "shipping_charge": "0",
                "items": [
                    {
                        "sku": "SKU-1",
                        "product_name": "Ashwagandha 60ct",
                        "quantity": 1,
                        "unit_price": "500.00",
                    }
                ],
            },
        )
        confirmed_id = confirmed.json()["data"]["id"]
        await auth_client.patch(f"/api/v1/orders/{confirmed_id}", json={"status": "confirmed"})

        response = await auth_client.get("/api/v1/analytics/summary")
        assert response.status_code == 200
        data = response.json()["data"]

        # Two orders total: OMS-PEND-A (prepaid, still pending) and
        # OMS-CONF-A (cod, confirmed) -> only one is still PENDING.
        assert data["pending_orders"]["current"] == "1"
        assert data["cod_orders"]["current"] == "1"
        assert data["prepaid_orders"]["current"] == "1"
        assert data["cod_value"]["current"] == "500.00"
        assert data["prepaid_value"]["current"] == "649.00"


async def test_analytics_breakdowns_use_real_enum_values(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=_ANALYTICS_PERMS
    ) as auth_client:
        await _create_order(auth_client, "OMS-B-1")

        response = await auth_client.get("/api/v1/analytics/breakdowns")

        assert response.status_code == 200
        data = response.json()["data"]
        order_statuses = {row["status"] for row in data["order_status"]}
        assert order_statuses <= {
            "pending",
            "confirmed",
            "processing",
            "packed",
            "shipped",
            "delivered",
            "cancelled",
        }


async def test_analytics_top_products_and_recent_activity_smoke(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=_ANALYTICS_PERMS
    ) as auth_client:
        await _create_order(auth_client, "OMS-C-1")

        top_products = await auth_client.get("/api/v1/analytics/top-products")
        assert top_products.status_code == 200
        products = top_products.json()["data"]
        assert products[0]["sku"] == "SKU-1"
        assert products[0]["units_sold"] == 1

        recent = await auth_client.get("/api/v1/analytics/recent-activity")
        assert recent.status_code == 200
        assert len(recent.json()["data"]["recent_orders"]) == 1

        timeseries = await auth_client.get(
            "/api/v1/analytics/orders-timeseries", params={"interval": "day"}
        )
        assert timeseries.status_code == 200
        assert sum(p["order_count"] for p in timeseries.json()["data"]["points"]) == 1

        couriers = await auth_client.get("/api/v1/analytics/couriers")
        assert couriers.status_code == 200
        assert couriers.json()["data"] == []


async def test_orders_timeseries_buckets_by_ist_calendar_day_not_utc(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """Regression test: an order placed at 1 AM IST on 15 March is stored
    as 2026-03-14T19:30:00Z (still 14 March in UTC). Bucketing by the raw
    UTC date used to put it in a "2026-03-14" bucket — wrong, since the
    OMS's business calendar day is IST (spec: "this OMS operates in
    India"). It must appear under "2026-03-15", matching every other
    date-scoped feature (dashboard KPIs, Orders page filters) which are
    all computed against IST midnight boundaries by the frontend and
    filtered as such by the backend's plain `order_datetime` range checks.
    """
    async with await make_authenticated_client(
        db_session, permission_codes=_ANALYTICS_PERMS
    ) as auth_client:
        payload = {
            "order_number": "OMS-IST-BOUNDARY",
            "payment_type": "prepaid",
            "shipping_charge": "0",
            "order_datetime": "2026-03-14T19:30:00Z",  # == 2026-03-15T01:00:00+05:30
            "items": [
                {
                    "sku": "SKU-1",
                    "product_name": "Ashwagandha 60ct",
                    "quantity": 1,
                    "unit_price": "649.00",
                }
            ],
        }
        created = await auth_client.post("/api/v1/orders", json=payload)
        assert created.status_code == 201

        response = await auth_client.get(
            "/api/v1/analytics/orders-timeseries",
            params={
                "date_from": "2026-03-14T00:00:00Z",
                "date_to": "2026-03-16T00:00:00Z",
                "interval": "day",
            },
        )

        assert response.status_code == 200
        points = {p["bucket"]: p["order_count"] for p in response.json()["data"]["points"]}
        assert points.get("2026-03-15") == 1
        assert "2026-03-14" not in points


async def test_orders_timeseries_hour_interval_buckets_by_ist_hour(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """The dashboard's Today-vs-Yesterday comparison chart needs an hourly
    bucket (day/week/month buckets are useless for a single calendar day).
    An order at 2026-03-15T01:00:00+05:30 (stored as 2026-03-14T19:30:00Z)
    must land in the "2026-03-15T01:00" IST-hour bucket, not a UTC one.
    """
    async with await make_authenticated_client(
        db_session, permission_codes=_ANALYTICS_PERMS
    ) as auth_client:
        payload = {
            "order_number": "OMS-IST-HOUR",
            "payment_type": "prepaid",
            "shipping_charge": "0",
            "order_datetime": "2026-03-14T19:30:00Z",  # == 2026-03-15T01:00:00+05:30
            "items": [
                {
                    "sku": "SKU-1",
                    "product_name": "Ashwagandha 60ct",
                    "quantity": 1,
                    "unit_price": "649.00",
                }
            ],
        }
        created = await auth_client.post("/api/v1/orders", json=payload)
        assert created.status_code == 201

        response = await auth_client.get(
            "/api/v1/analytics/orders-timeseries",
            params={
                "date_from": "2026-03-14T00:00:00Z",
                "date_to": "2026-03-16T00:00:00Z",
                "interval": "hour",
            },
        )

        assert response.status_code == 200
        points = {p["bucket"]: p["order_count"] for p in response.json()["data"]["points"]}
        assert points.get("2026-03-15T01:00") == 1


# --- Revenue/order drill-down analytics (Total Revenue/Total Orders ->
# COD/Prepaid -> Paid/Pending). Field semantics confirmed against the real
# schema: Order.total_amount (revenue), Order.payment_type (cod/prepaid
# split), Order.payment_status (paid vs everything-else split) -- see
# AnalyticsService's module docstring for the exact rule. Uses direct
# service-level order creation (not the HTTP orders API) because
# payment_status isn't settable through that API -- it's only ever set by
# the Shopify sync/payment flows -- so it's set directly via the
# repository here, the same way test_shiprocket_sync.py's helpers build
# fixture state the HTTP layer doesn't expose. ---------------------------


async def _make_order(
    session: AsyncSession,
    *,
    order_number: str,
    payment_type: PaymentType,
    payment_status: PaymentStatus,
    amount: str,
    order_datetime: datetime | None = None,
    status: OrderStatus = OrderStatus.CONFIRMED,
):
    order = await OrderService(session).create_order(
        actor=None,
        order_number=order_number,
        customer_id=None,
        order_datetime=order_datetime or datetime.now(UTC),
        currency="INR",
        payment_type=payment_type,
        shipping_charge=0,
        notes=None,
        items=[
            OrderItemCreateRequest(
                sku="SKU-1", product_name="Ashwagandha 60ct", quantity=1, unit_price=amount
            )
        ],
    )
    await OrderRepository(session).update(
        order, payment_status=payment_status, status=status
    )
    await session.commit()
    return order


async def test_payment_status_breakdown_computes_total_paid_pending_revenue_and_counts(
    db_session: AsyncSession,
) -> None:
    await _make_order(
        db_session,
        order_number="OMS-COD-PAID",
        payment_type=PaymentType.COD,
        payment_status=PaymentStatus.PAID,
        amount="600.00",
    )
    await _make_order(
        db_session,
        order_number="OMS-COD-PENDING",
        payment_type=PaymentType.COD,
        payment_status=PaymentStatus.PENDING,
        amount="400.00",
    )
    await _make_order(
        db_session,
        order_number="OMS-PREPAID-PAID",
        payment_type=PaymentType.PREPAID,
        payment_status=PaymentStatus.PAID,
        amount="1000.00",
    )

    result = await AnalyticsService(db_session).get_payment_status_breakdown(
        None, None, PaymentType.COD
    )

    # 1: total COD revenue = sum of COD orders only, excluding prepaid.
    assert result.total_revenue == 1000  # 600 + 400, NOT the 1000 prepaid order
    assert result.total_count == 2
    # 2: paid COD = only the PAID-status COD order.
    assert result.paid_revenue == 600
    assert result.paid_count == 1
    # 3: pending COD = every non-PAID status (here just PENDING).
    assert result.pending_revenue == 400
    assert result.pending_count == 1


async def test_payment_status_breakdown_for_prepaid_excludes_cod(
    db_session: AsyncSession,
) -> None:
    await _make_order(
        db_session,
        order_number="OMS-PP-PAID",
        payment_type=PaymentType.PREPAID,
        payment_status=PaymentStatus.PAID,
        amount="900.00",
    )
    await _make_order(
        db_session,
        order_number="OMS-PP-PENDING",
        payment_type=PaymentType.PREPAID,
        payment_status=PaymentStatus.FAILED,
        amount="100.00",
    )
    await _make_order(
        db_session,
        order_number="OMS-COD-UNRELATED",
        payment_type=PaymentType.COD,
        payment_status=PaymentStatus.PAID,
        amount="5000.00",
    )

    result = await AnalyticsService(db_session).get_payment_status_breakdown(
        None, None, PaymentType.PREPAID
    )

    assert result.total_revenue == 1000  # 900 + 100, excludes the 5000 COD order
    assert result.paid_revenue == 900
    # FAILED (not PENDING) still counts as "pending" -- any non-PAID status
    # does, per the documented rule ("paid" vs "everything else").
    assert result.pending_revenue == 100
    assert result.pending_count == 1


async def test_payment_status_breakdown_without_payment_type_covers_all_orders(
    db_session: AsyncSession,
) -> None:
    await _make_order(
        db_session,
        order_number="OMS-ALL-COD",
        payment_type=PaymentType.COD,
        payment_status=PaymentStatus.PAID,
        amount="300.00",
    )
    await _make_order(
        db_session,
        order_number="OMS-ALL-PREPAID",
        payment_type=PaymentType.PREPAID,
        payment_status=PaymentStatus.PENDING,
        amount="700.00",
    )

    result = await AnalyticsService(db_session).get_payment_status_breakdown(None, None, None)

    assert result.total_revenue == 1000
    assert result.total_count == 2
    assert result.payment_type is None


async def test_payment_status_breakdown_respects_date_filtering(
    db_session: AsyncSession,
) -> None:
    await _make_order(
        db_session,
        order_number="OMS-OLD",
        payment_type=PaymentType.COD,
        payment_status=PaymentStatus.PAID,
        amount="500.00",
        order_datetime=datetime(2020, 1, 1, tzinfo=UTC),
    )
    await _make_order(
        db_session,
        order_number="OMS-RECENT",
        payment_type=PaymentType.COD,
        payment_status=PaymentStatus.PAID,
        amount="700.00",
    )

    result = await AnalyticsService(db_session).get_payment_status_breakdown(
        datetime(2025, 1, 1, tzinfo=UTC), datetime.now(UTC), PaymentType.COD
    )

    # 4: the 2020 order must be excluded by the date range.
    assert result.total_revenue == 700
    assert result.total_count == 1


async def test_payment_status_breakdown_includes_cancelled_orders(
    db_session: AsyncSession,
) -> None:
    """Cancelled orders are included -- matches the existing, unchanged
    behavior of total_revenue/cod_value/prepaid_value in get_summary,
    which has never excluded them. Not a new rule invented for this
    drill-down.
    """
    await _make_order(
        db_session,
        order_number="OMS-CANCELLED",
        payment_type=PaymentType.COD,
        payment_status=PaymentStatus.PAID,
        amount="250.00",
        status=OrderStatus.CANCELLED,
    )

    result = await AnalyticsService(db_session).get_payment_status_breakdown(
        None, None, PaymentType.COD
    )

    assert result.total_revenue == 250
    assert result.total_count == 1


async def test_revenue_timeseries_splits_cod_and_prepaid_per_bucket(
    db_session: AsyncSession,
) -> None:
    day = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
    await _make_order(
        db_session,
        order_number="OMS-TS-COD",
        payment_type=PaymentType.COD,
        payment_status=PaymentStatus.PAID,
        amount="200.00",
        order_datetime=day,
    )
    await _make_order(
        db_session,
        order_number="OMS-TS-PREPAID",
        payment_type=PaymentType.PREPAID,
        payment_status=PaymentStatus.PAID,
        amount="300.00",
        order_datetime=day,
    )

    result = await AnalyticsService(db_session).get_revenue_timeseries(
        datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 30, tzinfo=UTC), "day"
    )

    assert len(result.points) == 1
    point = result.points[0]
    assert point.cod_orders == 1
    assert point.cod_revenue == 200
    assert point.prepaid_orders == 1
    assert point.prepaid_revenue == 300
    # 5: cards and charts must use the same dataset -- total here must
    # equal cod + prepaid, matching get_summary's total_revenue for the
    # same range (no double-counting).
    assert point.total_orders == 2
    assert point.total_revenue == 500


async def test_payment_status_timeseries_splits_paid_and_pending_per_bucket(
    db_session: AsyncSession,
) -> None:
    day = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
    await _make_order(
        db_session,
        order_number="OMS-PST-PAID",
        payment_type=PaymentType.COD,
        payment_status=PaymentStatus.PAID,
        amount="150.00",
        order_datetime=day,
    )
    await _make_order(
        db_session,
        order_number="OMS-PST-PENDING",
        payment_type=PaymentType.COD,
        payment_status=PaymentStatus.PENDING,
        amount="50.00",
        order_datetime=day,
    )
    # A prepaid order on the same day must never leak into a COD-scoped
    # timeseries.
    await _make_order(
        db_session,
        order_number="OMS-PST-PREPAID",
        payment_type=PaymentType.PREPAID,
        payment_status=PaymentStatus.PAID,
        amount="9999.00",
        order_datetime=day,
    )

    result = await AnalyticsService(db_session).get_payment_status_timeseries(
        datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 30, tzinfo=UTC), "day", PaymentType.COD
    )

    assert result.payment_type == "cod"
    assert len(result.points) == 1
    point = result.points[0]
    assert point.paid_orders == 1
    assert point.paid_revenue == 150
    assert point.pending_orders == 1
    assert point.pending_revenue == 50
    assert point.total_revenue == 200  # excludes the 9999 prepaid order


async def test_payment_status_breakdown_endpoint_combined_filters(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """6: date range + payment_type combined via the real HTTP endpoint,
    proving the route wiring (not just the service method) works.
    """
    await _make_order(
        db_session,
        order_number="OMS-HTTP-COD",
        payment_type=PaymentType.COD,
        payment_status=PaymentStatus.PAID,
        amount="800.00",
    )
    await _make_order(
        db_session,
        order_number="OMS-HTTP-PREPAID",
        payment_type=PaymentType.PREPAID,
        payment_status=PaymentStatus.PAID,
        amount="1200.00",
    )

    async with await make_authenticated_client(
        db_session, permission_codes=_ANALYTICS_PERMS
    ) as auth_client:
        response = await auth_client.get(
            "/api/v1/analytics/payment-status-breakdown", params={"payment_type": "cod"}
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["total_revenue"] == "800.00"
        assert data["paid_revenue"] == "800.00"
        assert data["payment_type"] == "cod"

        revenue_ts = await auth_client.get(
            "/api/v1/analytics/revenue-timeseries", params={"interval": "day"}
        )
        assert revenue_ts.status_code == 200

        status_ts = await auth_client.get(
            "/api/v1/analytics/payment-status-timeseries",
            params={"interval": "day", "payment_type": "prepaid"},
        )
        assert status_ts.status_code == 200
        assert status_ts.json()["data"]["payment_type"] == "prepaid"


# --- Dashboard Returns/Refunds cards + Courier Performance's in-transit/
# pending breakdown. Direct ORM creation (not the HTTP API) for
# Return/Refund/Shipment/Courier rows, same reasoning as `_make_order`
# above: only the exact status/amount combination matters here, not the
# workflow that produced it. ---------------------------------------------


async def test_returns_refunds_summary_reports_pending_and_completed_buckets(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    order = await OrderService(db_session).create_order(
        actor=None,
        order_number="OMS-RR-1",
        customer_id=None,
        order_datetime=datetime.now(UTC),
        currency="INR",
        payment_type=PaymentType.PREPAID,
        shipping_charge=0,
        notes=None,
        items=[
            OrderItemCreateRequest(
                sku="SKU-1", product_name="Ashwagandha 60ct", quantity=1, unit_price="500.00"
            )
        ],
    )

    db_session.add_all(
        [
            Return(order_id=order.id, status=ReturnStatus.REQUESTED, source_system="manual"),
            Return(order_id=order.id, status=ReturnStatus.COMPLETED, source_system="manual"),
            Refund(
                order_id=order.id,
                amount=Decimal("500.00"),
                status=RefundStatus.COMPLETED,
                source_system="manual",
            ),
            Refund(
                order_id=order.id,
                amount=Decimal("250.00"),
                status=RefundStatus.PENDING,
                source_system="manual",
            ),
        ]
    )
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=_ANALYTICS_PERMS
    ) as auth_client:
        response = await auth_client.get("/api/v1/analytics/returns-refunds")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["returns"]["total_returns"] == 2
        assert data["returns"]["completed_returns"] == 1
        assert data["returns"]["pending_returns"] == 1
        assert data["returns"]["return_rate_pct"] == pytest.approx(200.0)
        assert data["refunds"]["total_refunds"] == 2
        assert data["refunds"]["completed_refunds"] == 1
        assert data["refunds"]["pending_refunds"] == 1
        assert data["refunds"]["total_refund_amount"] == "500.00"


async def test_courier_performance_reports_in_transit_and_pending_counts(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    order = await OrderService(db_session).create_order(
        actor=None,
        order_number="OMS-CP-1",
        customer_id=None,
        order_datetime=datetime.now(UTC),
        currency="INR",
        payment_type=PaymentType.PREPAID,
        shipping_charge=0,
        notes=None,
        items=[
            OrderItemCreateRequest(
                sku="SKU-1", product_name="Ashwagandha 60ct", quantity=1, unit_price="500.00"
            )
        ],
    )
    courier = Courier(name="Test Courier", code="test-courier")
    db_session.add(courier)
    await db_session.flush()
    db_session.add_all(
        [
            Shipment(
                order_id=order.id,
                courier_id=courier.id,
                current_status=ShipmentStatus.IN_TRANSIT,
                source_system="manual",
            ),
            Shipment(
                order_id=order.id,
                courier_id=courier.id,
                current_status=ShipmentStatus.PENDING,
                source_system="manual",
            ),
            Shipment(
                order_id=order.id,
                courier_id=courier.id,
                current_status=ShipmentStatus.DELIVERED,
                source_system="manual",
            ),
        ]
    )
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=_ANALYTICS_PERMS
    ) as auth_client:
        response = await auth_client.get("/api/v1/analytics/couriers")

        assert response.status_code == 200
        courier_data = response.json()["data"][0]
        assert courier_data["shipment_count"] == 3
        assert courier_data["delivered_count"] == 1
        assert courier_data["in_transit_count"] == 1
        assert courier_data["pending_count"] == 1


# --- Perf refactor regression coverage (round-trip consolidation) -------
#
# `_summary_counts`, `get_breakdowns`, and `get_returns_refunds_summary`
# were rewritten to combine several previously-separate round trips into
# fewer statements (conditional aggregation / scalar subqueries / one raw
# fetch tallied in Python) — see `analytics_service.py`. Every query
# above already re-passed unchanged, which covers the single-value cases;
# the tests below specifically exercise MULTIPLE distinct values per
# metric/dimension at once, since that's exactly where a consolidation
# bug (wrong condition attached to the wrong subquery/column) would show
# up first.


async def test_analytics_summary_reports_every_consolidated_metric_correctly(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """Direct regression for the new combined `other_row` scalar-subquery
    statement in `_summary_counts` -- covers every field that query now
    computes (`total_products`, `delivered_shipments`, `in_transit_
    shipments`, `out_for_delivery_shipments`, `delayed_shipments`,
    `open_ndr`, `open_rto`, `returns`, `refunds`) in one request, each
    with both a matching and a non-matching row so a wrong WHERE
    condition on any one subquery would be caught.
    """
    order = await OrderService(db_session).create_order(
        actor=None,
        order_number="OMS-SUM-1",
        customer_id=None,
        order_datetime=datetime.now(UTC),
        currency="INR",
        payment_type=PaymentType.PREPAID,
        shipping_charge=Decimal("0"),
        notes=None,
        items=[
            OrderItemCreateRequest(
                sku="SKU-1", product_name="Ashwagandha 60ct", quantity=1, unit_price="500.00"
            )
        ],
    )

    db_session.add_all([Product(title="Product A"), Product(title="Product B")])

    delivered = Shipment(
        order_id=order.id,
        current_status=ShipmentStatus.DELIVERED,
        source_system="manual",
        actual_delivery_date=datetime.now(UTC),
    )
    in_transit = Shipment(
        order_id=order.id,
        current_status=ShipmentStatus.IN_TRANSIT,
        source_system="manual",
    )
    out_for_delivery = Shipment(
        order_id=order.id,
        current_status=ShipmentStatus.OUT_FOR_DELIVERY,
        source_system="manual",
    )
    delayed = Shipment(
        order_id=order.id,
        current_status=ShipmentStatus.PENDING,
        delay_status=ShipmentDelayStatus.DELAYED,
        source_system="manual",
    )
    not_delayed = Shipment(
        order_id=order.id,
        current_status=ShipmentStatus.PENDING,
        delay_status=ShipmentDelayStatus.ON_TIME,
        source_system="manual",
    )
    db_session.add_all([delivered, in_transit, out_for_delivery, delayed, not_delayed])
    await db_session.flush()

    db_session.add_all(
        [
            NDR(
                shipment_id=in_transit.id,
                order_id=order.id,
                status=NDRStatus.OPEN,
                source_system="manual",
            ),
            NDR(
                shipment_id=in_transit.id,
                order_id=order.id,
                status=NDRStatus.RESOLVED,
                source_system="manual",
            ),
            RTO(
                shipment_id=in_transit.id,
                order_id=order.id,
                status=RTOStatus.INITIATED,
                source_system="manual",
            ),
            RTO(
                shipment_id=in_transit.id,
                order_id=order.id,
                status=RTOStatus.RECEIVED,
                source_system="manual",
            ),
            Return(order_id=order.id, status=ReturnStatus.REQUESTED, source_system="manual"),
            Refund(
                order_id=order.id,
                amount=Decimal("100.00"),
                status=RefundStatus.PENDING,
                source_system="manual",
            ),
        ]
    )
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=_ANALYTICS_PERMS
    ) as auth_client:
        response = await auth_client.get("/api/v1/analytics/summary")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["total_products"]["current"] == "2"
        assert data["delivered_shipments"]["current"] == "1"
        assert data["in_transit_shipments"]["current"] == "1"
        assert data["out_for_delivery_shipments"]["current"] == "1"
        assert data["delayed_shipments"]["current"] == "1"
        assert data["open_ndr"]["current"] == "1"
        assert data["open_rto"]["current"] == "1"
        assert data["returns"]["current"] == "1"
        assert data["refunds"]["current"] == "1"


async def test_analytics_breakdowns_tally_multiple_orders_across_every_dimension(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """Regression for the round-trip consolidation in `get_breakdowns`:
    `order_status`/`payment_type`/`payment_status`/`fulfillment_status`
    are now tallied from ONE raw-row fetch in Python instead of 4
    separate `GROUP BY` queries -- must still produce the exact same
    per-status counts across MULTIPLE distinct values in EVERY dimension
    at once (a single-value case can't catch a column mixed up between
    dimensions).
    """
    order_a = await OrderService(db_session).create_order(
        actor=None,
        order_number="OMS-BD-1",
        customer_id=None,
        order_datetime=datetime.now(UTC),
        currency="INR",
        payment_type=PaymentType.COD,
        shipping_charge=Decimal("0"),
        notes=None,
        items=[
            OrderItemCreateRequest(sku="SKU-1", product_name="X", quantity=1, unit_price="100.00")
        ],
    )
    order_b = await OrderService(db_session).create_order(
        actor=None,
        order_number="OMS-BD-2",
        customer_id=None,
        order_datetime=datetime.now(UTC),
        currency="INR",
        payment_type=PaymentType.PREPAID,
        shipping_charge=Decimal("0"),
        notes=None,
        items=[
            OrderItemCreateRequest(sku="SKU-1", product_name="Y", quantity=1, unit_price="200.00")
        ],
    )
    order_a.status = OrderStatus.PENDING
    order_a.payment_status = PaymentStatus.PENDING
    order_a.fulfillment_status = FulfillmentStatus.UNFULFILLED
    order_b.status = OrderStatus.CONFIRMED
    order_b.payment_status = PaymentStatus.PAID
    order_b.fulfillment_status = FulfillmentStatus.FULFILLED
    db_session.add_all(
        [
            Shipment(
                order_id=order_a.id, current_status=ShipmentStatus.PENDING, source_system="manual"
            ),
            Shipment(
                order_id=order_b.id,
                current_status=ShipmentStatus.DELIVERED,
                source_system="manual",
            ),
        ]
    )
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=_ANALYTICS_PERMS
    ) as auth_client:
        response = await auth_client.get("/api/v1/analytics/breakdowns")

        assert response.status_code == 200
        data = response.json()["data"]

        def _counts(rows: list[dict]) -> dict[str, int]:
            return {row["status"]: row["count"] for row in rows}

        assert _counts(data["order_status"]) == {"pending": 1, "confirmed": 1}
        assert _counts(data["payment_type"]) == {"cod": 1, "prepaid": 1}
        assert _counts(data["payment_status"]) == {"pending": 1, "paid": 1}
        assert _counts(data["fulfillment_status"]) == {"unfulfilled": 1, "fulfilled": 1}
        assert _counts(data["shipment_status"]) == {"pending": 1, "delivered": 1}


async def test_recent_activity_returns_correct_field_values_not_just_counts(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """Regression for switching `get_recent_activity` from `select(Order)`/
    `select(Shipment)`/etc. (full ORM entities) to selecting only the
    columns each response field needs -- must still return the exact same
    field values, not just the same row counts.
    """
    order = await OrderService(db_session).create_order(
        actor=None,
        order_number="OMS-RA-1",
        customer_id=None,
        order_datetime=datetime.now(UTC),
        currency="INR",
        payment_type=PaymentType.PREPAID,
        shipping_charge=Decimal("0"),
        notes=None,
        items=[
            OrderItemCreateRequest(
                sku="SKU-1", product_name="Ashwagandha 60ct", quantity=1, unit_price="777.00"
            )
        ],
    )
    shipment = Shipment(
        order_id=order.id,
        current_status=ShipmentStatus.IN_TRANSIT,
        awb="AWB-RA-1",
        source_system="manual",
    )
    db_session.add(shipment)
    await db_session.flush()
    db_session.add_all(
        [
            NDR(
                shipment_id=shipment.id,
                order_id=order.id,
                status=NDRStatus.OPEN,
                reason="Customer unavailable",
                source_system="manual",
            ),
            RTO(
                shipment_id=shipment.id,
                order_id=order.id,
                status=RTOStatus.INITIATED,
                reason="Refused delivery",
                source_system="manual",
            ),
        ]
    )
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=_ANALYTICS_PERMS
    ) as auth_client:
        response = await auth_client.get("/api/v1/analytics/recent-activity")

        assert response.status_code == 200
        data = response.json()["data"]

        recent_order = data["recent_orders"][0]
        assert recent_order["order_number"] == "OMS-RA-1"
        assert recent_order["total_amount"] == "777.00"
        assert recent_order["status"] == "pending"

        recent_shipment = data["recent_shipments"][0]
        assert recent_shipment["order_id"] == str(order.id)
        assert recent_shipment["awb"] == "AWB-RA-1"
        assert recent_shipment["current_status"] == "in_transit"

        kinds = {item["kind"]: item for item in data["recent_ndr_rto"]}
        assert kinds["ndr"]["reason"] == "Customer unavailable"
        assert kinds["ndr"]["status"] == "open"
        assert kinds["rto"]["reason"] == "Refused delivery"
        assert kinds["rto"]["status"] == "initiated"
