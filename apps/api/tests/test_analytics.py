from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_ANALYTICS_PERMS = ["analytics.read", "orders.read", "orders.create"]


async def _create_order(auth_client, order_number: str) -> None:
    payload = {
        "order_number": order_number,
        "payment_type": "prepaid",
        "shipping_charge": "0",
        "items": [
            {
                "sku": "SKU-1",
                "product_name": "Ashwagandha 60ct",
                "quantity": 1,
                "unit_price": "649.00",
            }
        ],
    }
    response = await auth_client.post("/api/v1/orders", json=payload)
    assert response.status_code == 201


async def test_analytics_summary_reflects_orders_in_range(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=_ANALYTICS_PERMS
    ) as auth_client:
        await _create_order(auth_client, "OMS-A-1")
        await _create_order(auth_client, "OMS-A-2")

        response = await auth_client.get("/api/v1/analytics/summary")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["total_orders"]["current"] == "2"
        assert data["total_revenue"]["current"] == "1298.00"
        # No orders in the prior period -> undefined %, not a crash.
        assert data["total_orders"]["change_pct"] is None


async def test_analytics_breakdowns_use_real_enum_values(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=_ANALYTICS_PERMS
    ) as auth_client:
        await _create_order(auth_client, "OMS-B-1")

        response = await auth_client.get("/api/v1/analytics/breakdowns")

        assert response.status_code == 200
        data = response.json()["data"]
        order_statuses = {row["status"] for row in data["order_status"]}
        assert order_statuses <= {
            "pending",
            "confirmed",
            "processing",
            "packed",
            "shipped",
            "delivered",
            "cancelled",
        }


async def test_analytics_top_products_and_recent_activity_smoke(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=_ANALYTICS_PERMS
    ) as auth_client:
        await _create_order(auth_client, "OMS-C-1")

        top_products = await auth_client.get("/api/v1/analytics/top-products")
        assert top_products.status_code == 200
        products = top_products.json()["data"]
        assert products[0]["sku"] == "SKU-1"
        assert products[0]["units_sold"] == 1

        recent = await auth_client.get("/api/v1/analytics/recent-activity")
        assert recent.status_code == 200
        assert len(recent.json()["data"]["recent_orders"]) == 1

        timeseries = await auth_client.get(
            "/api/v1/analytics/orders-timeseries", params={"interval": "day"}
        )
        assert timeseries.status_code == 200
        assert sum(p["order_count"] for p in timeseries.json()["data"]["points"]) == 1

        couriers = await auth_client.get("/api/v1/analytics/couriers")
        assert couriers.status_code == 200
        assert couriers.json()["data"] == []
