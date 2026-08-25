"""NDR/RTO have no POST endpoint (spec §36 — creation is Phase 2 sync
work), so fixtures are created directly via the repository layer, same as
Phase 2's sync adapters will.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.models.enums import NDRStatus, RTOStatus
from app.models.ndr import NDR
from app.models.order import Order
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
