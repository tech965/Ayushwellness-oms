from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.models.enums import ShipmentStatus
from app.models.order import Order, OrderItem
from app.models.shipment import Shipment
from app.services.supply_intelligence_service import normalize_state
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_PERMS = ["analytics.read"]


async def _make_order(
    db_session: AsyncSession,
    *,
    order_number: str,
    state: str | None,
    city: str | None,
    amount: str,
    order_datetime: datetime,
    sku: str = "SKU-1",
    product_name: str = "Ashwagandha 60ct",
    quantity: int = 1,
) -> Order:
    shipping_address = (
        {"line1": "1 MG Road", "city": city, "state": state, "country": "India", "pin_code": "400001"}
        if state or city
        else None
    )
    order = Order(
        order_number=order_number,
        order_datetime=order_datetime,
        total_amount=Decimal(amount),
        source_system="manual",
        shipping_address=shipping_address,
    )
    db_session.add(order)
    await db_session.flush()
    db_session.add(
        OrderItem(
            order_id=order.id,
            sku=sku,
            product_name=product_name,
            quantity=quantity,
            unit_price=Decimal(amount),
            total_amount=Decimal(amount),
            source_system="manual",
        )
    )
    await db_session.commit()
    await db_session.refresh(order)
    return order


async def test_normalize_state_handles_aliases_and_unknown_values() -> None:
    assert normalize_state("Maharashtra") == "Maharashtra"
    assert normalize_state("maharashtra") == "Maharashtra"
    assert normalize_state("Orissa") == "Odisha"
    assert normalize_state("NCT of Delhi") == "Delhi"
    assert normalize_state(None) is None
    assert normalize_state("") is None
    assert normalize_state("Narnia") is None


async def test_supply_intelligence_aggregates_orders_by_state(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    now = datetime.now(UTC)
    await _make_order(
        db_session,
        order_number="OMS-SI-1",
        state="Maharashtra",
        city="Mumbai",
        amount="1000.00",
        order_datetime=now,
    )
    await _make_order(
        db_session,
        order_number="OMS-SI-2",
        state="Orissa",  # old name -- must merge into "Odisha" bucket
        city="Bhubaneswar",
        amount="500.00",
        order_datetime=now,
    )
    await _make_order(
        db_session,
        order_number="OMS-SI-3",
        state="Odisha",
        city="Bhubaneswar",
        amount="250.00",
        order_datetime=now,
    )
    await _make_order(
        db_session,
        order_number="OMS-SI-UNMAPPED",
        state="Narnia",
        city="Nowhere",
        amount="99.00",
        order_datetime=now,
    )
    await _make_order(
        db_session,
        order_number="OMS-SI-NOADDR",
        state=None,
        city=None,
        amount="10.00",
        order_datetime=now,
    )

    async with await make_authenticated_client(
        db_session, permission_codes=_PERMS
    ) as auth_client:
        response = await auth_client.get("/api/v1/analytics/supply-intelligence")

        assert response.status_code == 200
        data = response.json()["data"]

        states_by_name = {s["state"]: s for s in data["states"]}
        assert states_by_name["Maharashtra"]["orders"] == 1
        assert states_by_name["Maharashtra"]["revenue"] == "1000.00"
        # "Orissa" and "Odisha" merge into one canonical bucket.
        assert states_by_name["Odisha"]["orders"] == 2
        assert states_by_name["Odisha"]["revenue"] == "750.00"
        # Every canonical state/UT is present, even with zero orders.
        assert "Kerala" in states_by_name
        assert states_by_name["Kerala"]["orders"] == 0
        assert states_by_name["Kerala"]["opportunity"] == "untapped"

        # "Narnia" and the address-less order are neither fabricated into
        # a state nor silently dropped -- they're disclosed.
        assert data["unmapped_order_count"] == 2

        assert data["summary"]["total_orders"] == 3  # Maharashtra(1) + Odisha(2)
        assert data["summary"]["active_states"] == 2
        assert data["summary"]["top_state"] == "Odisha"
        assert data["summary"]["top_revenue_state"] == "Maharashtra"


async def test_supply_intelligence_state_detail_includes_cities_and_products(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    now = datetime.now(UTC)
    await _make_order(
        db_session,
        order_number="OMS-SI-DETAIL-1",
        state="Karnataka",
        city="Bengaluru",
        amount="600.00",
        order_datetime=now,
        sku="SKU-A",
        product_name="Chyawanprash",
        quantity=2,
    )
    await _make_order(
        db_session,
        order_number="OMS-SI-DETAIL-2",
        state="Karnataka",
        city="Mysuru",
        amount="400.00",
        order_datetime=now,
        sku="SKU-B",
        product_name="Triphala",
        quantity=1,
    )

    async with await make_authenticated_client(
        db_session, permission_codes=_PERMS
    ) as auth_client:
        response = await auth_client.get(
            "/api/v1/analytics/supply-intelligence", params={"state": "Karnataka"}
        )

        assert response.status_code == 200
        detail = response.json()["data"]["selected_state"]
        assert detail["state"] == "Karnataka"
        assert detail["orders"] == 2
        assert detail["revenue"] == "1000.00"
        assert detail["avg_order_value"] == "500.00"

        cities = {c["city"]: c["orders"] for c in detail["cities"]}
        assert cities == {"Bengaluru": 1, "Mysuru": 1}

        products = {p["sku"]: p for p in detail["products"]}
        assert products["SKU-A"]["quantity"] == 2
        assert products["SKU-B"]["quantity"] == 1


async def test_supply_intelligence_computes_rto_rate_and_growth(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    now = datetime.now(UTC)
    thirty_five_days_ago = now - timedelta(days=35)

    # Previous comparable period (further back than the default 30-day
    # window, so it falls in the auto-computed "previous period").
    await _make_order(
        db_session,
        order_number="OMS-SI-PREV",
        state="Gujarat",
        city="Ahmedabad",
        amount="100.00",
        order_datetime=thirty_five_days_ago,
    )

    order = await _make_order(
        db_session,
        order_number="OMS-SI-CURRENT-1",
        state="Gujarat",
        city="Ahmedabad",
        amount="100.00",
        order_datetime=now,
    )
    order2 = await _make_order(
        db_session,
        order_number="OMS-SI-CURRENT-2",
        state="Gujarat",
        city="Surat",
        amount="100.00",
        order_datetime=now,
    )
    db_session.add_all(
        [
            Shipment(
                order_id=order.id,
                current_status=ShipmentStatus.RTO_INITIATED,
                source_system="manual",
            ),
            Shipment(
                order_id=order2.id,
                current_status=ShipmentStatus.DELIVERED,
                source_system="manual",
            ),
        ]
    )
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=_PERMS
    ) as auth_client:
        response = await auth_client.get("/api/v1/analytics/supply-intelligence")

        assert response.status_code == 200
        states_by_name = {s["state"]: s for s in response.json()["data"]["states"]}
        gujarat = states_by_name["Gujarat"]
        assert gujarat["orders"] == 2
        assert gujarat["rto"] == 1
        assert gujarat["delivered"] == 1
        assert gujarat["rto_rate_pct"] == pytest.approx(50.0)
        # 2 current vs 1 previous -> +100% growth.
        assert gujarat["growth_pct"] == pytest.approx(100.0)


async def test_supply_intelligence_requires_analytics_permission(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=["orders.read"]
    ) as auth_client:
        response = await auth_client.get("/api/v1/analytics/supply-intelligence")
        assert response.status_code == 403
