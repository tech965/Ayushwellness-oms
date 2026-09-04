"""NDR/RTO have no POST endpoint (spec §36 — creation is Phase 2 sync
work), so fixtures are created directly via the repository layer, same as
Phase 2's sync adapters will.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.models.courier import Courier
from app.models.customer import Customer
from app.models.enums import NDRStatus, RTOStatus
from app.models.ndr import NDR
from app.models.order import Order, OrderItem
from app.models.rto import RTO
from app.models.shipment import Shipment
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _create_shipment(db_session: AsyncSession, order_number: str) -> Shipment:
    order = Order(
        order_number=order_number, order_datetime=datetime.now(UTC), source_system="manual"
    )
    db_session.add(order)
    await db_session.flush()
    shipment = Shipment(order_id=order.id, awb=f"AWB-{order_number}", source_system="manual")
    db_session.add(shipment)
    await db_session.commit()
    await db_session.refresh(shipment)
    return shipment


async def _create_enriched_shipment(
    db_session: AsyncSession,
    order_number: str,
    *,
    customer_name: str = "Ananya Rao",
    customer_phone: str = "9998887776",
    product_name: str = "Ashwagandha 60ct",
    payment_type: str = "prepaid",
    total_amount: str = "649.00",
    shipment_status: str = "in_transit",
    courier_name: str | None = None,
) -> Shipment:
    """Fuller fixture for the enriched-response/search/filter tests below
    — a real Customer + OrderItem attached to the Order, matching what
    `NDRRepository.search_query`'s eager-loaded relationships expect.
    `courier_name`, when given, also attaches a real `Courier` row to the
    shipment (mirroring how a synced shipment's `courier_id` is set) so
    NDR/RTO's own `courier_id` (copied from `shipment.courier_id` at
    creation — see `NDRService.upsert_synced_ndr`) has something real to
    resolve to `courier_name` in the list response.
    """
    customer = Customer(full_name=customer_name, phone=customer_phone, source_system="manual")
    db_session.add(customer)
    await db_session.flush()

    order = Order(
        order_number=order_number,
        order_datetime=datetime.now(UTC),
        source_system="manual",
        customer_id=customer.id,
        payment_type=payment_type,
        total_amount=Decimal(total_amount),
    )
    db_session.add(order)
    await db_session.flush()

    item = OrderItem(
        order_id=order.id,
        sku="ASH-60",
        product_name=product_name,
        quantity=1,
        unit_price=Decimal(total_amount),
        total_amount=Decimal(total_amount),
        source_system="manual",
    )
    db_session.add(item)

    courier_id = None
    if courier_name is not None:
        courier = Courier(
            name=courier_name,
            code=f"{courier_name.upper().replace(' ', '-')}-{order_number}",
            source_system="manual",
        )
        db_session.add(courier)
        await db_session.flush()
        courier_id = courier.id

    shipment = Shipment(
        order_id=order.id,
        awb=f"AWB-{order_number}",
        source_system="manual",
        current_status=shipment_status,
        courier_id=courier_id,
    )
    db_session.add(shipment)
    await db_session.commit()
    await db_session.refresh(shipment)
    return shipment


async def test_ndr_read_and_update(db_session: AsyncSession, make_authenticated_client) -> None:
    shipment = await _create_shipment(db_session, "OMS-NDR-1")
    ndr = NDR(
        shipment_id=shipment.id,
        order_id=shipment.order_id,
        reason="Customer unavailable",
        status=NDRStatus.OPEN,
        source_system="shiprocket",
    )
    db_session.add(ndr)
    await db_session.commit()
    await db_session.refresh(ndr)

    async with await make_authenticated_client(
        db_session, permission_codes=["ndr.read", "ndr.update", "shipments.read"]
    ) as auth_client:
        listing = await auth_client.get("/api/v1/ndr", params={"status": "open"})
        assert listing.status_code == 200
        assert listing.json()["meta"]["total_items"] == 1

        update = await auth_client.patch(
            f"/api/v1/ndr/{ndr.id}",
            json={"status": "reattempt_scheduled", "reattempt_date": "2026-09-01T00:00:00Z"},
        )
        assert update.status_code == 200
        assert update.json()["data"]["status"] == "reattempt_scheduled"

        shipment_after = await auth_client.get(f"/api/v1/shipments/{shipment.id}")
        assert shipment_after.json()["data"]["ndr_status"] == "reattempt_scheduled"


async def test_rto_read_and_update(db_session: AsyncSession, make_authenticated_client) -> None:
    shipment = await _create_shipment(db_session, "OMS-RTO-1")
    rto = RTO(
        shipment_id=shipment.id,
        order_id=shipment.order_id,
        reason="Refused by customer",
        status=RTOStatus.INITIATED,
        source_system="shiprocket",
    )
    db_session.add(rto)
    await db_session.commit()
    await db_session.refresh(rto)

    async with await make_authenticated_client(
        db_session, permission_codes=["rto.read", "rto.update"]
    ) as auth_client:
        get_response = await auth_client.get(f"/api/v1/rto/{rto.id}")
        assert get_response.status_code == 200

        update = await auth_client.patch(f"/api/v1/rto/{rto.id}", json={"status": "received"})
        assert update.status_code == 200
        assert update.json()["data"]["status"] == "received"


# --- NDR: enriched response / search / filters / pagination ----------------


async def test_ndr_list_returns_enriched_order_customer_and_product_data(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    shipment = await _create_enriched_shipment(
        db_session, "OMS-NDR-ENR-1", courier_name="Delhivery"
    )
    ndr = NDR(
        shipment_id=shipment.id,
        order_id=shipment.order_id,
        courier_id=shipment.courier_id,
        reason="Customer unavailable",
        status=NDRStatus.OPEN,
        attempt_number=2,
        source_system="shiprocket",
    )
    db_session.add(ndr)
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["ndr.read"]
    ) as auth_client:
        response = await auth_client.get("/api/v1/ndr")
        assert response.status_code == 200
        row = response.json()["data"][0]
        assert row["order_number"] == "OMS-NDR-ENR-1"
        assert row["customer_name"] == "Ananya Rao"
        assert row["customer_phone"] == "9998887776"
        assert row["product"] == "Ashwagandha 60ct"
        assert row["order_amount"] == "649.00"
        assert row["payment_type"] == "prepaid"
        assert row["shipment_status"] == "in_transit"
        assert row["attempt_number"] == 2
        assert row["awb"] == "AWB-OMS-NDR-ENR-1"
        assert row["courier_name"] == "Delhivery"


async def test_ndr_list_handles_missing_courier_without_error(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """An NDR whose shipment has no assigned courier yet (e.g. synced
    before AWB assignment) must still serialize cleanly — `awb`/
    `courier_name` are `None`, never a fabricated value or a 500.
    """
    shipment = await _create_enriched_shipment(db_session, "OMS-NDR-NOCOURIER-1")
    ndr = NDR(
        shipment_id=shipment.id,
        order_id=shipment.order_id,
        status=NDRStatus.OPEN,
        source_system="shiprocket",
    )
    db_session.add(ndr)
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["ndr.read"]
    ) as auth_client:
        response = await auth_client.get("/api/v1/ndr")
        assert response.status_code == 200
        row = response.json()["data"][0]
        assert row["awb"] == "AWB-OMS-NDR-NOCOURIER-1"
        assert row["courier_name"] is None


async def test_ndr_search_matches_order_number_customer_and_product(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    match_shipment = await _create_enriched_shipment(
        db_session, "OMS-NDR-SEARCH-1", customer_name="Priya Nair", product_name="Turmeric 30ct"
    )
    other_shipment = await _create_enriched_shipment(
        db_session, "OMS-NDR-OTHER-1", customer_name="Rahul Iyer", product_name="Neem Capsules"
    )
    for shipment in (match_shipment, other_shipment):
        db_session.add(
            NDR(
                shipment_id=shipment.id,
                order_id=shipment.order_id,
                status=NDRStatus.OPEN,
                source_system="shiprocket",
            )
        )
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["ndr.read"]
    ) as auth_client:
        for query in ("OMS-NDR-SEARCH-1", "Priya", "Turmeric"):
            response = await auth_client.get("/api/v1/ndr", params={"q": query})
            order_numbers = [row["order_number"] for row in response.json()["data"]]
            assert order_numbers == ["OMS-NDR-SEARCH-1"], (query, order_numbers)


async def test_ndr_filters_by_payment_type(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    cod_shipment = await _create_enriched_shipment(
        db_session, "OMS-NDR-COD-1", payment_type="cod"
    )
    prepaid_shipment = await _create_enriched_shipment(
        db_session, "OMS-NDR-PRE-1", payment_type="prepaid"
    )
    for shipment in (cod_shipment, prepaid_shipment):
        db_session.add(
            NDR(
                shipment_id=shipment.id,
                order_id=shipment.order_id,
                status=NDRStatus.OPEN,
                source_system="shiprocket",
            )
        )
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["ndr.read"]
    ) as auth_client:
        response = await auth_client.get("/api/v1/ndr", params={"payment_type": "cod"})
        order_numbers = [row["order_number"] for row in response.json()["data"]]
        assert order_numbers == ["OMS-NDR-COD-1"]


async def test_ndr_filters_by_status(db_session: AsyncSession, make_authenticated_client) -> None:
    open_shipment = await _create_enriched_shipment(db_session, "OMS-NDR-OPEN-1")
    resolved_shipment = await _create_enriched_shipment(db_session, "OMS-NDR-RES-1")
    db_session.add(
        NDR(
            shipment_id=open_shipment.id,
            order_id=open_shipment.order_id,
            status=NDRStatus.OPEN,
            source_system="shiprocket",
        )
    )
    db_session.add(
        NDR(
            shipment_id=resolved_shipment.id,
            order_id=resolved_shipment.order_id,
            status=NDRStatus.RESOLVED,
            source_system="shiprocket",
        )
    )
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["ndr.read"]
    ) as auth_client:
        response = await auth_client.get("/api/v1/ndr", params={"status": "resolved"})
        order_numbers = [row["order_number"] for row in response.json()["data"]]
        assert order_numbers == ["OMS-NDR-RES-1"]


async def test_ndr_filters_by_date_range_on_created_at(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    shipment = await _create_enriched_shipment(db_session, "OMS-NDR-DATE-1")
    ndr = NDR(
        shipment_id=shipment.id,
        order_id=shipment.order_id,
        status=NDRStatus.OPEN,
        source_system="shiprocket",
    )
    db_session.add(ndr)
    await db_session.commit()
    await db_session.refresh(ndr)

    async with await make_authenticated_client(
        db_session, permission_codes=["ndr.read"]
    ) as auth_client:
        now = ndr.created_at
        in_range = await auth_client.get(
            "/api/v1/ndr",
            params={
                "date_from": (now - timedelta(days=1)).isoformat(),
                "date_to": (now + timedelta(days=1)).isoformat(),
            },
        )
        assert [row["id"] for row in in_range.json()["data"]] == [str(ndr.id)]

        out_of_range = await auth_client.get(
            "/api/v1/ndr",
            params={
                "date_from": (now + timedelta(days=1)).isoformat(),
                "date_to": (now + timedelta(days=2)).isoformat(),
            },
        )
        assert out_of_range.json()["data"] == []


async def test_ndr_pagination_respects_filters(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    for i in range(3):
        shipment = await _create_enriched_shipment(db_session, f"OMS-NDR-PAGE-{i}")
        db_session.add(
            NDR(
                shipment_id=shipment.id,
                order_id=shipment.order_id,
                status=NDRStatus.OPEN,
                source_system="shiprocket",
            )
        )
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["ndr.read"]
    ) as auth_client:
        page1 = await auth_client.get("/api/v1/ndr", params={"page": 1, "page_size": 2})
        assert page1.json()["meta"]["total_items"] == 3
        assert len(page1.json()["data"]) == 2

        page2 = await auth_client.get("/api/v1/ndr", params={"page": 2, "page_size": 2})
        assert len(page2.json()["data"]) == 1


# --- RTO: enriched response / search / filters ------------------------------


async def test_rto_list_returns_enriched_order_customer_and_product_data(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    shipment = await _create_enriched_shipment(
        db_session, "OMS-RTO-ENR-1", shipment_status="rto_initiated", courier_name="Xpressbees"
    )
    rto = RTO(
        shipment_id=shipment.id,
        order_id=shipment.order_id,
        courier_id=shipment.courier_id,
        reason="Refused by customer",
        status=RTOStatus.INITIATED,
        source_system="shiprocket",
    )
    db_session.add(rto)
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["rto.read"]
    ) as auth_client:
        response = await auth_client.get("/api/v1/rto")
        assert response.status_code == 200
        row = response.json()["data"][0]
        assert row["order_number"] == "OMS-RTO-ENR-1"
        assert row["customer_name"] == "Ananya Rao"
        assert row["customer_phone"] == "9998887776"
        assert row["product"] == "Ashwagandha 60ct"
        assert row["order_amount"] == "649.00"
        assert row["payment_type"] == "prepaid"
        assert row["shipment_status"] == "rto_initiated"
        assert row["awb"] == "AWB-OMS-RTO-ENR-1"
        assert row["courier_name"] == "Xpressbees"


async def test_rto_list_handles_null_reason_without_fabricating(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """RTO rows derived from a tracking event (see `sync.py`'s
    `apply_tracking_event`) can legitimately have no reason — the API
    must return `null`, never invent placeholder text.
    """
    shipment = await _create_enriched_shipment(db_session, "OMS-RTO-NOREASON-1")
    rto = RTO(
        shipment_id=shipment.id,
        order_id=shipment.order_id,
        status=RTOStatus.INITIATED,
        source_system="shiprocket",
    )
    db_session.add(rto)
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["rto.read"]
    ) as auth_client:
        response = await auth_client.get("/api/v1/rto")
        assert response.status_code == 200
        row = response.json()["data"][0]
        assert row["reason"] is None
        assert row["external_reason"] is None
        assert row["awb"] == "AWB-OMS-RTO-NOREASON-1"
        assert row["courier_name"] is None


async def test_rto_search_matches_order_number_customer_and_product(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    match_shipment = await _create_enriched_shipment(
        db_session, "OMS-RTO-SEARCH-1", customer_name="Priya Nair", product_name="Turmeric 30ct"
    )
    other_shipment = await _create_enriched_shipment(
        db_session, "OMS-RTO-OTHER-1", customer_name="Rahul Iyer", product_name="Neem Capsules"
    )
    for shipment in (match_shipment, other_shipment):
        db_session.add(
            RTO(
                shipment_id=shipment.id,
                order_id=shipment.order_id,
                status=RTOStatus.INITIATED,
                source_system="shiprocket",
            )
        )
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["rto.read"]
    ) as auth_client:
        for query in ("OMS-RTO-SEARCH-1", "Priya", "Turmeric"):
            response = await auth_client.get("/api/v1/rto", params={"q": query})
            order_numbers = [row["order_number"] for row in response.json()["data"]]
            assert order_numbers == ["OMS-RTO-SEARCH-1"], (query, order_numbers)


async def test_rto_filters_by_payment_type(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    cod_shipment = await _create_enriched_shipment(
        db_session, "OMS-RTO-COD-1", payment_type="cod"
    )
    prepaid_shipment = await _create_enriched_shipment(
        db_session, "OMS-RTO-PRE-1", payment_type="prepaid"
    )
    for shipment in (cod_shipment, prepaid_shipment):
        db_session.add(
            RTO(
                shipment_id=shipment.id,
                order_id=shipment.order_id,
                status=RTOStatus.INITIATED,
                source_system="shiprocket",
            )
        )
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["rto.read"]
    ) as auth_client:
        response = await auth_client.get("/api/v1/rto", params={"payment_type": "cod"})
        order_numbers = [row["order_number"] for row in response.json()["data"]]
        assert order_numbers == ["OMS-RTO-COD-1"]


async def test_rto_filters_by_status(db_session: AsyncSession, make_authenticated_client) -> None:
    initiated_shipment = await _create_enriched_shipment(db_session, "OMS-RTO-INIT-1")
    received_shipment = await _create_enriched_shipment(db_session, "OMS-RTO-RECV-1")
    db_session.add(
        RTO(
            shipment_id=initiated_shipment.id,
            order_id=initiated_shipment.order_id,
            status=RTOStatus.INITIATED,
            source_system="shiprocket",
        )
    )
    db_session.add(
        RTO(
            shipment_id=received_shipment.id,
            order_id=received_shipment.order_id,
            status=RTOStatus.RECEIVED,
            source_system="shiprocket",
        )
    )
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["rto.read"]
    ) as auth_client:
        response = await auth_client.get("/api/v1/rto", params={"status": "received"})
        order_numbers = [row["order_number"] for row in response.json()["data"]]
        assert order_numbers == ["OMS-RTO-RECV-1"]


async def test_rto_filters_by_date_range_on_created_at(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    shipment = await _create_enriched_shipment(db_session, "OMS-RTO-DATE-1")
    rto = RTO(
        shipment_id=shipment.id,
        order_id=shipment.order_id,
        status=RTOStatus.INITIATED,
        source_system="shiprocket",
    )
    db_session.add(rto)
    await db_session.commit()
    await db_session.refresh(rto)

    async with await make_authenticated_client(
        db_session, permission_codes=["rto.read"]
    ) as auth_client:
        now = rto.created_at
        in_range = await auth_client.get(
            "/api/v1/rto",
            params={
                "date_from": (now - timedelta(days=1)).isoformat(),
                "date_to": (now + timedelta(days=1)).isoformat(),
            },
        )
        assert [row["id"] for row in in_range.json()["data"]] == [str(rto.id)]

        out_of_range = await auth_client.get(
            "/api/v1/rto",
            params={
                "date_from": (now + timedelta(days=1)).isoformat(),
                "date_to": (now + timedelta(days=2)).isoformat(),
            },
        )
        assert out_of_range.json()["data"] == []
