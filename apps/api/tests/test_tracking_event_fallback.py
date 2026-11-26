"""Regression: Module 3/4 analytics when pickup / delivery / courier live
only in the append-only `shipment_events` timeline.

Production symptom this guards: shipments tracked through Shiprocket
webhooks/pull-sync had `current_status` set but NULL `pickup_date`,
`actual_delivery_date` and `courier_id`, so Fulfilment & Delivery showed
"0 fulfilled / 0 delivered" and Shipping Partner Performance was empty.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.db.session import get_db
from app.integrations.shiprocket.normalizer import WEBHOOK_TRACKING_NORMALIZER
from app.integrations.shiprocket.sync import apply_tracking_event
from app.main import app
from app.models.courier import Courier
from app.models.enums import OrderStatus, PaymentType, ShipmentStatus
from app.models.shipment import Shipment, ShipmentEvent
from app.services.inventory_service import InventoryService
from app.services.rto_service import RTOService
from app.services.shipment_service import ShipmentService
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import bearer_client, make_order, make_role, make_user

pytestmark = pytest.mark.asyncio

NOW = datetime.now(UTC).replace(microsecond=0)


@pytest.fixture(autouse=True)
def _clear_get_db_override():
    yield
    app.dependency_overrides.clear()


async def _reader(db: AsyncSession, email: str):
    role = await make_role(db, name=f"OPS-{email}", permission_codes=["shipments.read"])
    return await make_user(db, email=email, role=role)


async def _shipment(db, order, status, *, pickup=None, delivered=None, courier_id=None):
    s = Shipment(
        order_id=order.id,
        current_status=status,
        pickup_date=pickup,
        actual_delivery_date=delivered,
        courier_id=courier_id,
        source_system="manual",
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _event(db, shipment, status, ts, courier_name=None):
    db.add(
        ShipmentEvent(
            shipment_id=shipment.id,
            status=status,
            event_timestamp=ts,
            courier_name=courier_name,
            source="shiprocket_webhook",
        )
    )
    await db.commit()


async def _get(db, user, path, **params):
    async with bearer_client(app, get_db, db, user.id) as client:
        r = await client.get(
            path, params={"date_from": (NOW - timedelta(days=30)).isoformat(), **params}
        )
        assert r.status_code == 200, r.text
        return r.json()["data"]


async def _confirmed_order(db, number, *, created, payment=PaymentType.PREPAID):
    return await make_order(
        db,
        order_number=number,
        status=OrderStatus.CONFIRMED,
        order_datetime=created,
        payment_type=payment,
    )


async def test_pickup_and_delivery_read_from_events_when_columns_null(
    db_session: AsyncSession,
) -> None:
    user = await _reader(db_session, "ev1@example.com")
    created = NOW - timedelta(days=10)
    order = await _confirmed_order(db_session, "EV-1", created=created)
    s = await _shipment(db_session, order, ShipmentStatus.DELIVERED)
    await _event(db_session, s, "PICKED UP", created + timedelta(days=1))
    await _event(db_session, s, "In Transit", created + timedelta(days=2))
    await _event(db_session, s, "DELIVERED", created + timedelta(days=4))

    ful = await _get(db_session, user, "/api/v1/shipments/fulfilment-performance")
    assert ful["fulfilled_order_count"] == 1
    assert ful["avg_fulfilment_days"] == pytest.approx(1.0, abs=0.01)
    assert ful["missing_pickup_timestamp_count"] == 0

    dlv = await _get(db_session, user, "/api/v1/shipments/delivery-performance")
    assert dlv["delivered_order_count"] == 1
    assert dlv["overall_avg_delivery_days"] == pytest.approx(3.0, abs=0.01)
    assert dlv["missing_timestamp_count"] == 0
    assert dlv["prepaid_avg_delivery_days"] == pytest.approx(3.0, abs=0.01)
    assert dlv["cod_avg_delivery_days"] is None


async def test_stored_columns_win_over_events(db_session: AsyncSession) -> None:
    user = await _reader(db_session, "ev2@example.com")
    created = NOW - timedelta(days=10)
    order = await _confirmed_order(db_session, "EV-2", created=created)
    s = await _shipment(
        db_session,
        order,
        ShipmentStatus.DELIVERED,
        pickup=created + timedelta(days=1),
        delivered=created + timedelta(days=3),
    )
    await _event(db_session, s, "PICKED UP", created + timedelta(days=2))
    await _event(db_session, s, "DELIVERED", created + timedelta(days=9))
    dlv = await _get(db_session, user, "/api/v1/shipments/delivery-performance")
    assert dlv["overall_avg_delivery_days"] == pytest.approx(2.0, abs=0.01)


async def test_genuinely_missing_timestamps_stay_missing_never_zero(
    db_session: AsyncSession,
) -> None:
    """Delivered, but the only event is DELIVERED -- no pickup anywhere.
    Must be counted as missing, not averaged as a 0-day delivery.
    """
    user = await _reader(db_session, "ev3@example.com")
    created = NOW - timedelta(days=10)
    order = await _confirmed_order(db_session, "EV-3", created=created)
    s = await _shipment(db_session, order, ShipmentStatus.DELIVERED)
    await _event(db_session, s, "DELIVERED", created + timedelta(days=4))

    dlv = await _get(db_session, user, "/api/v1/shipments/delivery-performance")
    assert dlv["delivered_order_count"] == 0
    assert dlv["overall_avg_delivery_days"] is None
    assert dlv["missing_timestamp_count"] == 1
    ful = await _get(db_session, user, "/api/v1/shipments/fulfilment-performance")
    assert ful["missing_pickup_timestamp_count"] == 1


async def test_rto_delivered_event_is_not_a_delivery(db_session: AsyncSession) -> None:
    user = await _reader(db_session, "ev4@example.com")
    created = NOW - timedelta(days=10)
    order = await _confirmed_order(db_session, "EV-4", created=created)
    s = await _shipment(db_session, order, ShipmentStatus.RTO_DELIVERED)
    await _event(db_session, s, "PICKED UP", created + timedelta(days=1))
    await _event(db_session, s, "RTO DELIVERED", created + timedelta(days=6))
    dlv = await _get(db_session, user, "/api/v1/shipments/delivery-performance")
    assert dlv["delivered_order_count"] == 0
    assert dlv["missing_timestamp_count"] == 0


async def test_delivery_before_pickup_is_excluded_not_negative(db_session: AsyncSession) -> None:
    user = await _reader(db_session, "ev5@example.com")
    created = NOW - timedelta(days=10)
    order = await _confirmed_order(db_session, "EV-5", created=created)
    await _shipment(
        db_session,
        order,
        ShipmentStatus.DELIVERED,
        pickup=created + timedelta(days=5),
        delivered=created + timedelta(days=2),
    )
    dlv = await _get(db_session, user, "/api/v1/shipments/delivery-performance")
    assert dlv["delivered_order_count"] == 0
    assert dlv["missing_timestamp_count"] == 1


async def test_partners_from_event_courier_names_without_courier_rows(
    db_session: AsyncSession,
) -> None:
    """No `Courier` row and no `courier_id`: the partner is identified by
    the courier name on its tracking events. Name variants merge; COD and
    prepaid are separated; orders with no courier anywhere are not
    allocated to any partner.
    """
    user = await _reader(db_session, "ev6@example.com")
    created = NOW - timedelta(days=10)

    async def ship(number, courier, final, *, payment=PaymentType.PREPAID, deliver_days=3):
        order = await _confirmed_order(db_session, number, created=created, payment=payment)
        s = await _shipment(db_session, order, final)
        await _event(db_session, s, "PICKED UP", created + timedelta(days=1), courier)
        if final == ShipmentStatus.DELIVERED:
            await _event(
                db_session, s, "DELIVERED", created + timedelta(days=1 + deliver_days), courier
            )
        return s

    await ship("P-1", "Delhivery", ShipmentStatus.DELIVERED, deliver_days=2)
    await ship(
        "P-2", "delhivery ", ShipmentStatus.DELIVERED, payment=PaymentType.COD, deliver_days=4
    )
    await ship("P-3", "Delhivery", ShipmentStatus.RTO_DELIVERED)
    await ship("P-4", "Blue Dart", ShipmentStatus.DELIVERED, deliver_days=1)
    anon = await _confirmed_order(db_session, "P-5", created=created)
    await _shipment(db_session, anon, ShipmentStatus.IN_TRANSIT)  # no courier anywhere

    alloc = await _get(db_session, user, "/api/v1/shipments/partner-allocation")
    assert alloc["total_orders_allocated"] == 4
    assert sum(p["allocation_percent"] for p in alloc["partners"]) == pytest.approx(
        100.0, abs=0.001
    )
    by_key = {p["courier_key"]: p for p in alloc["partners"]}
    assert set(by_key) == {"family:delhivery", "family:blue dart"}
    assert by_key["family:delhivery"]["orders_allocated"] == 3
    assert by_key["family:delhivery"]["allocation_percent"] == pytest.approx(75.0, abs=0.001)
    assert by_key["family:delhivery"]["courier_id"] is None
    assert by_key["family:delhivery"]["avg_delivery_days"] == pytest.approx(3.0, abs=0.01)
    assert by_key["family:blue dart"]["avg_delivery_days"] == pytest.approx(1.0, abs=0.01)

    perf = await _get(db_session, user, "/api/v1/shipments/partner-performance")
    d = next(p for p in perf["partners"] if p["courier_key"] == "family:delhivery")
    assert (d["total_allocated"], d["total_picked_up"], d["total_delivered"]) == (3, 3, 2)
    assert d["delivery_success_rate"] == pytest.approx(66.67, abs=0.01)
    assert d["rto_percentage"] == pytest.approx(33.33, abs=0.01)
    assert d["cod_delivery_success_rate"] == 100.0
    assert d["prepaid_delivery_success_rate"] == 50.0


async def test_linked_courier_and_event_name_are_one_partner(db_session: AsyncSession) -> None:
    user = await _reader(db_session, "ev7@example.com")
    created = NOW - timedelta(days=10)
    courier = Courier(name="Shiprocket", code="EV7-SR")
    db_session.add(courier)
    await db_session.commit()
    await db_session.refresh(courier)
    o1 = await _confirmed_order(db_session, "L-1", created=created)
    await _shipment(db_session, o1, ShipmentStatus.IN_TRANSIT, courier_id=courier.id)
    o2 = await _confirmed_order(db_session, "L-2", created=created)
    s2 = await _shipment(db_session, o2, ShipmentStatus.IN_TRANSIT)
    await _event(db_session, s2, "PICKED UP", created + timedelta(days=1), "SHIPROCKET")

    alloc = await _get(db_session, user, "/api/v1/shipments/partner-allocation")
    assert len(alloc["partners"]) == 1
    assert alloc["partners"][0]["orders_allocated"] == 2
    assert alloc["partners"][0]["courier_id"] == str(courier.id)


async def test_ingestion_persists_pickup_delivery_and_courier(db_session: AsyncSession) -> None:
    """Forward fix: the shared ingestion path now writes the real event
    time onto the shipment, links an existing Courier by name, and keeps
    the earliest pickup / first delivery on replays.
    """
    courier = Courier(name="Delhivery", code="ING-DL")
    db_session.add(courier)
    await db_session.commit()
    await db_session.refresh(courier)
    order = await _confirmed_order(db_session, "ING-1", created=NOW - timedelta(days=10))
    s = await _shipment(db_session, order, ShipmentStatus.PENDING)

    svc = ShipmentService(db_session)

    async def send(status, ts):
        normalized = WEBHOOK_TRACKING_NORMALIZER.normalize_event(
            {
                "current_status": status,
                "courier_name": "delhivery",
                "current_timestamp": ts.isoformat(),
            }
        )
        await apply_tracking_event(
            db_session,
            s,
            normalized,
            shipment_service=svc,
            rto_service=RTOService(db_session),
            inventory_service=InventoryService(db_session),
        )

    t_pick = NOW - timedelta(days=8)
    t_del = NOW - timedelta(days=5)
    await send("PICKED UP", t_pick)
    await send("DELIVERED", t_del)
    await send("PICKED UP", t_pick + timedelta(hours=6))  # later duplicate scan: pickup stays earliest
    await db_session.refresh(s)

    assert s.pickup_date == t_pick
    assert s.actual_delivery_date == t_del
    assert s.courier_id == courier.id


# ---- carrier families, real production service names -------------------------------------


@pytest.mark.parametrize(
    ("raw", "family"),
    [
        ("Blue Dart Surface", "Blue Dart"),
        ("Bluedart Surface - Select  500gm", "Blue Dart"),
        ("Blue Dart Air", "Blue Dart"),
        ("bluedart surface 2kg", "Blue Dart"),
        ("Delhivery Surface", "Delhivery"),
        ("Delhivery Air", "Delhivery"),
        ("Delhivery Surface 2 Kgs", "Delhivery"),
        ("Shadowfax Surface", "Shadowfax"),
        ("Xpressbees Surface", "Xpressbees"),
        ("Xpressbees Surface 2kg", "Xpressbees"),
        ("DTDC Surface", "DTDC"),
        ("DTDC Air 500gm", "DTDC"),
        ("Ekart Logistics Surface", "Ekart"),
        ("Amazon COD Surface 500gm", "Amazon Shipping"),
        ("Amazon Prepaid Surface 500g", "Amazon Shipping"),
    ],
)
def test_carrier_family_mapping(raw: str, family: str) -> None:
    from app.repositories.shipment_effective import carrier_family, partner_display_name

    assert carrier_family(raw) == family
    assert partner_display_name(raw) == family


@pytest.mark.parametrize("raw", [None, "", "   ", "Some Local Courier"])
def test_unmapped_or_empty_names_are_not_forced_into_a_family(raw) -> None:
    from app.repositories.shipment_effective import carrier_family

    assert carrier_family(raw) is None


async def test_service_names_group_into_one_carrier_and_stay_visible(
    db_session: AsyncSession,
) -> None:
    """Four Blue Dart service names are ONE partner; the original service
    names remain available for the detail view.
    """
    user = await _reader(db_session, "fam1@example.com")
    created = NOW - timedelta(days=10)
    services = [
        "Blue Dart Surface",
        "Blue Dart Surface",
        "Bluedart Surface - Select 500gm",
        "Blue Dart Air",
    ]
    for i, service in enumerate(services):
        order = await _confirmed_order(db_session, f"FAM-{i}", created=created)
        s = await _shipment(db_session, order, ShipmentStatus.DELIVERED)
        await _event(db_session, s, "PICKED UP", created + timedelta(days=1), service)
        await _event(db_session, s, "DELIVERED", created + timedelta(days=3), service)
    other = await _confirmed_order(db_session, "FAM-X", created=created)
    s = await _shipment(db_session, other, ShipmentStatus.IN_TRANSIT)
    await _event(db_session, s, "PICKED UP", created + timedelta(days=1), "Shadowfax Surface")

    alloc = await _get(db_session, user, "/api/v1/shipments/partner-allocation")
    names = {p["courier_name"]: p for p in alloc["partners"]}
    assert set(names) == {"Blue Dart", "Shadowfax"}
    assert names["Blue Dart"]["orders_allocated"] == 4
    assert names["Blue Dart"]["service_names"] == [
        "Blue Dart Surface",
        "Blue Dart Air",
        "Bluedart Surface - Select 500gm",
    ]
    assert names["Shadowfax"]["service_names"] == ["Shadowfax Surface"]

    perf = await _get(db_session, user, "/api/v1/shipments/partner-performance")
    bd = next(p for p in perf["partners"] if p["courier_name"] == "Blue Dart")
    assert bd["total_delivered"] == 4
    assert bd["avg_delivery_days"] == pytest.approx(2.0, abs=0.01)
    assert len(bd["service_names"]) == 3

    dlv = await _get(db_session, user, "/api/v1/shipments/delivery-performance")
    row = next(r for r in dlv["by_courier"] if r["courier_name"] == "Blue Dart")
    assert row["delivered_order_count"] == 4
    assert len(row["service_names"]) == 3


async def test_two_courier_rows_of_one_family_merge_without_ambiguous_id(
    db_session: AsyncSession,
) -> None:
    user = await _reader(db_session, "fam2@example.com")
    created = NOW - timedelta(days=10)
    c1 = Courier(name="Blue Dart", code="FAM2-A")
    c2 = Courier(name="Blue Dart Surface", code="FAM2-B")
    db_session.add_all([c1, c2])
    await db_session.commit()
    for i, c in enumerate((c1, c2)):
        order = await _confirmed_order(db_session, f"FAM2-{i}", created=created)
        await _shipment(db_session, order, ShipmentStatus.IN_TRANSIT, courier_id=c.id)
    alloc = await _get(db_session, user, "/api/v1/shipments/partner-allocation")
    assert len(alloc["partners"]) == 1
    assert alloc["partners"][0]["orders_allocated"] == 2
    assert alloc["partners"][0]["courier_id"] is None  # two backing rows -> no single id


# ---- pickup is NEVER inferred from non-pickup events ------------------------------------


@pytest.mark.parametrize("label", ["IN TRANSIT", "OUT FOR PICKUP", "REACHED AT DESTINATION HUB", "UNDELIVERED"])
async def test_pickup_never_inferred_from_other_events(db_session: AsyncSession, label: str) -> None:
    """Production shape: a delivered shipment whose first event is IN
    TRANSIT / OUT FOR PICKUP and which never got a PICKED UP scan. The
    pickup stays missing; delivery time is NOT computed from that event.
    """
    user = await _reader(db_session, f"np-{label.replace(' ', '')}@example.com")
    created = NOW - timedelta(days=10)
    order = await _confirmed_order(db_session, f"NP-{label.replace(' ', '')}", created=created)
    s = await _shipment(db_session, order, ShipmentStatus.DELIVERED)
    await _event(db_session, s, label, created + timedelta(days=1), "Blue Dart Surface")
    await _event(db_session, s, "DELIVERED", created + timedelta(days=4), "Blue Dart Surface")

    ful = await _get(db_session, user, "/api/v1/shipments/fulfilment-performance")
    assert ful["fulfilled_order_count"] == 0
    assert ful["avg_fulfilment_days"] is None
    assert ful["missing_pickup_timestamp_count"] == 1
    dlv = await _get(db_session, user, "/api/v1/shipments/delivery-performance")
    assert dlv["delivered_order_count"] == 0
    assert dlv["overall_avg_delivery_days"] is None
    assert dlv["missing_timestamp_count"] == 1
    # Still allocated to the carrier (its courier IS identifiable)...
    alloc = await _get(db_session, user, "/api/v1/shipments/partner-allocation")
    assert alloc["partners"][0]["courier_name"] == "Blue Dart"
    # ...and counted as delivered, with no average delivery time fabricated.
    perf = await _get(db_session, user, "/api/v1/shipments/partner-performance")
    assert perf["partners"][0]["total_delivered"] == 1
    assert perf["partners"][0]["avg_delivery_days"] is None
    assert perf["partners"][0]["missing_delivery_timestamp_count"] == 0


async def test_events_with_no_courier_name_are_excluded_from_partners(
    db_session: AsyncSession,
) -> None:
    """Unknown courier: no linked Courier and no courier name on any
    event (e.g. a pending shipment with no AWB) -> not allocated.
    """
    user = await _reader(db_session, "unk1@example.com")
    created = NOW - timedelta(days=10)
    o1 = await _confirmed_order(db_session, "UNK-1", created=created)
    s1 = await _shipment(db_session, o1, ShipmentStatus.IN_TRANSIT)
    await _event(db_session, s1, "IN TRANSIT", created + timedelta(days=1), None)
    await _event(db_session, s1, "IN TRANSIT", created + timedelta(days=2), "   ")
    o2 = await _confirmed_order(db_session, "UNK-2", created=created)
    await _shipment(db_session, o2, ShipmentStatus.PENDING)  # no events at all
    o3 = await _confirmed_order(db_session, "UNK-3", created=created)
    s3 = await _shipment(db_session, o3, ShipmentStatus.IN_TRANSIT)
    await _event(db_session, s3, "PICKED UP", created + timedelta(days=1), "Delhivery Surface")

    alloc = await _get(db_session, user, "/api/v1/shipments/partner-allocation")
    assert alloc["total_orders_allocated"] == 1
    assert [p["courier_name"] for p in alloc["partners"]] == ["Delhivery"]


async def test_partner_kpis_respect_the_date_range(db_session: AsyncSession) -> None:
    """The date range scopes every partner figure: an order outside the
    window contributes nothing to allocation, performance or delivery.
    """
    user = await _reader(db_session, "dr1@example.com")
    inside = NOW - timedelta(days=5)
    outside = NOW - timedelta(days=60)
    for number, created in (("DR-IN", inside), ("DR-OUT", outside)):
        order = await _confirmed_order(db_session, number, created=created)
        s = await _shipment(db_session, order, ShipmentStatus.DELIVERED)
        await _event(db_session, s, "PICKED UP", created + timedelta(days=1), "DTDC Surface")
        await _event(db_session, s, "DELIVERED", created + timedelta(days=3), "DTDC Surface")

    window = {"date_from": (NOW - timedelta(days=30)).isoformat(), "date_to": NOW.isoformat()}
    async with bearer_client(app, get_db, db_session, user.id) as client:
        for path, field in (
            ("/api/v1/shipments/partner-allocation", "total_orders_allocated"),
        ):
            data = (await client.get(path, params=window)).json()["data"]
            assert data[field] == 1
        perf = (await client.get("/api/v1/shipments/partner-performance", params=window)).json()["data"]
        assert perf["partners"][0]["total_allocated"] == 1
        dlv = (await client.get("/api/v1/shipments/delivery-performance", params=window)).json()["data"]
        assert dlv["delivered_order_count"] == 1
        ful = (await client.get("/api/v1/shipments/fulfilment-performance", params=window)).json()["data"]
        assert ful["fulfilled_order_count"] == 1
        trend = (await client.get("/api/v1/shipments/delivery-performance/history", params=window)).json()["data"]
        assert sum(p["delivered_order_count"] for p in trend["daily"]) == 1
