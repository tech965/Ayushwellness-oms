"""Generic, provider-agnostic payment analytics
(`GET /payments/analytics/{overview,trend,method-breakdown}`).

Sibling of `test_cashfree_payments.py`'s "Payment analytics" section
(§J) — that file's `Payment.provider == "cashfree"`-scoped endpoints are
untouched here; these tests only exercise the new generic endpoints that
sit above the same `/payments` table and must, unlike the Cashfree-only
ones, include every provider (Shopify included) by default and filter
down to a specific provider on request.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.models.enums import PaymentStatus, PaymentType
from app.repositories.order import OrderRepository
from app.repositories.payment import PaymentRepository
from app.services.order_service import OrderService
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _make_order(
    session: AsyncSession, *, order_number: str, total_amount: Decimal = Decimal("500.00")
) -> uuid.UUID:
    order = await OrderService(session).create_order(
        actor=None,
        order_number=order_number,
        customer_id=None,
        order_datetime=datetime.now(UTC),
        currency="INR",
        payment_type=PaymentType.PREPAID,
        shipping_charge=0,
        notes=None,
        items=[],
    )
    await OrderRepository(session).update(order, total_amount=total_amount)
    await session.commit()
    return order.id


async def _make_payment(
    session: AsyncSession,
    *,
    order_number: str,
    provider: str,
    status: PaymentStatus,
    amount: Decimal = Decimal("500.00"),
    payment_type: PaymentType = PaymentType.PREPAID,
    payment_method: str | None = None,
):
    """`provider="cashfree"` sets `payment_metadata` (mirrors a real
    Cashfree payment); `provider="shopify"` never does — matches
    `OrderService.upsert_synced_order`, which sets `payment_type` but no
    `payment_metadata` for a real Shopify-synced payment.

    `OrderService.create_order` (used by `_make_order` for test setup)
    already creates its own baseline `Payment` row as a side effect —
    exactly like a real manually-created OMS order gets exactly one.
    Updating that row in place (rather than inserting a second one)
    keeps each test order at the one-payment-per-order shape every real
    order actually has; two rows per order here would be a test-only
    artifact no real Shopify- or manually-created order ever produces.
    """
    order_id = await _make_order(session, order_number=order_number, total_amount=amount)
    metadata = {"payment_method": payment_method} if payment_method else None
    [payment] = await PaymentRepository(session).list_for_order(order_id)
    payment = await PaymentRepository(session).update(
        payment,
        payment_type=payment_type,
        status=status,
        amount=amount,
        provider=provider,
        source_system=provider,
        external_id=f"{provider}_{order_number}",
        payment_metadata=metadata,
    )
    await session.commit()
    return payment


# --- Overview -----------------------------------------------------------


async def test_overview_all_providers_includes_shopify_and_cashfree(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    await _make_payment(
        db_session,
        order_number="GEN1",
        provider="shopify",
        status=PaymentStatus.PAID,
        amount=Decimal("200.00"),
    )
    await _make_payment(
        db_session,
        order_number="GEN2",
        provider="shopify",
        status=PaymentStatus.PENDING,
        amount=Decimal("50.00"),
    )
    await _make_payment(
        db_session,
        order_number="GEN3",
        provider="cashfree",
        status=PaymentStatus.PAID,
        amount=Decimal("100.00"),
        payment_method="upi",
    )

    async with await make_authenticated_client(
        db_session, permission_codes=["payments.read"]
    ) as authed_client:
        response = await authed_client.get("/api/v1/payments/analytics/overview")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["total_payments"]["current"] == "3"
    assert body["paid_payments"]["current"] == "2"
    assert body["pending_payments"]["current"] == "1"
    # 200 (shopify, paid) + 100 (cashfree, paid) -- Shopify is not excluded.
    assert body["total_amount"]["current"] == "300.00"


async def test_overview_provider_filter_narrows_to_shopify_only(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    await _make_payment(
        db_session,
        order_number="SHOP1",
        provider="shopify",
        status=PaymentStatus.PAID,
        amount=Decimal("200.00"),
    )
    await _make_payment(
        db_session,
        order_number="CF1",
        provider="cashfree",
        status=PaymentStatus.PAID,
        amount=Decimal("999.00"),
    )

    async with await make_authenticated_client(
        db_session, permission_codes=["payments.read"]
    ) as authed_client:
        response = await authed_client.get(
            "/api/v1/payments/analytics/overview", params={"provider": "shopify"}
        )

    body = response.json()["data"]
    assert body["total_payments"]["current"] == "1"
    assert body["total_amount"]["current"] == "200.00"


async def test_overview_provider_cashfree_matches_the_dedicated_cashfree_endpoint(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """The generic endpoint filtered to `provider=cashfree` must agree
    exactly with the existing, untouched Cashfree-only endpoint — proof
    this is a superset, not a divergent re-implementation.
    """
    await _make_payment(
        db_session,
        order_number="AGREE1",
        provider="cashfree",
        status=PaymentStatus.PAID,
        amount=Decimal("321.00"),
    )
    await _make_payment(
        db_session,
        order_number="AGREE2",
        provider="shopify",
        status=PaymentStatus.PAID,
        amount=Decimal("999.00"),
    )

    async with await make_authenticated_client(
        db_session, permission_codes=["payments.read"]
    ) as authed_client:
        generic = await authed_client.get(
            "/api/v1/payments/analytics/overview", params={"provider": "cashfree"}
        )
        dedicated = await authed_client.get("/api/v1/payments/cashfree/analytics/overview")

    assert generic.json()["data"]["total_payments"] == dedicated.json()["data"]["total_payments"]
    assert generic.json()["data"]["total_amount"] == dedicated.json()["data"]["total_amount"]


async def test_overview_requires_permission(db_session: AsyncSession, client: AsyncClient) -> None:
    response = await client.get("/api/v1/payments/analytics/overview")
    assert response.status_code == 401


# --- Trend ---------------------------------------------------------------


async def test_trend_all_providers_includes_shopify(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    shopify_payment = await _make_payment(
        db_session,
        order_number="TR-SHOP",
        provider="shopify",
        status=PaymentStatus.PAID,
        amount=Decimal("150.00"),
    )
    await PaymentRepository(db_session).update(shopify_payment, created_at=datetime.now(UTC))
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["payments.read"]
    ) as authed_client:
        response = await authed_client.get(
            "/api/v1/payments/analytics/trend", params={"interval": "day"}
        )

    assert response.status_code == 200
    body = response.json()["data"]
    assert len(body["points"]) == 1
    assert body["points"][0]["paid_count"] == 1
    assert body["points"][0]["paid_amount"] == "150.00"


async def test_trend_rejects_an_invalid_interval(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=["payments.read"]
    ) as authed_client:
        response = await authed_client.get(
            "/api/v1/payments/analytics/trend", params={"interval": "fortnight"}
        )
    assert response.status_code == 422


# --- Method breakdown ------------------------------------------------------


async def test_method_breakdown_falls_back_to_payment_type_for_shopify(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """Shopify payments never populate `payment_metadata` -- the
    breakdown must still represent them using their real `payment_type`
    (cod/prepaid), never silently drop them from an "All providers" view.
    """
    await _make_payment(
        db_session,
        order_number="MBSHOP1",
        provider="shopify",
        status=PaymentStatus.PAID,
        amount=Decimal("100.00"),
        payment_type=PaymentType.COD,
    )
    await _make_payment(
        db_session,
        order_number="MBSHOP2",
        provider="shopify",
        status=PaymentStatus.PAID,
        amount=Decimal("200.00"),
        payment_type=PaymentType.PREPAID,
    )
    await _make_payment(
        db_session,
        order_number="MBCF1",
        provider="cashfree",
        status=PaymentStatus.PAID,
        amount=Decimal("50.00"),
        payment_method="upi",
    )

    async with await make_authenticated_client(
        db_session, permission_codes=["payments.read"]
    ) as authed_client:
        response = await authed_client.get("/api/v1/payments/analytics/method-breakdown")

    assert response.status_code == 200
    items = {item["payment_method"]: item for item in response.json()["data"]["items"]}
    assert items["cod"]["count"] == 1
    assert items["cod"]["amount"] == "100.00"
    assert items["prepaid"]["count"] == 1
    assert items["upi"]["count"] == 1


async def test_method_breakdown_provider_filter_excludes_other_providers(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    await _make_payment(
        db_session,
        order_number="MBF-SHOP",
        provider="shopify",
        status=PaymentStatus.PAID,
        amount=Decimal("100.00"),
        payment_type=PaymentType.COD,
    )
    await _make_payment(
        db_session,
        order_number="MBF-CF",
        provider="cashfree",
        status=PaymentStatus.PAID,
        amount=Decimal("50.00"),
        payment_method="upi",
    )

    async with await make_authenticated_client(
        db_session, permission_codes=["payments.read"]
    ) as authed_client:
        response = await authed_client.get(
            "/api/v1/payments/analytics/method-breakdown", params={"provider": "cashfree"}
        )

    items = {item["payment_method"]: item for item in response.json()["data"]["items"]}
    assert "upi" in items
    assert "cod" not in items
