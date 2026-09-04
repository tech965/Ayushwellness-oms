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


# --- I. Connection status endpoints -----------------------------------


async def test_status_endpoint_reports_configured_environment_and_api_url(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=["payments.read"]
    ) as authed_client:
        response = await authed_client.get("/api/v1/payments/cashfree/status")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["configured"] is True
    assert body["environment"] == "sandbox"
    assert body["api_url"] == "https://sandbox.cashfree.com/pg"
    assert body["api_version"] == "2025-01-01"
    # Never the client secret, in any form.
    assert _SECRET not in response.text


async def test_status_endpoint_reports_not_configured_when_credentials_missing(
    db_session: AsyncSession, make_authenticated_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "CASHFREE_CLIENT_ID", None)
    monkeypatch.setattr(settings, "CASHFREE_CLIENT_SECRET", None)

    async with await make_authenticated_client(
        db_session, permission_codes=["payments.read"]
    ) as authed_client:
        response = await authed_client.get("/api/v1/payments/cashfree/status")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["configured"] is False
    assert body["environment"] == "not_configured"
    assert body["api_url"] is None


async def test_status_endpoint_requires_permission(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    response = await client.get("/api/v1/payments/cashfree/status")
    assert response.status_code == 401


async def test_status_endpoint_reports_production_environment_from_api_url(
    db_session: AsyncSession, make_authenticated_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "CASHFREE_API_URL", "https://api.cashfree.com/pg")

    async with await make_authenticated_client(
        db_session, permission_codes=["payments.read"]
    ) as authed_client:
        response = await authed_client.get("/api/v1/payments/cashfree/status")

    assert response.json()["data"]["environment"] == "production"


async def test_test_connection_reports_connected_on_a_404_sentinel_response(
    db_session: AsyncSession, make_authenticated_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 404 on the deliberately-nonexistent sentinel order id means the
    request reached Cashfree and the credentials were accepted -- the
    exact contract already verified against real Cashfree production via
    a Render shell.
    """
    from app.integrations.cashfree.errors import CashfreeApiError

    async def _fake_get_order(self, order_id):  # noqa: ANN001
        assert order_id == "oms-connectivity-check-000000"
        raise CashfreeApiError(
            "Cashfree order/payment not found.", error_type="not_found", status_code=404
        )

    monkeypatch.setattr(CashfreeClient, "get_order", _fake_get_order)
    monkeypatch.setattr(CashfreeClient, "aclose", lambda self: _noop())

    async with await make_authenticated_client(
        db_session, permission_codes=["integrations.test"]
    ) as authed_client:
        response = await authed_client.post("/api/v1/payments/cashfree/status/test-connection")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["connected"] is True
    assert body["configured"] is True
    assert body["error_type"] == "not_found"
    assert body["status_code"] == 404
    assert _SECRET not in response.text


async def test_test_connection_reports_not_connected_on_authentication_failure(
    db_session: AsyncSession, make_authenticated_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.integrations.cashfree.errors import CashfreeApiError

    async def _fake_get_order(self, order_id):  # noqa: ANN001
        raise CashfreeApiError(
            "Cashfree rejected the credentials.", error_type="authentication_error", status_code=401
        )

    monkeypatch.setattr(CashfreeClient, "get_order", _fake_get_order)
    monkeypatch.setattr(CashfreeClient, "aclose", lambda self: _noop())

    async with await make_authenticated_client(
        db_session, permission_codes=["integrations.test"]
    ) as authed_client:
        response = await authed_client.post("/api/v1/payments/cashfree/status/test-connection")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["connected"] is False
    assert body["error_type"] == "authentication_error"
    assert body["status_code"] == 401
    # Never leaks the credentials that were rejected.
    assert _SECRET not in response.text
    assert "test-client-id" not in response.text


async def test_test_connection_reports_not_configured_without_calling_cashfree(
    db_session: AsyncSession,
    make_authenticated_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "CASHFREE_CLIENT_ID", None)
    monkeypatch.setattr(settings, "CASHFREE_CLIENT_SECRET", None)

    calls = {"n": 0}

    async def _fake_get_order(self, order_id):  # noqa: ANN001
        calls["n"] += 1
        raise AssertionError("must never call Cashfree when unconfigured")

    monkeypatch.setattr(CashfreeClient, "get_order", _fake_get_order)

    async with await make_authenticated_client(
        db_session, permission_codes=["integrations.test"]
    ) as authed_client:
        response = await authed_client.post("/api/v1/payments/cashfree/status/test-connection")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["configured"] is False
    assert body["connected"] is False
    assert calls["n"] == 0


async def test_test_connection_requires_integrations_test_permission(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=["payments.read"]
    ) as authed_client:
        response = await authed_client.post("/api/v1/payments/cashfree/status/test-connection")
    assert response.status_code == 403


# --- J. Payment analytics endpoints -----------------------------------


async def _make_cashfree_payment(
    session: AsyncSession,
    *,
    order_number: str,
    status: PaymentStatus,
    amount: Decimal = Decimal("500.00"),
    payment_method: str | None = None,
):
    order_id = await _make_order(session, order_number=order_number, total_amount=amount)
    metadata = {"payment_method": payment_method} if payment_method else None
    payment = await PaymentRepository(session).create(
        order_id=order_id,
        payment_type=PaymentType.PREPAID,
        status=status,
        amount=amount,
        currency="INR",
        provider="cashfree",
        source_system="cashfree",
        external_id=f"cf_{order_number}",
        payment_metadata=metadata,
    )
    await session.commit()
    return payment


async def test_payment_overview_counts_and_amounts_are_cashfree_scoped(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    await _make_cashfree_payment(
        db_session, order_number="OV1", status=PaymentStatus.PAID, amount=Decimal("100.00")
    )
    await _make_cashfree_payment(
        db_session, order_number="OV2", status=PaymentStatus.PAID, amount=Decimal("200.00")
    )
    await _make_cashfree_payment(
        db_session, order_number="OV3", status=PaymentStatus.PENDING, amount=Decimal("50.00")
    )
    await _make_cashfree_payment(
        db_session, order_number="OV4", status=PaymentStatus.FAILED, amount=Decimal("75.00")
    )
    # A plain COD/manual order (no Cashfree payment at all) must never be
    # counted here -- only its own auto-created "manual" baseline payment
    # exists, which has `provider=None`, not "cashfree".
    await _make_order(db_session, order_number="#NOTCASHFREE")

    async with await make_authenticated_client(
        db_session, permission_codes=["payments.read"]
    ) as authed_client:
        response = await authed_client.get("/api/v1/payments/cashfree/analytics/overview")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["total_payments"]["current"] == "4"
    assert body["paid_payments"]["current"] == "2"
    assert body["pending_payments"]["current"] == "1"
    assert body["failed_payments"]["current"] == "1"
    assert body["total_amount"]["current"] == "300.00"
    assert body["pending_amount"]["current"] == "50.00"
    statuses = {item["status"]: item["count"] for item in body["status_breakdown"]}
    assert statuses["paid"] == 2
    assert statuses["pending"] == 1
    assert statuses["failed"] == 1


async def test_payment_overview_requires_permission(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    response = await client.get("/api/v1/payments/cashfree/analytics/overview")
    assert response.status_code == 401


async def test_payment_trend_buckets_by_day(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    payment = await _make_cashfree_payment(
        db_session, order_number="TR1", status=PaymentStatus.PAID, amount=Decimal("150.00")
    )
    await PaymentRepository(db_session).update(payment, created_at=datetime.now(UTC))
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["payments.read"]
    ) as authed_client:
        response = await authed_client.get(
            "/api/v1/payments/cashfree/analytics/trend", params={"interval": "day"}
        )

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["interval"] == "day"
    assert len(body["points"]) == 1
    point = body["points"][0]
    assert point["total_count"] == 1
    assert point["paid_count"] == 1
    assert point["paid_amount"] == "150.00"


async def test_payment_trend_rejects_an_invalid_interval(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=["payments.read"]
    ) as authed_client:
        response = await authed_client.get(
            "/api/v1/payments/cashfree/analytics/trend", params={"interval": "fortnight"}
        )
    assert response.status_code == 422


async def test_payment_method_breakdown_groups_by_method(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    await _make_cashfree_payment(
        db_session,
        order_number="MB1",
        status=PaymentStatus.PAID,
        amount=Decimal("100.00"),
        payment_method="upi",
    )
    await _make_cashfree_payment(
        db_session,
        order_number="MB2",
        status=PaymentStatus.PAID,
        amount=Decimal("200.00"),
        payment_method="upi",
    )
    await _make_cashfree_payment(
        db_session,
        order_number="MB3",
        status=PaymentStatus.PAID,
        amount=Decimal("50.00"),
        payment_method="card",
    )
    # No payment_method recorded yet (e.g. still PENDING) -- must not
    # appear in the breakdown as a fake "None" method.
    await _make_cashfree_payment(db_session, order_number="MB4", status=PaymentStatus.PENDING)

    async with await make_authenticated_client(
        db_session, permission_codes=["payments.read"]
    ) as authed_client:
        response = await authed_client.get("/api/v1/payments/cashfree/analytics/method-breakdown")

    assert response.status_code == 200
    items = {item["payment_method"]: item for item in response.json()["data"]["items"]}
    assert items["upi"]["count"] == 2
    assert items["upi"]["amount"] == "300.00"
    assert items["card"]["count"] == 1
    assert items["card"]["amount"] == "50.00"


# --- J. Sync endpoints (bulk transaction/settlement sync) ----------------


async def test_sync_endpoint_requires_payments_create_permission(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=["payments.read"]
    ) as authed_client:
        response = await authed_client.post(
            "/api/v1/payments/cashfree/sync",
            json={"date_from": "2026-09-03T00:00:00Z", "date_to": "2026-09-03T23:59:59Z"},
        )
    assert response.status_code == 403


async def test_sync_endpoint_runs_reconciliation_and_reports_result(
    db_session: AsyncSession, make_authenticated_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    order_id = await _make_order(
        db_session, order_number="#AWLSYNCEP1", total_amount=Decimal("321.00")
    )

    async def _fake_recon(self, *, start_date, end_date, cursor=None, limit=1000):  # noqa: ANN001
        return {
            "data": [
                {
                    "order_id": "AWLSYNCEP1",
                    "cf_payment_id": "pay_ep1",
                    "payment_amount": "321.00",
                    "payment_currency": "INR",
                    "payment_time": "2026-09-03T10:00:00Z",
                    "status": "SUCCESS",
                    "event_type": "PAYMENT",
                }
            ],
            "cursor": None,
        }

    monkeypatch.setattr(CashfreeClient, "get_reconciliation", _fake_recon)
    monkeypatch.setattr(CashfreeClient, "aclose", lambda self: _noop())

    async with await make_authenticated_client(
        db_session, permission_codes=["payments.create"]
    ) as authed_client:
        response = await authed_client.post(
            "/api/v1/payments/cashfree/sync",
            json={"date_from": "2026-09-03T00:00:00Z", "date_to": "2026-09-03T23:59:59Z"},
        )

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["fetched"] == 1
    assert body["applied"] == 1
    assert body["failures"] == 0

    payment = await PaymentRepository(db_session).get_by_source_external_id(
        source_system="cashfree", external_id="AWLSYNCEP1"
    )
    assert payment is not None
    assert payment.order_id == order_id
    assert payment.status == PaymentStatus.PAID


async def test_settlement_sync_endpoint_requires_permission(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=["payments.read"]
    ) as authed_client:
        response = await authed_client.post(
            "/api/v1/payments/cashfree/settlements/sync",
            json={"date_from": "2026-09-03T00:00:00Z", "date_to": "2026-09-03T23:59:59Z"},
        )
    assert response.status_code == 403


async def test_settlement_sync_endpoint_populates_and_summary_reflects_it(
    db_session: AsyncSession, make_authenticated_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_settlements(
        self, *, start_date=None, end_date=None, settlement_status=None, cursor=None, limit=1000
    ):  # noqa: ANN001
        return {
            "data": [
                {
                    "cf_settlement_id": "stl_ep1",
                    "status": "SUCCESS",
                    "status_description": "Success",
                    "settlement_utr": "UTREP1",
                    "settlement_initiated_on": "2026-09-02T10:00:00Z",
                    "settlement_processed_on": "2026-09-03T10:00:00Z",
                    "payment_amount": "10000.00",
                    "pg_service_charge": "400.00",
                    "pg_service_tax": "72.00",
                    "adjustment": "0.00",
                    "settlement_charge": "20.00",
                    "settlement_tax": "8.00",
                    "amount_settled": "9500.00",
                }
            ],
            "cursor": None,
        }

    monkeypatch.setattr(CashfreeClient, "get_settlements", _fake_settlements)
    monkeypatch.setattr(CashfreeClient, "aclose", lambda self: _noop())

    async with await make_authenticated_client(
        db_session, permission_codes=["payments.create", "payments.read"]
    ) as authed_client:
        sync_response = await authed_client.post(
            "/api/v1/payments/cashfree/settlements/sync",
            json={"date_from": "2026-09-01T00:00:00Z", "date_to": "2026-09-03T23:59:59Z"},
        )
        assert sync_response.status_code == 200
        assert sync_response.json()["data"]["applied"] == 1

        summary_response = await authed_client.get(
            "/api/v1/payments/cashfree/analytics/settlements"
        )

    assert summary_response.status_code == 200
    summary = summary_response.json()["data"]
    assert summary["last_settled_amount"] == "9500.00"
    assert summary["last_settlement_utr"] == "UTREP1"
    assert summary["unsettled_amount"] == "0"
    assert len(summary["history"]) == 1


async def test_settlement_analytics_requires_permission(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(db_session, permission_codes=[]) as authed_client:
        response = await authed_client.get("/api/v1/payments/cashfree/analytics/settlements")
    assert response.status_code == 403


async def test_settlement_analytics_endpoint_returns_a_clean_empty_response_never_a_500(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """Issue 3 investigation: confirms `GET /analytics/settlements` itself
    has no bug that would turn "nothing synced yet" into a 500 — a
    genuinely empty `cashfree_settlements` table (the state before the
    settlement sync has ever been run, e.g. right after this feature's
    migration first lands) must render as a real, structured empty
    response (matching `test_settlement_summary_with_no_data_returns_
    honest_empty_values` at the service layer, but exercised through the
    real HTTP route + response_model serialization this time), never an
    unhandled exception the global error handler would mask as "An
    unexpected error occurred."
    """
    async with await make_authenticated_client(
        db_session, permission_codes=["payments.read"]
    ) as authed_client:
        response = await authed_client.get("/api/v1/payments/cashfree/analytics/settlements")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["unsettled_amount"] == "0"
    assert data["upcoming_settlement_amount"] is None
    assert data["last_settled_amount"] is None
    assert data["history"] == []
