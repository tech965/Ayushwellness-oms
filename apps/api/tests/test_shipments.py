from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.models.order import Order
from app.services.shipment_service import ShipmentService
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_SHIPMENT_PERMS = ["shipments.read", "shipments.update", "orders.read", "orders.create"]


async def _create_order(db_session: AsyncSession, order_number: str) -> Order:
    order = Order(
        order_number=order_number,
        order_datetime=datetime.now(UTC),
        source_system="manual",
    )
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)
    return order


async def test_shipment_create_and_timeline(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    order = await _create_order(db_session, "OMS-SHIP-1")

    async with await make_authenticated_client(
        db_session, permission_codes=_SHIPMENT_PERMS
    ) as auth_client:
        create = await auth_client.post(
            "/api/v1/shipments", json={"order_id": str(order.id), "awb": "AWB123"}
        )
        assert create.status_code == 201
        shipment_id = create.json()["data"]["id"]

        timeline = await auth_client.get(f"/api/v1/shipments/{shipment_id}/timeline")
        assert timeline.status_code == 200
        assert timeline.json()["data"] == []


async def test_duplicate_shipment_event_with_external_id_is_deduplicated(
    db_session: AsyncSession,
) -> None:
    """Spec §49 case 3 + §22: replaying the same external tracking event
    must not create a second ShipmentEvent row.
    """
    order = await _create_order(db_session, "OMS-SHIP-2")
    service = ShipmentService(db_session)
    shipment = await service.create_shipment(
        actor=None, order_id=order.id, awb="AWB456", courier_id=None, expected_delivery_date=None
    )

    ts = datetime.now(UTC)
    first_event, created_first = await service.add_tracking_event(
        shipment.id,
        external_event_id="evt-1",
        status="in_transit",
        location="Mumbai Hub",
        event_timestamp=ts,
        description="Departed hub",
        courier_name="Delhivery",
        source="webhook",
    )
    assert created_first is True

    second_event, created_second = await service.add_tracking_event(
        shipment.id,
        external_event_id="evt-1",
        status="in_transit",
        location="Mumbai Hub",
        event_timestamp=ts,
        description="Departed hub (retry)",
        courier_name="Delhivery",
        source="webhook",
    )
    assert created_second is False
    assert second_event.id == first_event.id

    timeline = await service.get_timeline(shipment.id)
    assert len(timeline) == 1


async def test_shipment_event_history_is_never_overwritten(db_session: AsyncSession) -> None:
    """Spec §21/§57: Shipment holds current state; ShipmentEvent
    accumulates history — every distinct event must be preserved.
    """
    order = await _create_order(db_session, "OMS-SHIP-3")
    service = ShipmentService(db_session)
    shipment = await service.create_shipment(
        actor=None, order_id=order.id, awb="AWB789", courier_id=None, expected_delivery_date=None
    )

    stages = ["picked_up", "in_transit", "out_for_delivery", "delivered"]
    for i, status in enumerate(stages):
        await service.add_tracking_event(
            shipment.id,
            external_event_id=f"evt-{i}",
            status=status,
            location=f"Hub {i}",
            event_timestamp=datetime.now(UTC),
            description=None,
            courier_name="Delhivery",
        )

    timeline = await service.get_timeline(shipment.id)
    assert [e.status for e in timeline] == stages

    # Current state reflects the latest event, but history is untouched.
    refreshed = await service.get_shipment(shipment.id)
    assert refreshed.current_location == "Hub 3"
    assert len(await service.get_timeline(shipment.id)) == 4


async def test_shipment_event_without_external_id_dedupes_by_status_and_timestamp(
    db_session: AsyncSession,
) -> None:
    order = await _create_order(db_session, "OMS-SHIP-4")
    service = ShipmentService(db_session)
    shipment = await service.create_shipment(
        actor=None, order_id=order.id, awb="AWB999", courier_id=None, expected_delivery_date=None
    )
    ts = datetime.now(UTC)

    _, created_first = await service.add_tracking_event(
        shipment.id,
        external_event_id=None,
        status="picked_up",
        location="Origin",
        event_timestamp=ts,
        description=None,
        courier_name=None,
    )
    _, created_second = await service.add_tracking_event(
        shipment.id,
        external_event_id=None,
        status="picked_up",
        location="Origin",
        event_timestamp=ts,
        description=None,
        courier_name=None,
    )

    assert created_first is True
    assert created_second is False
    assert len(await service.get_timeline(shipment.id)) == 1
