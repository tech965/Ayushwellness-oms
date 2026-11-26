"""CEO-dashboard drilldowns for Modules 2, 3 and 4.

The contract under test: every number on a KPI card / category row / chart
bar / partner row equals the size of the order list it opens, under the same
filters, with no order repeated and no unauthorised access.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.db.session import get_db
from app.main import app
from app.models.enums import OrderStatus, PaymentType, ShipmentStatus
from app.models.shipment import Shipment, ShipmentEvent
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import bearer_client, make_order, make_role, make_user

pytestmark = pytest.mark.asyncio

NOW = datetime.now(UTC).replace(microsecond=0)
BASE = "/api/v1/shipments"


@pytest.fixture(autouse=True)
def _clear_get_db_override():
    yield
    app.dependency_overrides.clear()


async def _reader(db: AsyncSession, email: str, perms=("shipments.read",)):
    role = await make_role(db, name=f"R-{email}", permission_codes=list(perms))
    return await make_user(db, email=email, role=role)


async def _ship(db, order, status, *, courier_name=None, pickup=None, delivered=None, awb=None):
    shipment = Shipment(
        order_id=order.id,
        current_status=status,
        pickup_date=pickup,
        actual_delivery_date=delivered,
        awb=awb,
        source_system="manual",
    )
    db.add(shipment)
    await db.flush()
    if courier_name:
        db.add(
            ShipmentEvent(
                shipment_id=shipment.id,
                status="IN TRANSIT",
                event_timestamp=NOW,
                courier_name=courier_name,
                source="shiprocket_webhook",
            )
        )
    await db.commit()
    return shipment


async def _get(db, user, path, **params):
    async with bearer_client(app, get_db, db, user.id) as client:
        response = await client.get(
            path,
            params={
                "date_from": (NOW - timedelta(days=30)).isoformat(),
                "date_to": (NOW + timedelta(days=1)).isoformat(),
                **params,
            },
        )
        return response


async def _seed(db: AsyncSession) -> None:
    """9 orders: 2 delivered Blue Dart (COD+prepaid), 1 delivered Delhivery
    COD, 1 Blue Dart RTO, 1 in-transit Delhivery, 1 ready-to-ship (pending
    >48h), 1 pickup-pending (pending ~10h), 1 new order, 1 cancelled order.
    """
    day = NOW - timedelta(days=3)

    async def order(n, *, pay=PaymentType.PREPAID, started=None, status=OrderStatus.CONFIRMED):
        return await make_order(
            db,
            order_number=n,
            payment_type=pay,
            order_datetime=started or day,
            status=status,
        )

    o = await order("DD-1")
    await _ship(
        db,
        o,
        ShipmentStatus.DELIVERED,
        courier_name="Blue Dart Surface",
        pickup=day + timedelta(hours=4),
        delivered=day + timedelta(days=2, hours=4),
        awb="AWB1",
    )
    o = await order("DD-2", pay=PaymentType.COD)
    await _ship(
        db,
        o,
        ShipmentStatus.DELIVERED,
        courier_name="Bluedart Air",
        pickup=day + timedelta(hours=6),
        delivered=day + timedelta(days=4, hours=6),
        awb="AWB2",
    )
    o = await order("DD-3", pay=PaymentType.COD)
    await _ship(
        db,
        o,
        ShipmentStatus.DELIVERED,
        courier_name="Delhivery",
        pickup=day + timedelta(hours=2),
        delivered=day + timedelta(days=1, hours=2),
        awb="AWB3",
    )
    o = await order("DD-4")
    await _ship(
        db,
        o,
        ShipmentStatus.RTO_INITIATED,
        courier_name="Blue Dart Surface",
        pickup=day + timedelta(hours=3),
        awb="AWB4",
    )
    o = await order("DD-5")
    await _ship(
        db,
        o,
        ShipmentStatus.IN_TRANSIT,
        courier_name="Delhivery",
        pickup=day + timedelta(hours=3),
        awb="AWB5",
    )
    o = await order("DD-6", started=NOW - timedelta(hours=60))
    await _ship(db, o, ShipmentStatus.PENDING, courier_name="Delhivery", awb="AWB6")
    o = await order("DD-7", started=NOW - timedelta(hours=10))
    await _ship(db, o, ShipmentStatus.PENDING, courier_name="Delhivery", awb="AWB7")
    await order("DD-8", status=OrderStatus.PENDING)
    await order("DD-9", status=OrderStatus.CANCELLED)


def _numbers(rows):
    return [r["order_number"] for r in rows]


# --- Module 2 --------------------------------------------------------------


async def test_status_kpis_match_their_drilldowns(db_session):
    user = await _reader(db_session, "m2a@example.com")
    await _seed(db_session)
    summary = (await _get(db_session, user, f"{BASE}/status-analytics")).json()["data"]

    for kpi, field in (
        ("total", "total_orders"),
        ("fulfilled", "total_fulfilled"),
        ("delivered", "total_delivered"),
        ("rto", "total_rto"),
    ):
        listing = (await _get(db_session, user, f"{BASE}/status-analytics/orders", kpi=kpi)).json()
        assert listing["meta"]["total_items"] == summary[field], kpi
        numbers = _numbers(listing["data"])
        assert len(numbers) == len(set(numbers)), "an order appeared twice"

    assert summary["total_delivered"] == 3
    assert summary["total_rto"] == 1


async def test_every_category_drilldown_matches_its_count_and_they_partition_total(db_session):
    user = await _reader(db_session, "m2b@example.com")
    await _seed(db_session)
    summary = (await _get(db_session, user, f"{BASE}/status-analytics")).json()["data"]

    seen: list[str] = []
    for group in summary["status_groups"]:
        listing = (
            await _get(db_session, user, f"{BASE}/status-analytics/orders", group=group["key"])
        ).json()
        assert listing["meta"]["total_items"] == group["count"], group["key"]
        # The original technical status is preserved on every row.
        for row in listing["data"]:
            assert row["unified_status"] in group["statuses"]
        seen += _numbers(listing["data"])

    assert sorted(seen) == sorted(set(seen)), "an order sits in two categories"
    assert len(seen) == summary["total_orders"]
    assert sum(g["count"] for g in summary["status_groups"]) == summary["total_orders"]


async def test_return_to_origin_groups_rto_initiated_and_delivered_but_in_transit_takes_rto_in_transit(
    db_session,
):
    user = await _reader(db_session, "m2c@example.com")
    summary = (await _get(db_session, user, f"{BASE}/status-analytics")).json()["data"]
    by_key = {g["key"]: g["statuses"] for g in summary["status_groups"]}
    assert by_key["return_to_origin"] == ["rto_initiated", "rto_delivered"]
    assert by_key["in_transit"] == ["in_transit", "rto_in_transit"]
    assert by_key["tracking_pending"] == ["fulfilled_tracking_unavailable"]


async def test_drilldown_needs_exactly_one_selector(db_session):
    user = await _reader(db_session, "m2d@example.com")
    none = await _get(db_session, user, f"{BASE}/status-analytics/orders")
    assert none.status_code == 422
    two = await _get(
        db_session, user, f"{BASE}/status-analytics/orders", kpi="total", group="new_orders"
    )
    assert two.status_code == 422
    bad = await _get(db_session, user, f"{BASE}/status-analytics/orders", group="nope")
    assert bad.status_code == 422


async def test_drilldown_search_pagination_and_tracking_fields(db_session):
    user = await _reader(db_session, "m2e@example.com")
    await _seed(db_session)

    by_number = (
        await _get(db_session, user, f"{BASE}/status-analytics/orders", kpi="total", q="DD-3")
    ).json()
    assert _numbers(by_number["data"]) == ["DD-3"]
    row = by_number["data"][0]
    assert row["shipment_status"] == "delivered"
    assert row["awb"] == "AWB3"
    assert row["courier_name"] == "Delhivery"
    assert row["pickup_date"] is not None and row["actual_delivery_date"] is not None

    by_awb = (
        await _get(db_session, user, f"{BASE}/status-analytics/orders", kpi="total", q="awb5")
    ).json()
    assert _numbers(by_awb["data"]) == ["DD-5"]

    first = (
        await _get(
            db_session, user, f"{BASE}/status-analytics/orders", kpi="total", page_size=4, page=1
        )
    ).json()
    second = (
        await _get(
            db_session, user, f"{BASE}/status-analytics/orders", kpi="total", page_size=4, page=2
        )
    ).json()
    assert first["meta"]["total_items"] == 9
    assert not set(_numbers(first["data"])) & set(_numbers(second["data"]))


async def test_drilldown_respects_payment_and_date_filters(db_session):
    user = await _reader(db_session, "m2f@example.com")
    await _seed(db_session)
    cod = (
        await _get(
            db_session, user, f"{BASE}/status-analytics/orders", kpi="delivered", payment_type="cod"
        )
    ).json()
    assert sorted(_numbers(cod["data"])) == ["DD-2", "DD-3"]
    summary_cod = (
        await _get(db_session, user, f"{BASE}/status-analytics", payment_type="cod")
    ).json()["data"]
    assert summary_cod["total_delivered"] == 2

    empty = (
        await _get(
            db_session,
            user,
            f"{BASE}/status-analytics/orders",
            kpi="total",
            date_from=(NOW + timedelta(days=5)).isoformat(),
            date_to=(NOW + timedelta(days=6)).isoformat(),
        )
    ).json()
    assert empty["data"] == []
    assert empty["meta"]["total_items"] == 0


async def test_drilldown_requires_shipments_read(db_session):
    user = await _reader(db_session, "m2g@example.com", perms=("orders.read",))
    for path, params in (
        (f"{BASE}/status-analytics/orders", {"kpi": "total"}),
        (f"{BASE}/fulfilment-performance/orders", {"scope": "fulfilled"}),
        (f"{BASE}/partner-orders", {}),
    ):
        response = await _get(db_session, user, path, **params)
        assert response.status_code == 403, path


# --- Module 3 --------------------------------------------------------------


async def test_fulfilment_scopes_match_aggregate_counts(db_session):
    user = await _reader(db_session, "m3a@example.com")
    await _seed(db_session)
    summary = (await _get(db_session, user, f"{BASE}/fulfilment-performance")).json()["data"]
    delivery = (await _get(db_session, user, f"{BASE}/delivery-performance")).json()["data"]

    expectations = {
        "fulfilled": summary["fulfilled_order_count"],
        "pending": summary["pending_fulfilment_count"],
        "pending_24h": summary["pending_over_24h_count"],
        "pending_48h": summary["pending_over_48h_count"],
        "delivered": delivery["delivered_order_count"],
        "cod_delivered": delivery["cod_delivered_order_count"],
        "prepaid_delivered": delivery["prepaid_delivered_order_count"],
    }
    for scope, expected in expectations.items():
        listing = (
            await _get(db_session, user, f"{BASE}/fulfilment-performance/orders", scope=scope)
        ).json()
        assert listing["meta"]["total_items"] == expected, scope
        numbers = _numbers(listing["data"])
        assert len(numbers) == len(set(numbers))

    assert expectations["delivered"] == 3
    assert expectations["cod_delivered"] == 2
    assert expectations["prepaid_delivered"] == 1
    assert expectations["pending"] == 2
    assert expectations["pending_48h"] == 1
    assert expectations["pending_24h"] == 1


async def test_pending_orders_are_never_in_fulfilled_lists(db_session):
    user = await _reader(db_session, "m3b@example.com")
    await _seed(db_session)
    pending = (
        await _get(db_session, user, f"{BASE}/fulfilment-performance/orders", scope="pending")
    ).json()["data"]
    fulfilled = (
        await _get(db_session, user, f"{BASE}/fulfilment-performance/orders", scope="fulfilled")
    ).json()["data"]
    assert not set(_numbers(pending)) & set(_numbers(fulfilled))
    assert all(r["pending_hours"] is not None and r["pickup_date"] is None for r in pending)


async def test_fulfilment_rows_carry_durations(db_session):
    user = await _reader(db_session, "m3c@example.com")
    await _seed(db_session)
    rows = (
        await _get(
            db_session, user, f"{BASE}/fulfilment-performance/orders", scope="delivered", q="DD-1"
        )
    ).json()["data"]
    assert len(rows) == 1
    assert rows[0]["delivery_days"] == pytest.approx(2.0)
    assert rows[0]["fulfilment_days"] == pytest.approx(4 / 24, abs=0.01)
    assert rows[0]["payment_type"] == "prepaid"
    assert rows[0]["courier_name"] == "Blue Dart Surface"


async def test_daily_chart_day_matches_trend_counts(db_session):
    user = await _reader(db_session, "m3d@example.com")
    await _seed(db_session)
    fulfil_trend = (await _get(db_session, user, f"{BASE}/fulfilment-performance/history")).json()[
        "data"
    ]["daily"]
    delivery_trend = (await _get(db_session, user, f"{BASE}/delivery-performance/history")).json()[
        "data"
    ]["daily"]
    assert fulfil_trend and delivery_trend

    for point in fulfil_trend:
        listing = (
            await _get(
                db_session,
                user,
                f"{BASE}/fulfilment-performance/orders",
                scope="fulfilled",
                day=point["date"],
            )
        ).json()
        assert listing["meta"]["total_items"] == point["fulfilled_order_count"], point["date"]
    for point in delivery_trend:
        listing = (
            await _get(
                db_session,
                user,
                f"{BASE}/fulfilment-performance/orders",
                scope="delivered",
                day=point["date"],
            )
        ).json()
        assert listing["meta"]["total_items"] == point["delivered_order_count"], point["date"]


async def test_fulfilment_scope_validation(db_session):
    user = await _reader(db_session, "m3e@example.com")
    assert (
        await _get(db_session, user, f"{BASE}/fulfilment-performance/orders", scope="x")
    ).status_code == 422
    assert (
        await _get(
            db_session, user, f"{BASE}/fulfilment-performance/orders", scope="fulfilled", day="bad"
        )
    ).status_code == 422


async def test_fulfilment_drilldown_respects_payment_filter(db_session):
    user = await _reader(db_session, "m3f@example.com")
    await _seed(db_session)
    cod = (
        await _get(
            db_session,
            user,
            f"{BASE}/fulfilment-performance/orders",
            scope="delivered",
            payment_type="cod",
        )
    ).json()
    summary = (
        await _get(db_session, user, f"{BASE}/delivery-performance", payment_type="cod")
    ).json()["data"]
    assert cod["meta"]["total_items"] == summary["delivered_order_count"] == 2
    assert {r["payment_type"] for r in cod["data"]} == {"cod"}


# --- Module 4 --------------------------------------------------------------


async def test_partner_rows_and_metrics_match_their_drilldowns(db_session):
    user = await _reader(db_session, "m4a@example.com")
    await _seed(db_session)
    perf = (await _get(db_session, user, f"{BASE}/partner-performance")).json()["data"]["partners"]
    names = {p["courier_name"] for p in perf}
    assert names == {"Blue Dart", "Delhivery"}

    for partner in perf:
        key = partner["courier_key"]
        checks = {
            "allocated": partner["total_allocated"],
            "picked_up": partner["total_picked_up"],
            "delivered": partner["total_delivered"],
        }
        for metric, expected in checks.items():
            listing = (
                await _get(db_session, user, f"{BASE}/partner-orders", partner=key, metric=metric)
            ).json()
            assert listing["meta"]["total_items"] == expected, (partner["courier_name"], metric)
            assert all(r["courier_name"] for r in listing["data"])

    blue = next(p for p in perf if p["courier_name"] == "Blue Dart")
    rto = (
        await _get(
            db_session, user, f"{BASE}/partner-orders", partner=blue["courier_key"], metric="rto"
        )
    ).json()
    assert _numbers(rto["data"]) == ["DD-4"]
    assert blue["rto_percentage"] == pytest.approx(100 * 1 / blue["total_picked_up"], abs=0.01)


async def test_cod_and_prepaid_success_drilldowns_match_numerators(db_session):
    user = await _reader(db_session, "m4b@example.com")
    await _seed(db_session)
    perf = (await _get(db_session, user, f"{BASE}/partner-performance")).json()["data"]["partners"]
    blue = next(p for p in perf if p["courier_name"] == "Blue Dart")
    cod = (
        await _get(
            db_session,
            user,
            f"{BASE}/partner-orders",
            partner=blue["courier_key"],
            metric="cod_success",
        )
    ).json()
    prepaid = (
        await _get(
            db_session,
            user,
            f"{BASE}/partner-orders",
            partner=blue["courier_key"],
            metric="prepaid_success",
        )
    ).json()
    assert _numbers(cod["data"]) == ["DD-2"]
    assert _numbers(prepaid["data"]) == ["DD-1"]
    assert {r["payment_type"] for r in cod["data"]} == {"cod"}


async def test_partner_orders_without_partner_equals_total_allocated(db_session):
    user = await _reader(db_session, "m4c@example.com")
    await _seed(db_session)
    alloc = (await _get(db_session, user, f"{BASE}/partner-allocation")).json()["data"]
    everyone = (await _get(db_session, user, f"{BASE}/partner-orders")).json()
    assert everyone["meta"]["total_items"] == alloc["total_orders_allocated"]
    numbers = _numbers(everyone["data"])
    assert len(numbers) == len(set(numbers))
    # Orders with no identifiable courier (DD-8 new, DD-9 cancelled) are absent.
    assert "DD-8" not in numbers and "DD-9" not in numbers


async def test_partner_drilldown_uses_the_same_filters(db_session):
    user = await _reader(db_session, "m4d@example.com")
    await _seed(db_session)
    alloc = (await _get(db_session, user, f"{BASE}/partner-allocation", payment_type="cod")).json()[
        "data"
    ]
    listing = (await _get(db_session, user, f"{BASE}/partner-orders", payment_type="cod")).json()
    assert listing["meta"]["total_items"] == alloc["total_orders_allocated"]
    assert {r["payment_type"] for r in listing["data"]} == {"cod"}

    outside = (
        await _get(
            db_session,
            user,
            f"{BASE}/partner-orders",
            date_from=(NOW + timedelta(days=5)).isoformat(),
            date_to=(NOW + timedelta(days=6)).isoformat(),
        )
    ).json()
    assert outside["meta"]["total_items"] == 0


async def test_partner_search_and_pagination(db_session):
    user = await _reader(db_session, "m4e@example.com")
    await _seed(db_session)
    hit = (await _get(db_session, user, f"{BASE}/partner-orders", q="awb2")).json()
    assert _numbers(hit["data"]) == ["DD-2"]
    page1 = (await _get(db_session, user, f"{BASE}/partner-orders", page_size=3, page=1)).json()
    page2 = (await _get(db_session, user, f"{BASE}/partner-orders", page_size=3, page=2)).json()
    assert len(page1["data"]) == 3
    assert not set(_numbers(page1["data"])) & set(_numbers(page2["data"]))


async def test_unknown_partner_returns_empty_not_error(db_session):
    user = await _reader(db_session, "m4f@example.com")
    await _seed(db_session)
    listing = (
        await _get(db_session, user, f"{BASE}/partner-orders", partner="family:nobody")
    ).json()
    assert listing["data"] == [] and listing["meta"]["total_items"] == 0
