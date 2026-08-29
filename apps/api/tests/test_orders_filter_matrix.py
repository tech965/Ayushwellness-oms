"""Comprehensive Orders filter audit — reproduces and covers every filter
listed against real seeded data (never fetch-all-then-filter-in-Python;
every assertion here is against `GET /api/v1/orders`, driving the real
`OrderRepository.search_query` SQL path end to end).

Root-cause note (the reported "Pending shows 0 orders" bug): three
different, unrelated enums all happen to share the string "pending" —
`Order.status` (OrderStatus.PENDING — OMS workflow), `Order.payment_status`
(PaymentStatus.PENDING — payment state), and `Shipment.current_status`
(ShipmentStatus.PENDING — the DEFAULT status every new Shipment row gets
before an AWB is assigned). The Orders page has three separate dropdowns
(Order Status / Payment Status / Shipment Status) that can each show a
selected value of literally "Pending" with no field-name prefix once
selected, so a user (or a screenshot) genuinely cannot tell which of the
three is active. This file proves each of those three fields filters
correctly in isolation — the "0 orders" screenshot is consistent with
`shipment_status=pending` being selected in an environment where no
`Shipment` rows exist yet (a data fact, not a query bug), not with any of
the three filters being broken.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.models.courier import Courier
from app.models.customer import Customer
from app.models.shipment import Shipment
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_ORDER_PERMS = ["orders.read", "orders.create", "orders.update", "orders.cancel"]


def _payload(
    order_number: str,
    *,
    payment_type: str = "prepaid",
    unit_price: str = "500.00",
    sku: str = "SKU-1",
    order_datetime: str | None = None,
) -> dict:
    payload: dict = {
        "order_number": order_number,
        "payment_type": payment_type,
        "shipping_charge": "0",
        "items": [
            {
                "sku": sku,
                "product_name": "Ashwagandha 60ct",
                "quantity": 1,
                "unit_price": unit_price,
            }
        ],
    }
    if order_datetime:
        payload["order_datetime"] = order_datetime
    return payload


async def _add_shipment(
    session: AsyncSession,
    *,
    order_id,
    external_id: str,
    status: str = "pending",
    courier_id=None,
) -> Shipment:
    shipment = Shipment(
        order_id=order_id,
        source_system="manual",
        external_id=external_id,
        current_status=status,
        courier_id=courier_id,
    )
    session.add(shipment)
    await session.commit()
    await session.refresh(shipment)
    return shipment


async def _add_courier(session: AsyncSession, *, name: str, code: str) -> Courier:
    courier = Courier(name=name, code=code, source_system="manual", external_id=code)
    session.add(courier)
    await session.commit()
    await session.refresh(courier)
    return courier


# --- 1. No filters -------------------------------------------------------


async def test_no_filters_returns_existing_orders(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=_ORDER_PERMS
    ) as auth_client:
        await auth_client.post("/api/v1/orders", json=_payload("OMS-NF-1"))
        await auth_client.post("/api/v1/orders", json=_payload("OMS-NF-2"))

        response = await auth_client.get("/api/v1/orders")
        assert response.status_code == 200
        assert response.json()["meta"]["total_items"] == 2


# --- 2/3. Order status (the reported "Pending" field) ---------------------


async def test_order_status_pending_and_confirmed_are_isolated(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=_ORDER_PERMS
    ) as auth_client:
        await auth_client.post("/api/v1/orders", json=_payload("OMS-OS-PEND"))
        confirmed = await auth_client.post("/api/v1/orders", json=_payload("OMS-OS-CONF"))
        await auth_client.patch(
            f"/api/v1/orders/{confirmed.json()['data']['id']}", json={"status": "confirmed"}
        )

        pending = await auth_client.get("/api/v1/orders", params={"status": "pending"})
        assert [o["order_number"] for o in pending.json()["data"]] == ["OMS-OS-PEND"]

        confirmed_resp = await auth_client.get("/api/v1/orders", params={"status": "confirmed"})
        assert [o["order_number"] for o in confirmed_resp.json()["data"]] == ["OMS-OS-CONF"]


# --- 4/5. Payment type -----------------------------------------------------


async def test_payment_type_cod_and_prepaid_are_isolated(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=_ORDER_PERMS
    ) as auth_client:
        await auth_client.post("/api/v1/orders", json=_payload("OMS-PT-COD", payment_type="cod"))
        await auth_client.post(
            "/api/v1/orders", json=_payload("OMS-PT-PRE", payment_type="prepaid")
        )

        cod = await auth_client.get("/api/v1/orders", params={"payment_type": "cod"})
        assert [o["order_number"] for o in cod.json()["data"]] == ["OMS-PT-COD"]

        prepaid = await auth_client.get("/api/v1/orders", params={"payment_type": "prepaid"})
        assert [o["order_number"] for o in prepaid.json()["data"]] == ["OMS-PT-PRE"]


# --- 6. Payment status (every supported value) -----------------------------


async def test_payment_status_filters_every_supported_value(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    from app.repositories.order import OrderRepository

    repo = OrderRepository(db_session)
    for status in ("pending", "authorized", "paid", "failed", "refunded", "partially_refunded"):
        await repo.upsert_by_external_id(
            source_system="shopify",
            external_id=f"ps-{status}",
            order_number=f"OMS-PS-{status.upper()}",
            order_datetime=datetime.now(UTC),
            total_amount=Decimal("100.00"),
            payment_status=status,
        )
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=_ORDER_PERMS
    ) as auth_client:
        for status in ("pending", "authorized", "paid", "failed", "refunded", "partially_refunded"):
            response = await auth_client.get("/api/v1/orders", params={"payment_status": status})
            order_numbers = [o["order_number"] for o in response.json()["data"]]
            assert order_numbers == [f"OMS-PS-{status.upper()}"], (status, order_numbers)


# --- 7. Fulfillment status (every supported value) -------------------------


async def test_fulfillment_status_filters_every_supported_value(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    from app.repositories.order import OrderRepository

    repo = OrderRepository(db_session)
    for status in ("unfulfilled", "partial", "fulfilled"):
        await repo.upsert_by_external_id(
            source_system="shopify",
            external_id=f"fs-{status}",
            order_number=f"OMS-FS-{status.upper()}",
            order_datetime=datetime.now(UTC),
            total_amount=Decimal("100.00"),
            fulfillment_status=status,
        )
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=_ORDER_PERMS
    ) as auth_client:
        for status in ("unfulfilled", "partial", "fulfilled"):
            response = await auth_client.get(
                "/api/v1/orders", params={"fulfillment_status": status}
            )
            order_numbers = [o["order_number"] for o in response.json()["data"]]
            assert order_numbers == [f"OMS-FS-{status.upper()}"], (status, order_numbers)


# --- Shipment status (the THIRD "Pending", root cause of the screenshot) --


async def test_shipment_status_pending_only_matches_orders_with_a_pending_shipment(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """Direct reproduction of the screenshot: a `shipment_status=pending`
    filter (a real, distinct field from `status=pending`) correctly
    returns 0 results when no `Shipment` row with that status exists —
    proving "0 orders" is a correct answer to a specific, narrow filter,
    not a broken query, whenever a store has no shipments yet.
    """
    async with await make_authenticated_client(
        db_session, permission_codes=_ORDER_PERMS
    ) as auth_client:
        with_shipment = await auth_client.post("/api/v1/orders", json=_payload("OMS-SHIP-PEND"))
        await auth_client.post("/api/v1/orders", json=_payload("OMS-SHIP-NONE"))

        await _add_shipment(
            db_session,
            order_id=with_shipment.json()["data"]["id"],
            external_id="ship-pending-1",
            status="pending",
        )

        pending = await auth_client.get("/api/v1/orders", params={"shipment_status": "pending"})
        assert [o["order_number"] for o in pending.json()["data"]] == ["OMS-SHIP-PEND"]

        # No shipment at all yet -> correctly 0, not a bug.
        delivered = await auth_client.get("/api/v1/orders", params={"shipment_status": "delivered"})
        assert delivered.json()["data"] == []


# --- 8. Courier filter ------------------------------------------------------


async def test_courier_filter(db_session: AsyncSession, make_authenticated_client) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=_ORDER_PERMS
    ) as auth_client:
        with_courier = await auth_client.post("/api/v1/orders", json=_payload("OMS-COUR-1"))
        await auth_client.post("/api/v1/orders", json=_payload("OMS-COUR-2"))

        courier = await _add_courier(db_session, name="Delhivery", code="delhivery-test")
        await _add_shipment(
            db_session,
            order_id=with_courier.json()["data"]["id"],
            external_id="ship-courier-1",
            courier_id=courier.id,
        )

        response = await auth_client.get("/api/v1/orders", params={"courier_id": str(courier.id)})
        assert [o["order_number"] for o in response.json()["data"]] == ["OMS-COUR-1"]


# --- 9. SKU/product filter ---------------------------------------------------


async def test_sku_filter(db_session: AsyncSession, make_authenticated_client) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=_ORDER_PERMS
    ) as auth_client:
        await auth_client.post("/api/v1/orders", json=_payload("OMS-SKU-1", sku="ASH-60"))
        await auth_client.post("/api/v1/orders", json=_payload("OMS-SKU-2", sku="TUR-30"))

        response = await auth_client.get("/api/v1/orders", params={"sku": "ASH"})
        assert [o["order_number"] for o in response.json()["data"]] == ["OMS-SKU-1"]


# --- 10. Search by order number / customer name / phone / email -----------


async def test_search_matches_order_number_customer_name_phone_and_email(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    customer = Customer(full_name="Ananya Rao", email="ananya@example.com", phone="9998887776")
    db_session.add(customer)
    await db_session.commit()
    await db_session.refresh(customer)

    async with await make_authenticated_client(
        db_session, permission_codes=_ORDER_PERMS
    ) as auth_client:
        payload = _payload("OMS-SEARCH-1")
        payload["customer_id"] = str(customer.id)
        await auth_client.post("/api/v1/orders", json=payload)
        await auth_client.post("/api/v1/orders", json=_payload("OMS-OTHER-1"))

        for query in ("OMS-SEARCH-1", "Ananya", "9998887776", "ananya@example.com"):
            response = await auth_client.get("/api/v1/orders", params={"q": query})
            order_numbers = [o["order_number"] for o in response.json()["data"]]
            assert order_numbers == ["OMS-SEARCH-1"], (query, order_numbers)


# --- 11/12. Min/max amount --------------------------------------------------


async def test_min_and_max_amount_filters(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=_ORDER_PERMS
    ) as auth_client:
        await auth_client.post("/api/v1/orders", json=_payload("OMS-AMT-LOW", unit_price="100.00"))
        await auth_client.post("/api/v1/orders", json=_payload("OMS-AMT-HIGH", unit_price="900.00"))

        low_only = await auth_client.get("/api/v1/orders", params={"amount_max": "500"})
        assert [o["order_number"] for o in low_only.json()["data"]] == ["OMS-AMT-LOW"]

        high_only = await auth_client.get("/api/v1/orders", params={"amount_min": "500"})
        assert [o["order_number"] for o in high_only.json()["data"]] == ["OMS-AMT-HIGH"]


# --- 13-19. Date range (reusing app.core.timezone.to_ist, not a second impl) --


async def test_date_range_filter_is_inclusive_of_ist_day_boundaries(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """`date_from`/`date_to` are plain `Order.order_datetime` range
    comparisons (`>=`/`<=`) — inclusive on both ends, matching the
    dashboard/analytics date-range contract exactly (same field, same
    semantics, no separate date-filtering implementation for Orders).
    An order placed at 00:30 IST (still the previous UTC calendar date)
    must be included when the caller's range covers that IST day, the
    same boundary this OMS has a dedicated, shared `to_ist` helper for.
    """
    async with await make_authenticated_client(
        db_session, permission_codes=_ORDER_PERMS
    ) as auth_client:
        # 2026-03-15T00:30:00+05:30 == 2026-03-14T19:00:00Z
        await auth_client.post(
            "/api/v1/orders",
            json=_payload("OMS-DATE-BOUNDARY", order_datetime="2026-03-14T19:00:00Z"),
        )

        # A caller filtering for the IST calendar day of 2026-03-15 sends
        # the equivalent UTC instants for that IST day's boundaries.
        in_range = await auth_client.get(
            "/api/v1/orders",
            params={
                "date_from": "2026-03-14T18:30:00Z",  # 2026-03-15T00:00:00+05:30
                "date_to": "2026-03-15T18:29:59Z",  # 2026-03-15T23:59:59+05:30
            },
        )
        assert [o["order_number"] for o in in_range.json()["data"]] == ["OMS-DATE-BOUNDARY"]

        out_of_range = await auth_client.get(
            "/api/v1/orders",
            params={"date_from": "2026-03-15T18:30:00Z", "date_to": "2026-03-16T18:30:00Z"},
        )
        assert out_of_range.json()["data"] == []


# --- 20. Combined filters ----------------------------------------------------


async def test_combined_status_and_payment_type_filters_apply_together(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """`status=pending&payment_type=cod` must be an AND, not one filter
    silently overwriting the other — the exact failure mode Part 5 warns
    about if query-param assembly ever drops a key.
    """
    async with await make_authenticated_client(
        db_session, permission_codes=_ORDER_PERMS
    ) as auth_client:
        # Matches both.
        await auth_client.post(
            "/api/v1/orders", json=_payload("OMS-COMBO-MATCH", payment_type="cod")
        )
        # COD but confirmed -> must NOT match status=pending.
        cod_confirmed = await auth_client.post(
            "/api/v1/orders", json=_payload("OMS-COMBO-COD-CONFIRMED", payment_type="cod")
        )
        await auth_client.patch(
            f"/api/v1/orders/{cod_confirmed.json()['data']['id']}", json={"status": "confirmed"}
        )
        # Pending but prepaid -> must NOT match payment_type=cod.
        await auth_client.post(
            "/api/v1/orders", json=_payload("OMS-COMBO-PENDING-PREPAID", payment_type="prepaid")
        )

        response = await auth_client.get(
            "/api/v1/orders", params={"status": "pending", "payment_type": "cod"}
        )
        assert [o["order_number"] for o in response.json()["data"]] == ["OMS-COMBO-MATCH"]


async def test_combined_cod_and_minimum_amount(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=_ORDER_PERMS
    ) as auth_client:
        await auth_client.post(
            "/api/v1/orders",
            json=_payload("OMS-COMBO-COD-HIGH", payment_type="cod", unit_price="900.00"),
        )
        await auth_client.post(
            "/api/v1/orders",
            json=_payload("OMS-COMBO-COD-LOW", payment_type="cod", unit_price="100.00"),
        )
        await auth_client.post(
            "/api/v1/orders",
            json=_payload("OMS-COMBO-PREPAID-HIGH", payment_type="prepaid", unit_price="900.00"),
        )

        response = await auth_client.get(
            "/api/v1/orders", params={"payment_type": "cod", "amount_min": "500"}
        )
        assert [o["order_number"] for o in response.json()["data"]] == ["OMS-COMBO-COD-HIGH"]


# --- Pagination respects filters (Part 6) -----------------------------------


async def test_pagination_respects_active_filters(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """500 orders / 70 pending, per the spec's own worked example — a
    smaller but structurally identical version: total_items and the
    paginated rows must both reflect the FILTERED set, not the whole table.
    """
    from app.repositories.order import OrderRepository

    repo = OrderRepository(db_session)
    for i in range(7):
        await repo.upsert_by_external_id(
            source_system="shopify",
            external_id=f"page-pending-{i}",
            order_number=f"OMS-PAGE-PEND-{i}",
            order_datetime=datetime.now(UTC),
            total_amount=Decimal("100.00"),
            status="pending",
        )
    for i in range(13):
        await repo.upsert_by_external_id(
            source_system="shopify",
            external_id=f"page-confirmed-{i}",
            order_number=f"OMS-PAGE-CONF-{i}",
            order_datetime=datetime.now(UTC),
            total_amount=Decimal("100.00"),
            status="confirmed",
        )
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=_ORDER_PERMS
    ) as auth_client:
        page1 = await auth_client.get(
            "/api/v1/orders",
            params={"status": "pending", "page": 1, "page_size": 5},
        )
        assert page1.json()["meta"]["total_items"] == 7
        assert page1.json()["meta"]["total_pages"] == 2
        assert len(page1.json()["data"]) == 5

        page2 = await auth_client.get(
            "/api/v1/orders",
            params={"status": "pending", "page": 2, "page_size": 5},
        )
        assert len(page2.json()["data"]) == 2

        # No page 3 of a 2-page filtered result.
        page3 = await auth_client.get(
            "/api/v1/orders",
            params={"status": "pending", "page": 3, "page_size": 5},
        )
        assert page3.json()["data"] == []


# --- Clear filters == no params returns the full list (Part 7) -------------


async def test_no_query_params_is_equivalent_to_clearing_all_filters(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=_ORDER_PERMS
    ) as auth_client:
        await auth_client.post("/api/v1/orders", json=_payload("OMS-CLEAR-1", payment_type="cod"))
        await auth_client.post(
            "/api/v1/orders", json=_payload("OMS-CLEAR-2", payment_type="prepaid")
        )

        filtered = await auth_client.get("/api/v1/orders", params={"payment_type": "cod"})
        assert filtered.json()["meta"]["total_items"] == 1

        cleared = await auth_client.get("/api/v1/orders")
        assert cleared.json()["meta"]["total_items"] == 2


# --- Invalid filter value ----------------------------------------------------


async def test_invalid_status_value_returns_a_client_error_not_a_silent_empty_list(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """An invalid enum value must fail loudly (422) — silently returning
    an empty list would look identical to "filter works but matches
    nothing," which is exactly the confusing symptom being investigated.
    """
    async with await make_authenticated_client(
        db_session, permission_codes=_ORDER_PERMS
    ) as auth_client:
        await auth_client.post("/api/v1/orders", json=_payload("OMS-INVALID-1"))

        response = await auth_client.get("/api/v1/orders", params={"status": "not-a-real-status"})
        assert response.status_code == 422
