from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.models.order import Order
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_PERMS = ["returns.read", "returns.update", "refunds.read"]


async def _create_order(db_session: AsyncSession, order_number: str) -> Order:
    order = Order(
        order_number=order_number,
        order_datetime=datetime.now(UTC),
        total_amount=Decimal("500.00"),
        source_system="manual",
    )
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)
    return order


async def test_completing_a_return_creates_a_refund(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    order = await _create_order(db_session, "OMS-RET-1")

    async with await make_authenticated_client(db_session, permission_codes=_PERMS) as auth_client:
        created = await auth_client.post(
            "/api/v1/returns", json={"order_id": str(order.id), "reason": "Damaged", "quantity": 1}
        )
        assert created.status_code == 201
        return_id = created.json()["data"]["id"]
        assert created.json()["data"]["status"] == "requested"

        approved = await auth_client.patch(
            f"/api/v1/returns/{return_id}", json={"status": "approved"}
        )
        assert approved.status_code == 200

        completed = await auth_client.patch(
            f"/api/v1/returns/{return_id}", json={"status": "completed"}
        )
        assert completed.status_code == 200
        assert completed.json()["data"]["completed_at"] is not None

        refunds = await auth_client.get("/api/v1/refunds")
        assert refunds.status_code == 200
        matching = [r for r in refunds.json()["data"] if r["return_id"] == return_id]
        assert len(matching) == 1
        assert matching[0]["amount"] == "500.00"
        assert matching[0]["status"] == "pending"


async def test_completing_a_return_twice_does_not_duplicate_refund(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    order = await _create_order(db_session, "OMS-RET-2")

    async with await make_authenticated_client(db_session, permission_codes=_PERMS) as auth_client:
        created = await auth_client.post(
            "/api/v1/returns",
            json={"order_id": str(order.id), "reason": "Wrong item", "quantity": 1},
        )
        return_id = created.json()["data"]["id"]

        await auth_client.patch(f"/api/v1/returns/{return_id}", json={"status": "completed"})
        # Re-sending the same terminal status must stay idempotent.
        await auth_client.patch(f"/api/v1/returns/{return_id}", json={"status": "completed"})

        refunds = await auth_client.get("/api/v1/refunds")
        matching = [r for r in refunds.json()["data"] if r["return_id"] == return_id]
        assert len(matching) == 1
