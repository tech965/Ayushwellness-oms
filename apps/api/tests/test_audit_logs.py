from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def test_order_status_change_writes_an_audit_log(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    perms = ["orders.read", "orders.create", "orders.update", "audit_logs.read"]
    async with await make_authenticated_client(db_session, permission_codes=perms) as auth_client:
        created = await auth_client.post(
            "/api/v1/orders",
            json={
                "order_number": "OMS-AUDIT-1",
                "payment_type": "cod",
                "items": [
                    {
                        "sku": "SKU-A",
                        "product_name": "Item A",
                        "quantity": 1,
                        "unit_price": "100.00",
                    }
                ],
            },
        )
        order_id = created.json()["data"]["id"]

        await auth_client.patch(f"/api/v1/orders/{order_id}", json={"status": "confirmed"})

        logs = await auth_client.get(
            "/api/v1/audit-logs", params={"entity_type": "order", "entity_id": order_id}
        )
        assert logs.status_code == 200
        actions = {entry["action"] for entry in logs.json()["data"]}
        assert "order.created" in actions
        assert "order.status_changed" in actions


async def test_audit_logs_require_permission(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=["orders.read"]
    ) as auth_client:
        response = await auth_client.get("/api/v1/audit-logs")
        assert response.status_code == 403
