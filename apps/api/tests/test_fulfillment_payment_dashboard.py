"""Fulfilled/Unfulfilled amount analytics + COD Orders dashboard.

Covers: Total/COD/Prepaid amount+count+% split by Shopify
`fulfillment_status`, the Module-2-reused shipment-status classification
within one Fulfilled/Unfulfilled x COD/Prepaid slice, real NDR reason
surfacing (Unknown/Unclassified fallback), the COD dashboard's 4 KPIs,
reshipment non-double-counting, drill-down-matches-count consistency, and
zero-value/empty-population cases.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.db.session import get_db
from app.main import app
from app.models.enums import FulfillmentStatus, NDRStatus, OrderStatus, PaymentType, ShipmentStatus
from app.models.ndr import NDR
from app.models.shipment import Shipment
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import bearer_client, make_order, make_role, make_user

pytestmark = pytest.mark.asyncio

_PERMS = ["analytics.read"]


@pytest.fixture(autouse=True)
def _clear_get_db_override():
    yield
    app.dependency_overrides.clear()


async def _make_analytics_reader(db_session: AsyncSession, *, email: str):
    role = await make_role(db_session, name=f"ANALYTICS-{email}", permission_codes=_PERMS)
    return await make_user(db_session, email=email, role=role)


async def _add_shipment(
    db_session: AsyncSession,
    *,
    order,
    current_status: ShipmentStatus,
    created_at: datetime | None = None,
) -> Shipment:
    shipment = Shipment(
        order_id=order.id,
        current_status=current_status,
        source_system="manual",
        **({"created_at": created_at} if created_at is not None else {}),
    )
    db_session.add(shipment)
    await db_session.commit()
    await db_session.refresh(shipment)
    return shipment


# ---------------------------------------------------------------------------
# 1. Fulfilled/Unfulfilled amount breakdown — totals, COD/Prepaid split.
# ---------------------------------------------------------------------------


async def test_fulfillment_payment_breakdown_totals_and_splits(db_session: AsyncSession) -> None:
    user = await _make_analytics_reader(db_session, email="breakdown@example.com")

    await make_order(
        db_session,
        order_number="FB-F-COD",
        fulfillment_status=FulfillmentStatus.FULFILLED,
        payment_type=PaymentType.COD,
        total_amount=Decimal("100"),
    )
    await make_order(
        db_session,
        order_number="FB-F-PRE",
        fulfillment_status=FulfillmentStatus.FULFILLED,
        payment_type=PaymentType.PREPAID,
        total_amount=Decimal("200"),
    )
    await make_order(
        db_session,
        order_number="FB-U-COD",
        fulfillment_status=FulfillmentStatus.UNFULFILLED,
        payment_type=PaymentType.COD,
        total_amount=Decimal("50"),
    )
    await make_order(
        db_session,
        order_number="FB-U-PRE",
        fulfillment_status=FulfillmentStatus.UNFULFILLED,
        payment_type=PaymentType.PREPAID,
        total_amount=Decimal("25"),
    )
    # PARTIAL falls into neither bucket -- counted in the grand total only,
    # same convention `_summary_counts` already uses.
    await make_order(
        db_session,
        order_number="FB-PARTIAL",
        fulfillment_status=FulfillmentStatus.PARTIAL,
        payment_type=PaymentType.COD,
        total_amount=Decimal("10"),
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/analytics/fulfillment-payment-breakdown")
        assert response.status_code == 200
        data = response.json()["data"]

    assert data["total_orders"] == 5
    assert Decimal(data["total_amount"]) == Decimal("385")

    fulfilled = data["fulfilled"]
    assert fulfilled["order_count"] == 2
    assert Decimal(fulfilled["amount"]) == Decimal("300")
    assert fulfilled["percent_of_total_amount"] == pytest.approx(300 / 385 * 100, abs=0.01)
    assert Decimal(fulfilled["cod"]["amount"]) == Decimal("100")
    assert fulfilled["cod"]["percent_of_bucket_amount"] == pytest.approx(100 / 300 * 100, abs=0.01)
    assert Decimal(fulfilled["prepaid"]["amount"]) == Decimal("200")
    assert fulfilled["prepaid"]["percent_of_bucket_amount"] == pytest.approx(
        200 / 300 * 100, abs=0.01
    )

    unfulfilled = data["unfulfilled"]
    assert unfulfilled["order_count"] == 2
    assert Decimal(unfulfilled["amount"]) == Decimal("75")
    assert Decimal(unfulfilled["cod"]["amount"]) == Decimal("50")
    assert Decimal(unfulfilled["prepaid"]["amount"]) == Decimal("25")

    # fulfilled.amount + unfulfilled.amount != total_amount -- the PARTIAL
    # order's ₹10 is in the grand total only. Confirms no double-counting
    # AND confirms PARTIAL is genuinely excluded from both buckets.
    assert Decimal(fulfilled["amount"]) + Decimal(unfulfilled["amount"]) == Decimal("375")


async def test_fulfillment_payment_breakdown_zero_population(db_session: AsyncSession) -> None:
    """No orders at all in range -- every percentage must be 0.0, never a
    ZeroDivisionError or a misleading 100%.
    """
    user = await _make_analytics_reader(db_session, email="zero@example.com")
    future_from = datetime.now(UTC) + timedelta(days=365)
    future_to = future_from + timedelta(days=1)

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(
            "/api/v1/analytics/fulfillment-payment-breakdown",
            params={"date_from": future_from.isoformat(), "date_to": future_to.isoformat()},
        )
        assert response.status_code == 200
        data = response.json()["data"]

    assert data["total_orders"] == 0
    assert data["fulfilled"]["percent_of_total_amount"] == 0.0
    assert data["fulfilled"]["cod"]["percent_of_bucket_amount"] == 0.0


async def test_fulfillment_payment_breakdown_excludes_cancelled_from_unfulfilled(
    db_session: AsyncSession,
) -> None:
    """A cancelled order (large amount, Shopify UNFULFILLED) must not
    inflate the "Unfulfilled" bucket's count/amount -- it was never
    genuinely pending fulfilment. Still counted in the grand total.
    """
    user = await _make_analytics_reader(db_session, email="cancel-excl@example.com")

    await make_order(
        db_session,
        order_number="CANCEL-UNFULFILLED",
        fulfillment_status=FulfillmentStatus.UNFULFILLED,
        payment_type=PaymentType.COD,
        total_amount=Decimal("9999"),
        status=OrderStatus.CANCELLED,
    )
    await make_order(
        db_session,
        order_number="REAL-UNFULFILLED",
        fulfillment_status=FulfillmentStatus.UNFULFILLED,
        payment_type=PaymentType.COD,
        total_amount=Decimal("50"),
        status=OrderStatus.CONFIRMED,
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/analytics/fulfillment-payment-breakdown")
        assert response.status_code == 200
        data = response.json()["data"]

    # Grand total includes the cancelled order's amount -- it's still a
    # real order that existed in the range.
    assert data["total_orders"] == 2
    assert Decimal(data["total_amount"]) == Decimal("10049")

    # "Unfulfilled" excludes it entirely.
    unfulfilled = data["unfulfilled"]
    assert unfulfilled["order_count"] == 1
    assert Decimal(unfulfilled["amount"]) == Decimal("50")
    assert Decimal(unfulfilled["cod"]["amount"]) == Decimal("50")


async def test_cod_dashboard_excludes_cancelled_from_unfulfilled_cod(
    db_session: AsyncSession,
) -> None:
    user = await _make_analytics_reader(db_session, email="cancel-cod@example.com")

    await make_order(
        db_session,
        order_number="CODCANCEL",
        fulfillment_status=FulfillmentStatus.UNFULFILLED,
        payment_type=PaymentType.COD,
        total_amount=Decimal("500"),
        status=OrderStatus.CANCELLED,
    )
    await make_order(
        db_session,
        order_number="CODREAL",
        fulfillment_status=FulfillmentStatus.UNFULFILLED,
        payment_type=PaymentType.COD,
        total_amount=Decimal("40"),
        status=OrderStatus.CONFIRMED,
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/analytics/cod-dashboard")
        assert response.status_code == 200
        data = response.json()["data"]

    # Total COD includes the cancelled order -- it's still a real COD order.
    assert data["total_cod"]["order_count"] == 2
    assert Decimal(data["total_cod"]["amount"]) == Decimal("540")

    # Unfulfilled COD excludes it.
    assert data["unfulfilled_cod"]["order_count"] == 1
    assert Decimal(data["unfulfilled_cod"]["amount"]) == Decimal("40")


# ---------------------------------------------------------------------------
# 2. Classification within a slice — reuses Module 2's unified statuses,
#    sums to ~100% of the slice, and never double-counts a reshipped order.
# ---------------------------------------------------------------------------


async def test_fulfillment_payment_classification_sums_to_slice_amount(
    db_session: AsyncSession,
) -> None:
    user = await _make_analytics_reader(db_session, email="classify@example.com")

    delivered_order = await make_order(
        db_session,
        order_number="CL-DELIVERED",
        fulfillment_status=FulfillmentStatus.FULFILLED,
        payment_type=PaymentType.COD,
        total_amount=Decimal("100"),
        status=OrderStatus.CONFIRMED,
    )
    await _add_shipment(db_session, order=delivered_order, current_status=ShipmentStatus.DELIVERED)

    # Shopify-fulfilled, never routed through this OMS's own shipment
    # pipeline -> `fulfilled_tracking_unavailable`.
    await make_order(
        db_session,
        order_number="CL-TRACKING-UNAVAILABLE",
        fulfillment_status=FulfillmentStatus.FULFILLED,
        payment_type=PaymentType.COD,
        total_amount=Decimal("50"),
        status=OrderStatus.SHIPPED,
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(
            "/api/v1/analytics/fulfillment-payment-classification",
            params={"fulfillment_status": "fulfilled", "payment_type": "cod"},
        )
        assert response.status_code == 200
        data = response.json()["data"]

    assert data["slice_order_count"] == 2
    assert Decimal(data["slice_amount"]) == Decimal("150")
    rows = {row["unified_status"]: row for row in data["classifications"]}
    assert rows["delivered"]["order_count"] == 1
    assert Decimal(rows["delivered"]["amount"]) == Decimal("100")
    assert rows["delivered"]["percent_of_slice_amount"] == pytest.approx(100 / 150 * 100, abs=0.01)
    assert rows["fulfilled_tracking_unavailable"]["order_count"] == 1
    assert Decimal(rows["fulfilled_tracking_unavailable"]["amount"]) == Decimal("50")
    # Every other bucket is present (all 13), explicitly zero.
    assert rows["in_transit"]["order_count"] == 0
    assert Decimal(rows["in_transit"]["amount"]) == Decimal("0")
    non_zero_pct = sum(
        row["percent_of_slice_amount"] for row in data["classifications"] if row["order_count"]
    )
    assert non_zero_pct == pytest.approx(100.0, abs=0.01)


async def test_fulfillment_payment_classification_cancelled_visible_but_excluded_from_slice(
    db_session: AsyncSession,
) -> None:
    """The "cancelled" row stays visible (real, countable record) but its
    amount is excluded from `slice_amount`/`slice_count`, keeping this
    endpoint's denominator in agreement with `get_fulfillment_payment_
    breakdown`'s own cancelled-order exclusion from "unfulfilled" for the
    identical slice.
    """
    user = await _make_analytics_reader(db_session, email="classify-cancel@example.com")

    await make_order(
        db_session,
        order_number="CLC-CANCELLED",
        fulfillment_status=FulfillmentStatus.UNFULFILLED,
        payment_type=PaymentType.COD,
        total_amount=Decimal("500"),
        status=OrderStatus.CANCELLED,
    )
    await make_order(
        db_session,
        order_number="CLC-PLACED",
        fulfillment_status=FulfillmentStatus.UNFULFILLED,
        payment_type=PaymentType.COD,
        total_amount=Decimal("80"),
        status=OrderStatus.PENDING,
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(
            "/api/v1/analytics/fulfillment-payment-classification",
            params={"fulfillment_status": "unfulfilled", "payment_type": "cod"},
        )
        breakdown_response = await client.get("/api/v1/analytics/fulfillment-payment-breakdown")
        assert response.status_code == 200
        data = response.json()["data"]
        breakdown = breakdown_response.json()["data"]

    rows = {row["unified_status"]: row for row in data["classifications"]}
    # The cancelled row is still present and shows the real ₹500.
    assert rows["cancelled"]["order_count"] == 1
    assert Decimal(rows["cancelled"]["amount"]) == Decimal("500")

    # But the slice denominator excludes it -- matches the breakdown
    # endpoint's "unfulfilled" bucket for the same slice exactly.
    assert data["slice_order_count"] == 1
    assert Decimal(data["slice_amount"]) == Decimal("80")
    assert data["slice_order_count"] == breakdown["unfulfilled"]["order_count"]
    assert Decimal(data["slice_amount"]) == Decimal(breakdown["unfulfilled"]["amount"])


async def test_fulfillment_payment_classification_reshipment_not_double_counted(
    db_session: AsyncSession,
) -> None:
    """An order with 2 Shipment rows (a cancelled booking, then a real
    reshipment) must contribute its ₹300 exactly once -- the
    most-recent-shipment join, same rule as Modules 2/3/4.
    """
    user = await _make_analytics_reader(db_session, email="reship@example.com")

    order = await make_order(
        db_session,
        order_number="RESHIP-1",
        fulfillment_status=FulfillmentStatus.FULFILLED,
        payment_type=PaymentType.COD,
        total_amount=Decimal("300"),
        status=OrderStatus.CONFIRMED,
    )
    earlier = datetime.now(UTC) - timedelta(days=2)
    later = datetime.now(UTC) - timedelta(days=1)
    await _add_shipment(
        db_session, order=order, current_status=ShipmentStatus.CANCELLED, created_at=earlier
    )
    await _add_shipment(
        db_session, order=order, current_status=ShipmentStatus.DELIVERED, created_at=later
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(
            "/api/v1/analytics/fulfillment-payment-classification",
            params={"fulfillment_status": "fulfilled", "payment_type": "cod"},
        )
        assert response.status_code == 200
        data = response.json()["data"]

    assert data["slice_order_count"] == 1
    assert Decimal(data["slice_amount"]) == Decimal("300")
    rows = {row["unified_status"]: row for row in data["classifications"]}
    assert rows["delivered"]["order_count"] == 1
    assert Decimal(rows["delivered"]["amount"]) == Decimal("300")
    assert rows["cancelled"]["order_count"] == 0


# ---------------------------------------------------------------------------
# 3. NDR reasons — real text or Unknown/Unclassified, never invented.
# ---------------------------------------------------------------------------


async def test_ndr_reason_breakdown_real_reason_and_unknown_fallback(
    db_session: AsyncSession,
) -> None:
    user = await _make_analytics_reader(db_session, email="ndr@example.com")

    with_reason = await make_order(
        db_session,
        order_number="NDR-REASON",
        fulfillment_status=FulfillmentStatus.FULFILLED,
        payment_type=PaymentType.COD,
        total_amount=Decimal("100"),
        status=OrderStatus.CONFIRMED,
    )
    shipment_a = await _add_shipment(
        db_session, order=with_reason, current_status=ShipmentStatus.NDR
    )
    db_session.add(
        NDR(
            shipment_id=shipment_a.id,
            order_id=with_reason.id,
            reason="Customer unreachable",
            normalized_reason="customer_unreachable",
            status=NDRStatus.OPEN,
        )
    )

    no_reason = await make_order(
        db_session,
        order_number="NDR-NO-REASON",
        fulfillment_status=FulfillmentStatus.FULFILLED,
        payment_type=PaymentType.COD,
        total_amount=Decimal("40"),
        status=OrderStatus.CONFIRMED,
    )
    shipment_b = await _add_shipment(db_session, order=no_reason, current_status=ShipmentStatus.NDR)
    db_session.add(
        NDR(shipment_id=shipment_b.id, order_id=no_reason.id, status=NDRStatus.OPEN)
    )
    await db_session.commit()

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(
            "/api/v1/analytics/fulfillment-payment-ndr-reasons",
            params={"fulfillment_status": "fulfilled", "payment_type": "cod"},
        )
        assert response.status_code == 200
        data = response.json()["data"]

    assert data["ndr_order_count"] == 2
    assert Decimal(data["ndr_amount"]) == Decimal("140")
    reasons = {row["reason"]: row for row in data["reasons"]}
    assert reasons["customer_unreachable"]["order_count"] == 1
    assert Decimal(reasons["customer_unreachable"]["amount"]) == Decimal("100")
    assert reasons["Unknown/Unclassified"]["order_count"] == 1
    assert Decimal(reasons["Unknown/Unclassified"]["amount"]) == Decimal("40")
    total_pct = sum(row["percent_of_ndr_amount"] for row in data["reasons"])
    assert total_pct == pytest.approx(100.0, abs=0.01)


async def test_ndr_reason_breakdown_no_ndr_orders_is_empty_not_error(
    db_session: AsyncSession,
) -> None:
    user = await _make_analytics_reader(db_session, email="ndr-empty@example.com")

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/analytics/fulfillment-payment-ndr-reasons")
        assert response.status_code == 200
        data = response.json()["data"]

    assert data["ndr_order_count"] == 0
    assert data["reasons"] == []


# ---------------------------------------------------------------------------
# 4. COD Orders dashboard — 4 KPIs, two different axes (Shopify fulfillment
#    vs Module 2 shipment status), neither forced to sum with the other.
# ---------------------------------------------------------------------------


async def test_cod_dashboard_four_kpis(db_session: AsyncSession) -> None:
    user = await _make_analytics_reader(db_session, email="cod-dash@example.com")

    delivered_cod = await make_order(
        db_session,
        order_number="CODD-DELIVERED",
        fulfillment_status=FulfillmentStatus.FULFILLED,
        payment_type=PaymentType.COD,
        total_amount=Decimal("100"),
        status=OrderStatus.CONFIRMED,
    )
    await _add_shipment(db_session, order=delivered_cod, current_status=ShipmentStatus.DELIVERED)

    await make_order(
        db_session,
        order_number="CODD-UNFULFILLED",
        fulfillment_status=FulfillmentStatus.UNFULFILLED,
        payment_type=PaymentType.COD,
        total_amount=Decimal("60"),
    )
    # A Prepaid order must never leak into any COD figure.
    await make_order(
        db_session,
        order_number="CODD-PREPAID",
        fulfillment_status=FulfillmentStatus.FULFILLED,
        payment_type=PaymentType.PREPAID,
        total_amount=Decimal("500"),
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/analytics/cod-dashboard")
        assert response.status_code == 200
        data = response.json()["data"]

    assert data["total_cod"]["order_count"] == 2
    assert Decimal(data["total_cod"]["amount"]) == Decimal("160")
    assert data["total_cod"]["percent_of_total_cod_amount"] == pytest.approx(100.0, abs=0.01)

    assert data["fulfilled_cod"]["order_count"] == 1
    assert Decimal(data["fulfilled_cod"]["amount"]) == Decimal("100")
    assert data["fulfilled_cod"]["percent_of_total_cod_amount"] == pytest.approx(
        100 / 160 * 100, abs=0.01
    )

    assert data["unfulfilled_cod"]["order_count"] == 1
    assert Decimal(data["unfulfilled_cod"]["amount"]) == Decimal("60")

    assert data["delivered_cod"]["order_count"] == 1
    assert Decimal(data["delivered_cod"]["amount"]) == Decimal("100")


async def test_cod_dashboard_zero_cod_population(db_session: AsyncSession) -> None:
    user = await _make_analytics_reader(db_session, email="cod-zero@example.com")
    await make_order(
        db_session,
        order_number="NOCOD",
        payment_type=PaymentType.PREPAID,
        total_amount=Decimal("500"),
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/analytics/cod-dashboard")
        assert response.status_code == 200
        data = response.json()["data"]

    assert data["total_cod"]["order_count"] == 0
    assert data["total_cod"]["percent_of_total_cod_amount"] == 0.0
    assert data["delivered_cod"]["order_count"] == 0


# ---------------------------------------------------------------------------
# 5. Drill-down matches the count/amount shown on the KPI/classification row.
# ---------------------------------------------------------------------------


async def test_fulfillment_payment_orders_drilldown_matches_classification_count(
    db_session: AsyncSession,
) -> None:
    user = await _make_analytics_reader(db_session, email="drill@example.com")

    for i in range(3):
        order = await make_order(
            db_session,
            order_number=f"DRILL-DELIVERED-{i}",
            fulfillment_status=FulfillmentStatus.FULFILLED,
            payment_type=PaymentType.COD,
            total_amount=Decimal("10"),
            status=OrderStatus.CONFIRMED,
        )
        await _add_shipment(db_session, order=order, current_status=ShipmentStatus.DELIVERED)
    # A Prepaid delivered order must not appear in this COD-scoped drilldown.
    other = await make_order(
        db_session,
        order_number="DRILL-OTHER-PAYMENT",
        fulfillment_status=FulfillmentStatus.FULFILLED,
        payment_type=PaymentType.PREPAID,
        total_amount=Decimal("10"),
        status=OrderStatus.CONFIRMED,
    )
    await _add_shipment(db_session, order=other, current_status=ShipmentStatus.DELIVERED)

    async with bearer_client(app, get_db, db_session, user.id) as client:
        classification = await client.get(
            "/api/v1/analytics/fulfillment-payment-classification",
            params={"fulfillment_status": "fulfilled", "payment_type": "cod"},
        )
        delivered_count = {
            row["unified_status"]: row["order_count"]
            for row in classification.json()["data"]["classifications"]
        }["delivered"]

        drilldown = await client.get(
            "/api/v1/analytics/fulfillment-payment-orders",
            params={
                "fulfillment_status": "fulfilled",
                "payment_type": "cod",
                "unified_status": "delivered",
            },
        )
        assert drilldown.status_code == 200
        payload = drilldown.json()

    assert delivered_count == 3
    assert payload["meta"]["total_items"] == delivered_count
    assert len(payload["data"]) == delivered_count
    assert all(row["payment_type"] == "cod" for row in payload["data"])
    assert all(row["unified_status"] == "delivered" for row in payload["data"])


async def test_cod_dashboard_total_cod_drilldown_matches_kpi_count(
    db_session: AsyncSession,
) -> None:
    user = await _make_analytics_reader(db_session, email="cod-drill@example.com")
    for i in range(2):
        await make_order(
            db_session,
            order_number=f"CODDRILL-{i}",
            payment_type=PaymentType.COD,
            total_amount=Decimal("10"),
        )
    await make_order(db_session, order_number="CODDRILL-PREPAID", payment_type=PaymentType.PREPAID)

    async with bearer_client(app, get_db, db_session, user.id) as client:
        dashboard = await client.get("/api/v1/analytics/cod-dashboard")
        total_cod_count = dashboard.json()["data"]["total_cod"]["order_count"]

        drilldown = await client.get(
            "/api/v1/analytics/fulfillment-payment-orders", params={"payment_type": "cod"}
        )
        assert drilldown.status_code == 200
        payload = drilldown.json()

    assert total_cod_count == 2
    assert payload["meta"]["total_items"] == 2
    assert all(row["payment_type"] == "cod" for row in payload["data"])


# ---------------------------------------------------------------------------
# 6. Other/Unclassified payment bucket + the cancelled+fulfilled off-by-one
#    fix (production discrepancy: Fulfilled amount/count didn't equal
#    cod+prepaid sub-figures; fulfilled+unfulfilled+cancelled exceeded
#    total_orders by one).
# ---------------------------------------------------------------------------


async def test_fulfillment_payment_breakdown_other_payment_bucket(
    db_session: AsyncSession,
) -> None:
    """`PaymentType.OTHER` orders must show up as their own `other`
    sub-bucket -- not silently absent, the exact root cause of a
    production gap between a bucket's headline amount/count and its
    cod+prepaid sub-figures.
    """
    user = await _make_analytics_reader(db_session, email="other-bucket@example.com")

    await make_order(
        db_session,
        order_number="OTHER-F-COD",
        fulfillment_status=FulfillmentStatus.FULFILLED,
        payment_type=PaymentType.COD,
        total_amount=Decimal("100"),
    )
    await make_order(
        db_session,
        order_number="OTHER-F-OTHER",
        fulfillment_status=FulfillmentStatus.FULFILLED,
        payment_type=PaymentType.OTHER,
        total_amount=Decimal("40"),
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/analytics/fulfillment-payment-breakdown")
        assert response.status_code == 200
        data = response.json()["data"]

    fulfilled = data["fulfilled"]
    assert fulfilled["order_count"] == 2
    assert Decimal(fulfilled["amount"]) == Decimal("140")
    assert fulfilled["other"]["order_count"] == 1
    assert Decimal(fulfilled["other"]["amount"]) == Decimal("40")
    assert fulfilled["other"]["percent_of_bucket_amount"] == pytest.approx(40 / 140 * 100, abs=0.01)

    # cod + prepaid + other must sum to the bucket's own amount exactly --
    # this is the reconciliation the production report found broken.
    reconciled = (
        Decimal(fulfilled["cod"]["amount"])
        + Decimal(fulfilled["prepaid"]["amount"])
        + Decimal(fulfilled["other"]["amount"])
    )
    assert reconciled == Decimal(fulfilled["amount"])


async def test_fulfillment_payment_breakdown_excludes_cancelled_even_if_fulfilled(
    db_session: AsyncSession,
) -> None:
    """An order can be `status=CANCELLED` AND `fulfillment_status=
    FULFILLED` simultaneously (Shopify can report a shipped-then-
    cancelled order this way; nothing in the schema prevents it). Such an
    order must count toward neither "Fulfilled" nor "Unfulfilled" --
    without this, it was double-counted (once into Fulfilled, once into
    a separate Cancelled total elsewhere) while `total_orders` counted it
    once, producing a `fulfilled + unfulfilled + cancelled` sum one
    higher than `total_orders` on production.
    """
    user = await _make_analytics_reader(db_session, email="cancel-fulfilled@example.com")

    await make_order(
        db_session,
        order_number="CANCELLED-BUT-FULFILLED",
        fulfillment_status=FulfillmentStatus.FULFILLED,
        payment_type=PaymentType.COD,
        total_amount=Decimal("9999"),
        status=OrderStatus.CANCELLED,
    )
    await make_order(
        db_session,
        order_number="REAL-FULFILLED",
        fulfillment_status=FulfillmentStatus.FULFILLED,
        payment_type=PaymentType.COD,
        total_amount=Decimal("50"),
        status=OrderStatus.CONFIRMED,
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/analytics/fulfillment-payment-breakdown")
        assert response.status_code == 200
        data = response.json()["data"]

    assert data["total_orders"] == 2
    assert Decimal(data["total_amount"]) == Decimal("10049")

    fulfilled = data["fulfilled"]
    assert fulfilled["order_count"] == 1
    assert Decimal(fulfilled["amount"]) == Decimal("50")

    unfulfilled = data["unfulfilled"]
    assert unfulfilled["order_count"] == 0
    assert Decimal(unfulfilled["amount"]) == Decimal("0")


async def test_fulfillment_payment_classification_and_drilldown_exclude_cancelled_with_shipment(
    db_session: AsyncSession,
) -> None:
    """The narrower, real-world case of the fix above: a cancelled order
    that was fulfilled and ALSO already has an active, non-cancelled
    `Shipment` (shipped then cancelled/refunded, e.g. `current_status ==
    DELIVERED`). `classify_unified_status` checks the shipment's own
    status before the order-level CANCELLED fallback, so this order
    lands in the `delivered` unified-status bucket, not `cancelled` --
    meaning the breakdown's `Order.status`-level exclusion alone doesn't
    stop it from still being counted into the classification endpoint's
    "fulfilled" slice or still appearing in a "fulfilled" + "delivered"
    drilldown. All three (breakdown, classification, drilldown) must
    agree: this order is excluded from the "fulfilled" slice everywhere,
    while a genuinely fulfilled, non-cancelled, delivered order is still
    counted/shown normally.
    """
    user = await _make_analytics_reader(db_session, email="cancel-shipped@example.com")

    cancelled_but_shipped = await make_order(
        db_session,
        order_number="CANCELLED-SHIPPED-DELIVERED",
        fulfillment_status=FulfillmentStatus.FULFILLED,
        payment_type=PaymentType.COD,
        total_amount=Decimal("9999"),
        status=OrderStatus.CANCELLED,
    )
    await _add_shipment(
        db_session, order=cancelled_but_shipped, current_status=ShipmentStatus.DELIVERED
    )

    real_delivered = await make_order(
        db_session,
        order_number="REAL-DELIVERED",
        fulfillment_status=FulfillmentStatus.FULFILLED,
        payment_type=PaymentType.COD,
        total_amount=Decimal("50"),
        status=OrderStatus.CONFIRMED,
    )
    await _add_shipment(db_session, order=real_delivered, current_status=ShipmentStatus.DELIVERED)

    async with bearer_client(app, get_db, db_session, user.id) as client:
        breakdown_resp = await client.get("/api/v1/analytics/fulfillment-payment-breakdown")
        assert breakdown_resp.status_code == 200
        breakdown = breakdown_resp.json()["data"]["fulfilled"]

        classification_resp = await client.get(
            "/api/v1/analytics/fulfillment-payment-classification",
            params={"fulfillment_status": "fulfilled", "payment_type": "cod"},
        )
        assert classification_resp.status_code == 200
        classification = classification_resp.json()["data"]

        drilldown_resp = await client.get(
            "/api/v1/analytics/fulfillment-payment-orders",
            params={
                "fulfillment_status": "fulfilled",
                "payment_type": "cod",
                "unified_status": "delivered",
            },
        )
        assert drilldown_resp.status_code == 200
        drilldown = drilldown_resp.json()

    # Breakdown: only the real delivered order counts toward "fulfilled".
    assert breakdown["order_count"] == 1
    assert Decimal(breakdown["amount"]) == Decimal("50")

    # Classification: the slice total agrees exactly with the breakdown,
    # and the cancelled-but-shipped order never lands in "delivered".
    assert classification["slice_order_count"] == 1
    assert Decimal(classification["slice_amount"]) == Decimal("50")
    delivered_row = next(
        c for c in classification["classifications"] if c["unified_status"] == "delivered"
    )
    assert delivered_row["order_count"] == 1
    assert Decimal(delivered_row["amount"]) == Decimal("50")
    cancelled_row = next(
        c for c in classification["classifications"] if c["unified_status"] == "cancelled"
    )
    assert cancelled_row["order_count"] == 0

    # Drilldown: "fulfilled" + "delivered" returns exactly the real
    # order, never the cancelled-but-shipped one.
    assert drilldown["meta"]["total_items"] == 1
    assert [row["order_number"] for row in drilldown["data"]] == ["REAL-DELIVERED"]


async def test_fulfillment_payment_breakdown_cancelled_excludes_other_payment_orders_too(
    db_session: AsyncSession,
) -> None:
    """The cancelled-exclusion applies regardless of `payment_type` --
    an order that is both `payment_type=OTHER` and `status=CANCELLED`
    (with `fulfillment_status=FULFILLED`) must be excluded from
    "fulfilled" (and therefore from its `other` sub-bucket too), not
    just from the cod/prepaid sub-buckets.
    """
    user = await _make_analytics_reader(db_session, email="cancel-other@example.com")

    await make_order(
        db_session,
        order_number="CANCELLED-OTHER",
        fulfillment_status=FulfillmentStatus.FULFILLED,
        payment_type=PaymentType.OTHER,
        total_amount=Decimal("500"),
        status=OrderStatus.CANCELLED,
    )
    await make_order(
        db_session,
        order_number="REAL-OTHER",
        fulfillment_status=FulfillmentStatus.FULFILLED,
        payment_type=PaymentType.OTHER,
        total_amount=Decimal("30"),
        status=OrderStatus.CONFIRMED,
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/analytics/fulfillment-payment-breakdown")
        assert response.status_code == 200
        fulfilled = response.json()["data"]["fulfilled"]

    assert fulfilled["order_count"] == 1
    assert Decimal(fulfilled["amount"]) == Decimal("30")
    assert fulfilled["other"]["order_count"] == 1
    assert Decimal(fulfilled["other"]["amount"]) == Decimal("30")
