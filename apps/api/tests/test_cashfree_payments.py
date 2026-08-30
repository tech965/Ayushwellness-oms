"""Cashfree checkout/payment-status/reconcile endpoints: server-computed
amount (never trusts the browser), idempotent checkout creation, auth,
invalid/already-paid order handling, and reconciliation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.core.config import settings
from app.integrations.cashfree.client import CashfreeClient
from app.integrations.registry import clear_adapters
from app.models.enums import OrderStatus, PaymentStatus, PaymentType
from app.repositories.order import OrderRepository
from app.repositories.payment import PaymentRepository
from app.services.order_service import OrderService
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_SECRET = "test-cashfree-client-secret"


@pytest.fixture(autouse=True)
def _configure_cashfree(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "CASHFREE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(settings, "CASHFREE_CLIENT_SECRET", _SECRET)
    yield
    clear_adapters()


async def _make_order(
    session: AsyncSession,
    *,
    order_number: str,
    total_amount: Decimal = Decimal("500.00"),
    phone: str | None = "9999999999",
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
    updates: dict = {"total_amount": total_amount}
    if phone:
        updates["shipping_address"] = {"contact_phone": phone, "contact_name": "Test Customer"}
    await OrderRepository(session).update(order, **updates)
    await session.commit()
    return order.id


def _stub_create_order(response: dict):
    calls = {"n": 0}

    async def _fake_create_order(self, payload):  # noqa: ANN001
        calls["n"] += 1
        return {
            **response,
            "order_id": payload["order_id"],
            "order_amount": payload["order_amount"],
        }

    return _fake_create_order, calls


# --- H. API endpoints ------------------------------------------------


async def test_create_checkout_requires_authentication(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    order_id = await _make_order(db_session, order_number="#AWLEP1")
    response = await client.post(f"/api/v1/payments/cashfree/orders/{order_id}/create")
    assert response.status_code == 401


async def test_create_checkout_requires_permission(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    order_id = await _make_order(db_session, order_number="#AWLEP2")
    async with await make_authenticated_client(
        db_session, permission_codes=["payments.read"]
    ) as authed_client:
        response = await authed_client.post(f"/api/v1/payments/cashfree/orders/{order_id}/create")
    assert response.status_code == 403


async def test_create_checkout_succeeds_with_server_computed_amount(
    db_session: AsyncSession, make_authenticated_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    order_id = await _make_order(db_session, order_number="#AWLEP3", total_amount=Decimal("777.00"))
    fake, calls = _stub_create_order(
        {"cf_order_id": "cf_1", "order_status": "ACTIVE", "payment_session_id": "session_1"}
    )
    monkeypatch.setattr(CashfreeClient, "create_order", fake)
    monkeypatch.setattr(CashfreeClient, "aclose", lambda self: _noop())

    async with await make_authenticated_client(
        db_session, permission_codes=["payments.create"]
    ) as authed_client:
        # Never trust a browser-supplied amount -- there is no amount
        # field on this request at all; the endpoint takes no body.
        response = await authed_client.post(
            f"/api/v1/payments/cashfree/orders/{order_id}/create",
            json={"amount": "1.00"},  # ignored even if a client sends one
        )

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["amount"] == "777.00"
    assert body["currency"] == "INR"
    assert body["payment_session_id"] == "session_1"
    assert body["created"] is True
    assert calls["n"] == 1

    payment = await PaymentRepository(db_session).get_by_source_external_id(
        source_system="cashfree", external_id=body["cashfree_order_id"]
    )
    assert payment is not None
    assert payment.amount == Decimal("777.00")


async def test_create_checkout_is_idempotent_and_reuses_active_session(
    db_session: AsyncSession, make_authenticated_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    order_id = await _make_order(db_session, order_number="#AWLEP4")
    fake, calls = _stub_create_order(
        {"cf_order_id": "cf_1", "order_status": "ACTIVE", "payment_session_id": "session_1"}
    )
    monkeypatch.setattr(CashfreeClient, "create_order", fake)
    monkeypatch.setattr(CashfreeClient, "aclose", lambda self: _noop())

    async with await make_authenticated_client(
        db_session, permission_codes=["payments.create"]
    ) as authed_client:
        first = await authed_client.post(f"/api/v1/payments/cashfree/orders/{order_id}/create")
        second = await authed_client.post(f"/api/v1/payments/cashfree/orders/{order_id}/create")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["data"]["created"] is True
    assert second.json()["data"]["created"] is False
    assert first.json()["data"]["cashfree_order_id"] == second.json()["data"]["cashfree_order_id"]
    # A repeated request never creates a second Cashfree order.
    assert calls["n"] == 1


async def test_create_checkout_on_already_paid_order_is_rejected(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    order_id = await _make_order(db_session, order_number="#AWLEP5")
    order = await OrderRepository(db_session).get_by_id(order_id)
    await OrderRepository(db_session).update(order, payment_status=PaymentStatus.PAID)
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["payments.create"]
    ) as authed_client:
        response = await authed_client.post(f"/api/v1/payments/cashfree/orders/{order_id}/create")

    assert response.status_code == 409


async def test_create_checkout_on_unknown_order_returns_404(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=["payments.create"]
    ) as authed_client:
        response = await authed_client.post(
            f"/api/v1/payments/cashfree/orders/{uuid.uuid4()}/create"
        )
    assert response.status_code == 404


async def test_create_checkout_without_customer_phone_is_rejected(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    order_id = await _make_order(db_session, order_number="#AWLEP6", phone=None)
    async with await make_authenticated_client(
        db_session, permission_codes=["payments.create"]
    ) as authed_client:
        response = await authed_client.post(f"/api/v1/payments/cashfree/orders/{order_id}/create")
    assert response.status_code == 422


async def test_get_payment_status_requires_permission(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    order_id = await _make_order(db_session, order_number="#AWLEP7")
    async with await make_authenticated_client(db_session, permission_codes=[]) as authed_client:
        response = await authed_client.get(f"/api/v1/payments/cashfree/orders/{order_id}")
    assert response.status_code == 403


async def test_get_payment_status_returns_safe_fields_only(
    db_session: AsyncSession, make_authenticated_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    order_id = await _make_order(db_session, order_number="#AWLEP8", total_amount=Decimal("321.00"))
    fake, _ = _stub_create_order(
        {"cf_order_id": "cf_1", "order_status": "ACTIVE", "payment_session_id": "session_1"}
    )
    monkeypatch.setattr(CashfreeClient, "create_order", fake)
    monkeypatch.setattr(CashfreeClient, "aclose", lambda self: _noop())

    async with await make_authenticated_client(
        db_session, permission_codes=["payments.create", "payments.read"]
    ) as authed_client:
        await authed_client.post(f"/api/v1/payments/cashfree/orders/{order_id}/create")
        response = await authed_client.get(f"/api/v1/payments/cashfree/orders/{order_id}")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["amount"] == "321.00"
    assert body["status"] == "pending"
    assert body["provider"] == "cashfree"
    # never exposes secrets/raw auth headers
    assert "client_secret" not in response.text
    assert _SECRET not in response.text


async def test_get_payment_status_for_order_with_no_cashfree_payment_returns_404(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    order_id = await _make_order(db_session, order_number="#AWLEP9")
    async with await make_authenticated_client(
        db_session, permission_codes=["payments.read"]
    ) as authed_client:
        response = await authed_client.get(f"/api/v1/payments/cashfree/orders/{order_id}")
    assert response.status_code == 404


async def test_reconcile_endpoint_applies_the_latest_cashfree_state(
    db_session: AsyncSession, make_authenticated_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    order_id = await _make_order(
        db_session, order_number="#AWLEP10", total_amount=Decimal("250.00")
    )
    create_fake, _ = _stub_create_order(
        {"cf_order_id": "cf_1", "order_status": "ACTIVE", "payment_session_id": "session_1"}
    )
    monkeypatch.setattr(CashfreeClient, "create_order", create_fake)
    monkeypatch.setattr(CashfreeClient, "aclose", lambda self: _noop())

    async def _fake_get_payments_for_order(self, order_id):  # noqa: ANN001
        return [
            {
                "cf_payment_id": "pay_recon_1",
                "payment_status": "SUCCESS",
                "payment_amount": 250.00,
                "payment_currency": "INR",
                "payment_method": {"upi": {}},
                "payment_time": "2026-02-01T10:00:00+05:30",
            }
        ]

    monkeypatch.setattr(CashfreeClient, "get_payments_for_order", _fake_get_payments_for_order)

    async with await make_authenticated_client(
        db_session, permission_codes=["payments.create", "payments.read"]
    ) as authed_client:
        await authed_client.post(f"/api/v1/payments/cashfree/orders/{order_id}/create")
        response = await authed_client.post(
            f"/api/v1/payments/cashfree/orders/{order_id}/reconcile"
        )

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["status"] == "paid"

    order = await OrderRepository(db_session).get_by_id(order_id)
    assert order.payment_status == PaymentStatus.PAID
    assert order.status == OrderStatus.CONFIRMED


async def _noop():
    return None
