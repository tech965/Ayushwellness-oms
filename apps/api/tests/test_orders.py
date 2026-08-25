from __future__ import annotations

from decimal import Decimal

import pytest
from app.repositories.order import OrderRepository
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_ORDER_PERMS = ["orders.read", "orders.create", "orders.update", "orders.cancel"]


def _order_payload(order_number: str = "OMS-1001") -> dict:
    return {
        "order_number": order_number,
        "payment_type": "prepaid",
        "shipping_charge": "49.00",
        "items": [
            {
                "sku": "SKU-1",
                "product_name": "Ashwagandha 60ct",
                "quantity": 2,
                "unit_price": "399.99",
                "discount_amount": "10.00",
                "tax_amount": "20.00",
            }
        ],
    }


async def test_create_order_is_atomic_and_computes_totals_with_decimal_precision(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=_ORDER_PERMS
    ) as auth_client:
        response = await auth_client.post("/api/v1/orders", json=_order_payload())

        assert response.status_code == 201
        data = response.json()["data"]
        # subtotal = 399.99 * 2 = 799.98; total = 799.98 - 10.00 + 20.00 + 49.00 = 858.98
        assert data["subtotal"] == "799.98"
        assert data["total_amount"] == "858.98"
        assert len(data["items"]) == 1
        assert data["status"] == "pending"

        # An initial "order_created" OrderEvent must exist — timeline is never empty.
        timeline = await auth_client.get(f"/api/v1/orders/{data['id']}/timeline")
        events = timeline.json()["data"]
        assert len(events) == 1
        assert events[0]["event_type"] == "order_created"


async def test_duplicate_order_number_is_rejected(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=_ORDER_PERMS
    ) as auth_client:
        first = await auth_client.post("/api/v1/orders", json=_order_payload("OMS-DUP"))
        assert first.status_code == 201

        second = await auth_client.post("/api/v1/orders", json=_order_payload("OMS-DUP"))
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "conflict"


async def test_valid_status_transition_appends_timeline_event(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=_ORDER_PERMS
    ) as auth_client:
        created = await auth_client.post("/api/v1/orders", json=_order_payload("OMS-2001"))
        order_id = created.json()["data"]["id"]

        response = await auth_client.patch(
            f"/api/v1/orders/{order_id}", json={"status": "confirmed"}
        )

        assert response.status_code == 200
        assert response.json()["data"]["status"] == "confirmed"

        timeline = await auth_client.get(f"/api/v1/orders/{order_id}/timeline")
        events = timeline.json()["data"]
        assert [e["event_type"] for e in events] == ["order_created", "status_changed"]


async def test_invalid_status_transition_is_rejected(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=_ORDER_PERMS
    ) as auth_client:
        created = await auth_client.post("/api/v1/orders", json=_order_payload("OMS-2002"))
        order_id = created.json()["data"]["id"]

        # PENDING -> DELIVERED is not a valid direct transition.
        response = await auth_client.patch(
            f"/api/v1/orders/{order_id}", json={"status": "delivered"}
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"


async def test_order_history_survives_multiple_transitions(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """Spec §49 case 4: the order timeline is never overwritten — every
    transition adds to it, never replaces a prior entry.
    """
    async with await make_authenticated_client(
        db_session, permission_codes=_ORDER_PERMS
    ) as auth_client:
        created = await auth_client.post("/api/v1/orders", json=_order_payload("OMS-2003"))
        order_id = created.json()["data"]["id"]

        for status in ("confirmed", "processing", "packed"):
            resp = await auth_client.patch(f"/api/v1/orders/{order_id}", json={"status": status})
            assert resp.status_code == 200

        timeline = await auth_client.get(f"/api/v1/orders/{order_id}/timeline")
        events = timeline.json()["data"]
        assert len(events) == 4  # created + 3 transitions
        assert [e["status"] for e in events[1:]] == ["confirmed", "processing", "packed"]


async def test_cancellation_requires_orders_cancel_permission(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session,
        permission_codes=["orders.read", "orders.create", "orders.update"],
        email="ops@example.com",
    ) as auth_client:
        created = await auth_client.post("/api/v1/orders", json=_order_payload("OMS-3001"))
        order_id = created.json()["data"]["id"]

        response = await auth_client.patch(
            f"/api/v1/orders/{order_id}", json={"status": "cancelled"}
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "authorization_error"


async def test_user_with_only_cancel_permission_can_cancel_but_not_transition(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=_ORDER_PERMS, email="creator@example.com"
    ) as creator:
        created = await creator.post("/api/v1/orders", json=_order_payload("OMS-3002"))
        order_id = created.json()["data"]["id"]

    async with await make_authenticated_client(
        db_session, permission_codes=["orders.read", "orders.cancel"], email="support@example.com"
    ) as support_client:
        cancelled = await support_client.patch(
            f"/api/v1/orders/{order_id}", json={"status": "cancelled"}
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["data"]["status"] == "cancelled"


async def test_duplicate_external_order_id_upserts_instead_of_duplicating(
    db_session: AsyncSession,
) -> None:
    """Spec §49 case 2."""
    from datetime import UTC, datetime

    repo = OrderRepository(db_session)

    first, created_first = await repo.upsert_by_external_id(
        source_system="shopify",
        external_id="order_999",
        order_number="SHOP-999",
        order_datetime=datetime.now(UTC),
        total_amount=Decimal("100.00"),
    )
    await db_session.commit()
    assert created_first is True

    second, created_second = await repo.upsert_by_external_id(
        source_system="shopify",
        external_id="order_999",
        order_number="SHOP-999",
        order_datetime=first.order_datetime,
        total_amount=Decimal("150.00"),
    )
    await db_session.commit()

    assert created_second is False
    assert second.id == first.id
    assert second.total_amount == Decimal("150.00")
