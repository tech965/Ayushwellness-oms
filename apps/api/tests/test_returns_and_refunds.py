from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.models.customer import Customer
from app.models.enums import RefundStatus, ReturnStatus
from app.models.order import Order, OrderItem
from app.models.refund import Refund
from app.models.returns import Return
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


async def _create_enriched_order(
    db_session: AsyncSession,
    order_number: str,
    *,
    customer_name: str = "Ananya Rao",
    customer_phone: str = "9998887776",
    product_name: str = "Ashwagandha 60ct",
    payment_type: str = "prepaid",
    total_amount: str = "649.00",
) -> tuple[Order, OrderItem]:
    """Fuller fixture for the enriched-response/search/filter tests below
    — a real Customer + OrderItem attached to the Order, matching what
    `ReturnRepository`/`RefundRepository`'s eager-loaded relationships
    expect.
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
    await db_session.commit()
    await db_session.refresh(order)
    await db_session.refresh(item)
    return order, item


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


# --- Returns: list endpoint / enriched response / search / filters ---------


async def test_returns_list_endpoint_returns_enriched_order_customer_and_product_data(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    order, item = await _create_enriched_order(db_session, "OMS-RET-ENR-1")
    return_ = Return(
        order_id=order.id,
        order_item_id=item.id,
        reason="Damaged",
        status=ReturnStatus.REQUESTED,
        quantity=1,
        source_system="manual",
    )
    db_session.add(return_)
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["returns.read"]
    ) as auth_client:
        response = await auth_client.get("/api/v1/returns")
        assert response.status_code == 200
        row = response.json()["data"][0]
        assert row["order_number"] == "OMS-RET-ENR-1"
        assert row["customer_name"] == "Ananya Rao"
        assert row["customer_phone"] == "9998887776"
        # Prefers the specific returned order_item's product name.
        assert row["product"] == "Ashwagandha 60ct"
        assert row["order_amount"] == "649.00"
        assert row["payment_type"] == "prepaid"


async def test_returns_search_matches_order_number_customer_and_product(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    match_order, _ = await _create_enriched_order(
        db_session, "OMS-RET-SEARCH-1", customer_name="Priya Nair", product_name="Turmeric 30ct"
    )
    other_order, _ = await _create_enriched_order(
        db_session, "OMS-RET-OTHER-1", customer_name="Rahul Iyer", product_name="Neem Capsules"
    )
    for order in (match_order, other_order):
        db_session.add(
            Return(order_id=order.id, status=ReturnStatus.REQUESTED, source_system="manual")
        )
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["returns.read"]
    ) as auth_client:
        for query in ("OMS-RET-SEARCH-1", "Priya", "Turmeric"):
            response = await auth_client.get("/api/v1/returns", params={"q": query})
            order_numbers = [row["order_number"] for row in response.json()["data"]]
            assert order_numbers == ["OMS-RET-SEARCH-1"], (query, order_numbers)


async def test_returns_filters_by_payment_type(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    cod_order, _ = await _create_enriched_order(db_session, "OMS-RET-COD-1", payment_type="cod")
    prepaid_order, _ = await _create_enriched_order(
        db_session, "OMS-RET-PRE-1", payment_type="prepaid"
    )
    for order in (cod_order, prepaid_order):
        db_session.add(
            Return(order_id=order.id, status=ReturnStatus.REQUESTED, source_system="manual")
        )
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["returns.read"]
    ) as auth_client:
        response = await auth_client.get("/api/v1/returns", params={"payment_type": "cod"})
        order_numbers = [row["order_number"] for row in response.json()["data"]]
        assert order_numbers == ["OMS-RET-COD-1"]


async def test_returns_filters_by_status(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    requested_order, _ = await _create_enriched_order(db_session, "OMS-RET-REQ-1")
    completed_order, _ = await _create_enriched_order(db_session, "OMS-RET-COMP-1")
    db_session.add(
        Return(order_id=requested_order.id, status=ReturnStatus.REQUESTED, source_system="manual")
    )
    db_session.add(
        Return(order_id=completed_order.id, status=ReturnStatus.COMPLETED, source_system="manual")
    )
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["returns.read"]
    ) as auth_client:
        response = await auth_client.get("/api/v1/returns", params={"status": "completed"})
        order_numbers = [row["order_number"] for row in response.json()["data"]]
        assert order_numbers == ["OMS-RET-COMP-1"]


async def test_returns_filters_by_date_range_on_created_at(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    order, _ = await _create_enriched_order(db_session, "OMS-RET-DATE-1")
    return_ = Return(order_id=order.id, status=ReturnStatus.REQUESTED, source_system="manual")
    db_session.add(return_)
    await db_session.commit()
    await db_session.refresh(return_)

    async with await make_authenticated_client(
        db_session, permission_codes=["returns.read"]
    ) as auth_client:
        now = return_.created_at
        in_range = await auth_client.get(
            "/api/v1/returns",
            params={
                "date_from": (now - timedelta(days=1)).isoformat(),
                "date_to": (now + timedelta(days=1)).isoformat(),
            },
        )
        assert [row["id"] for row in in_range.json()["data"]] == [str(return_.id)]

        out_of_range = await auth_client.get(
            "/api/v1/returns",
            params={
                "date_from": (now + timedelta(days=1)).isoformat(),
                "date_to": (now + timedelta(days=2)).isoformat(),
            },
        )
        assert out_of_range.json()["data"] == []


# --- Refunds: list endpoint / enriched response / search / filters ---------


async def test_refunds_list_endpoint_returns_enriched_order_customer_and_product_data(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    order, _ = await _create_enriched_order(db_session, "OMS-REF-ENR-1")
    refund = Refund(
        order_id=order.id,
        amount=Decimal("300.00"),
        reason="Damaged item",
        status=RefundStatus.PENDING,
        source_system="manual",
    )
    db_session.add(refund)
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["refunds.read"]
    ) as auth_client:
        response = await auth_client.get("/api/v1/refunds")
        assert response.status_code == 200
        row = response.json()["data"][0]
        assert row["order_number"] == "OMS-REF-ENR-1"
        assert row["customer_name"] == "Ananya Rao"
        assert row["customer_phone"] == "9998887776"
        assert row["product"] == "Ashwagandha 60ct"
        # Original order amount, distinct from the refund's own amount.
        assert row["order_amount"] == "649.00"
        assert row["amount"] == "300.00"
        assert row["payment_type"] == "prepaid"


async def test_refunds_search_matches_order_number_customer_and_product(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    match_order, _ = await _create_enriched_order(
        db_session, "OMS-REF-SEARCH-1", customer_name="Priya Nair", product_name="Turmeric 30ct"
    )
    other_order, _ = await _create_enriched_order(
        db_session, "OMS-REF-OTHER-1", customer_name="Rahul Iyer", product_name="Neem Capsules"
    )
    for order in (match_order, other_order):
        db_session.add(
            Refund(
                order_id=order.id,
                amount=Decimal("100.00"),
                status=RefundStatus.PENDING,
                source_system="manual",
            )
        )
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["refunds.read"]
    ) as auth_client:
        for query in ("OMS-REF-SEARCH-1", "Priya", "Turmeric"):
            response = await auth_client.get("/api/v1/refunds", params={"q": query})
            order_numbers = [row["order_number"] for row in response.json()["data"]]
            assert order_numbers == ["OMS-REF-SEARCH-1"], (query, order_numbers)


async def test_refunds_filters_by_payment_type(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    cod_order, _ = await _create_enriched_order(db_session, "OMS-REF-COD-1", payment_type="cod")
    prepaid_order, _ = await _create_enriched_order(
        db_session, "OMS-REF-PRE-1", payment_type="prepaid"
    )
    for order in (cod_order, prepaid_order):
        db_session.add(
            Refund(
                order_id=order.id,
                amount=Decimal("100.00"),
                status=RefundStatus.PENDING,
                source_system="manual",
            )
        )
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["refunds.read"]
    ) as auth_client:
        response = await auth_client.get("/api/v1/refunds", params={"payment_type": "cod"})
        order_numbers = [row["order_number"] for row in response.json()["data"]]
        assert order_numbers == ["OMS-REF-COD-1"]


async def test_refunds_filters_by_status(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    pending_order, _ = await _create_enriched_order(db_session, "OMS-REF-PEND-1")
    completed_order, _ = await _create_enriched_order(db_session, "OMS-REF-COMP-1")
    db_session.add(
        Refund(
            order_id=pending_order.id,
            amount=Decimal("100.00"),
            status=RefundStatus.PENDING,
            source_system="manual",
        )
    )
    db_session.add(
        Refund(
            order_id=completed_order.id,
            amount=Decimal("100.00"),
            status=RefundStatus.COMPLETED,
            source_system="manual",
        )
    )
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["refunds.read"]
    ) as auth_client:
        response = await auth_client.get("/api/v1/refunds", params={"status": "completed"})
        order_numbers = [row["order_number"] for row in response.json()["data"]]
        assert order_numbers == ["OMS-REF-COMP-1"]


async def test_refunds_filters_by_date_range_on_created_at(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    order, _ = await _create_enriched_order(db_session, "OMS-REF-DATE-1")
    refund = Refund(
        order_id=order.id,
        amount=Decimal("100.00"),
        status=RefundStatus.PENDING,
        source_system="manual",
    )
    db_session.add(refund)
    await db_session.commit()
    await db_session.refresh(refund)

    async with await make_authenticated_client(
        db_session, permission_codes=["refunds.read"]
    ) as auth_client:
        now = refund.created_at
        in_range = await auth_client.get(
            "/api/v1/refunds",
            params={
                "date_from": (now - timedelta(days=1)).isoformat(),
                "date_to": (now + timedelta(days=1)).isoformat(),
            },
        )
        assert [row["id"] for row in in_range.json()["data"]] == [str(refund.id)]

        out_of_range = await auth_client.get(
            "/api/v1/refunds",
            params={
                "date_from": (now + timedelta(days=1)).isoformat(),
                "date_to": (now + timedelta(days=2)).isoformat(),
            },
        )
        assert out_of_range.json()["data"] == []
