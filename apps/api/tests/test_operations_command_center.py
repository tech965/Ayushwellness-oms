from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.models.enums import FulfillmentStatus, PaymentType, ShipmentStatus
from app.models.order import Order, OrderItem
from app.models.shipment import Shipment
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_PERMS = ["analytics.read"]


async def _make_order(
    db_session: AsyncSession,
    *,
    order_number: str,
    amount: str,
    payment_type: PaymentType = PaymentType.PREPAID,
    fulfillment_status: FulfillmentStatus = FulfillmentStatus.UNFULFILLED,
    state: str | None = None,
) -> Order:
    order = Order(
        order_number=order_number,
        order_datetime=datetime.now(UTC),
        total_amount=Decimal(amount),
        payment_type=payment_type,
        fulfillment_status=fulfillment_status,
        source_system="manual",
        shipping_address={"city": "Test City", "state": state} if state else None,
    )
    db_session.add(order)
    await db_session.flush()
    db_session.add(
        OrderItem(
            order_id=order.id,
            sku="SKU-1",
            product_name="Ashwagandha 60ct",
            quantity=1,
            unit_price=Decimal(amount),
            total_amount=Decimal(amount),
            source_system="manual",
        )
    )
    await db_session.commit()
    await db_session.refresh(order)
    return order


async def test_command_center_summary_and_health_reflect_real_orders(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    await _make_order(
        db_session,
        order_number="OMS-OCC-1",
        amount="500.00",
        payment_type=PaymentType.COD,
        fulfillment_status=FulfillmentStatus.UNFULFILLED,
        state="Maharashtra",
    )
    await _make_order(
        db_session,
        order_number="OMS-OCC-2",
        amount="300.00",
        payment_type=PaymentType.PREPAID,
        fulfillment_status=FulfillmentStatus.FULFILLED,
        state="Maharashtra",
    )

    async with await make_authenticated_client(
        db_session, permission_codes=_PERMS
    ) as auth_client:
        response = await auth_client.get("/api/v1/analytics/operations-command-center")

        assert response.status_code == 200
        data = response.json()["data"]

        assert data["summary"]["total_orders"] == 2
        assert data["summary"]["total_revenue"] == "800.00"

        # No orders in the prior period -> undefined growth, not fabricated.
        assert data["summary"]["orders_growth_pct"] is None

        attention_by_type = {item["type"]: item for item in data["attention_items"]}
        assert attention_by_type["unfulfilled_orders"]["count"] == 1
        assert attention_by_type["cod_pending_fulfillment"]["count"] == 1

        orders_health = {m["label"]: m["value"] for m in data["operations_health"]["orders"]}
        assert orders_health["Fulfilled"] == 1
        assert orders_health["Unfulfilled"] == 1


async def test_command_center_pending_shipment_and_rto_metrics(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    order = await _make_order(db_session, order_number="OMS-OCC-SHIP", amount="100.00")
    db_session.add(
        Shipment(
            order_id=order.id,
            current_status=ShipmentStatus.PENDING,
            source_system="manual",
        )
    )
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=_PERMS
    ) as auth_client:
        response = await auth_client.get("/api/v1/analytics/operations-command-center")

        assert response.status_code == 200
        data = response.json()["data"]
        shipments_health = {
            m["label"]: m["value"] for m in data["operations_health"]["shipments"]
        }
        assert shipments_health["Pending"] == 1

        attention_by_type = {item["type"]: item for item in data["attention_items"]}
        assert attention_by_type["shipment_pending"]["count"] == 1

        messages = [i["message"] for i in data["insights"]]
        assert any("pending processing" in m for m in messages)


async def test_command_center_business_opportunities_use_real_state_data(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    await _make_order(
        db_session, order_number="OMS-OCC-STATE-1", amount="1000.00", state="Karnataka"
    )

    async with await make_authenticated_client(
        db_session, permission_codes=_PERMS
    ) as auth_client:
        response = await auth_client.get("/api/v1/analytics/operations-command-center")

        assert response.status_code == 200
        data = response.json()["data"]
        opportunities_by_type = {o["type"]: o for o in data["business_opportunities"]}
        assert "Karnataka" in opportunities_by_type["top_revenue_state"]["description"]


async def test_command_center_reports_not_enough_data_when_empty(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=_PERMS
    ) as auth_client:
        response = await auth_client.get("/api/v1/analytics/operations-command-center")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["summary"]["total_orders"] == 0
        opportunities_by_type = {o["type"]: o for o in data["business_opportunities"]}
        assert opportunities_by_type["top_revenue_state"]["description"] == "Not enough data yet"
        assert data["insights"] == []


async def test_command_center_requires_analytics_permission(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=["orders.read"]
    ) as auth_client:
        response = await auth_client.get("/api/v1/analytics/operations-command-center")
        assert response.status_code == 403
