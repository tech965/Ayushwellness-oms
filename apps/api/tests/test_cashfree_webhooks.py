"""Cashfree webhook endpoint: raw-body signature verification, idempotent
ingestion, event classification (SUCCESS/FAILED/USER_DROPPED/unknown),
amount/currency validation, and payment/order state transitions.

Payload shapes used here match Cashfree's official webhook documentation
(`type`/`event_time`/`data.order`/`data.payment`/`data.customer_details`)
— see docs/integrations/cashfree.md for sources consulted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.core.config import settings
from app.integrations.registry import clear_adapters
from app.models.enums import (
    IntegrationStatus,
    IntegrationType,
    OrderStatus,
    PaymentStatus,
    PaymentType,
)
from app.models.integration import Integration, IntegrationCode, WebhookEvent
from app.models.order import Order
from app.models.payment import Payment, PaymentTransaction
from app.repositories.integration import IntegrationRepository
from app.repositories.order import OrderRepository
from app.repositories.payment import PaymentRepository
from app.services.order_service import OrderService
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_SECRET = "test-cashfree-client-secret"
_URL = "/api/v1/webhooks/cashfree/payment"
_TIMESTAMP = "1700000000000"


@pytest.fixture(autouse=True)
def _configure_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "CASHFREE_CLIENT_SECRET", _SECRET)
    yield
    clear_adapters()


def _sign(timestamp: str, body: bytes, secret: str = _SECRET) -> str:
    signed_payload = timestamp.encode("utf-8") + body
    digest = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


async def _make_cashfree_integration(session: AsyncSession) -> Integration:
    integration = await IntegrationRepository(session).create(
        name="Cashfree",
        code=IntegrationCode.CASHFREE,
        type=IntegrationType.ECOMMERCE,
        status=IntegrationStatus.DISCONNECTED,
        enabled=True,
    )
    await session.commit()
    return integration


async def _make_order(
    session: AsyncSession, *, order_number: str, total_amount: Decimal = Decimal("500.00")
) -> Order:
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
    return order


async def _make_cashfree_payment(
    session: AsyncSession,
    *,
    order: Order,
    cashfree_order_id: str,
    amount: Decimal | None = None,
    status: PaymentStatus = PaymentStatus.PENDING,
    session_id: str = "session_abc",
) -> Payment:
    payment, _ = await PaymentRepository(session).upsert_by_external_id(
        source_system="cashfree",
        external_id=cashfree_order_id,
        order_id=order.id,
        payment_type=order.payment_type,
        status=status,
        amount=amount if amount is not None else order.total_amount,
        currency=order.currency,
        provider="cashfree",
        payment_metadata={
            "cashfree_order_id": cashfree_order_id,
            "payment_session_id": session_id,
        },
    )
    await session.commit()
    return payment


def _webhook_payload(
    *,
    event_type: str,
    order_id: str,
    cf_payment_id: str | None,
    payment_status: str,
    amount: str = "500.00",
    currency: str = "INR",
    error: dict | None = None,
) -> dict:
    data: dict = {
        "order": {"order_id": order_id, "order_amount": float(amount), "order_currency": currency},
        "payment": {
            "cf_payment_id": cf_payment_id,
            "payment_status": payment_status,
            "payment_amount": float(amount),
            "payment_currency": currency,
            "payment_time": "2026-02-01T10:00:00+05:30",
            "payment_method": {"upi": {"upi_id": "test@upi"}},
        },
        "customer_details": {"customer_id": "c1", "customer_phone": "9999999999"},
    }
    if error:
        data["error_details"] = error
    return {"type": event_type, "event_time": "2026-02-01T10:00:05+05:30", "data": data}


async def _post(
    client: AsyncClient,
    payload: dict,
    *,
    timestamp: str | None = _TIMESTAMP,
    signature: str | None = "__auto__",
    secret: str = _SECRET,
    version: str | None = "2025-01-01",
    raw_body: bytes | None = None,
):
    body = raw_body if raw_body is not None else json.dumps(payload).encode("utf-8")
    if signature == "__auto__":
        signature = _sign(timestamp or "", body, secret) if timestamp else None
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["x-webhook-signature"] = signature
    if timestamp is not None:
        headers["x-webhook-timestamp"] = timestamp
    if version is not None:
        headers["x-webhook-version"] = version
    return await client.post(_URL, content=body, headers=headers)


# --- B. Webhook security --------------------------------------------------


async def test_valid_signature_is_accepted(db_session: AsyncSession, client: AsyncClient) -> None:
    order = await _make_order(db_session, order_number="#AWL1001")
    await _make_cashfree_payment(db_session, order=order, cashfree_order_id="AWL1001")
    await _make_cashfree_integration(db_session)

    payload = _webhook_payload(
        event_type="PAYMENT_SUCCESS_WEBHOOK",
        order_id="AWL1001",
        cf_payment_id="pay_1",
        payment_status="SUCCESS",
    )
    response = await _post(client, payload)

    assert response.status_code == 200
    assert response.json()["success"] is True


async def test_invalid_signature_is_rejected(db_session: AsyncSession, client: AsyncClient) -> None:
    await _make_cashfree_integration(db_session)
    payload = _webhook_payload(
        event_type="PAYMENT_SUCCESS_WEBHOOK",
        order_id="AWL1001",
        cf_payment_id="pay_1",
        payment_status="SUCCESS",
    )
    response = await _post(client, payload, signature="not-a-valid-signature")

    assert response.status_code == 401
    total = await db_session.execute(select(func.count()).select_from(WebhookEvent))
    assert total.scalar_one() == 0


async def test_missing_signature_is_rejected(db_session: AsyncSession, client: AsyncClient) -> None:
    await _make_cashfree_integration(db_session)
    payload = _webhook_payload(
        event_type="PAYMENT_SUCCESS_WEBHOOK",
        order_id="AWL1001",
        cf_payment_id="pay_1",
        payment_status="SUCCESS",
    )
    response = await _post(client, payload, signature=None)
    assert response.status_code == 401


async def test_missing_timestamp_is_rejected(db_session: AsyncSession, client: AsyncClient) -> None:
    await _make_cashfree_integration(db_session)
    payload = _webhook_payload(
        event_type="PAYMENT_SUCCESS_WEBHOOK",
        order_id="AWL1001",
        cf_payment_id="pay_1",
        payment_status="SUCCESS",
    )
    body = json.dumps(payload).encode()
    # A signature computed as if a timestamp were present must still be
    # rejected once the header itself is absent -- the endpoint must
    # never fall back to an empty-string timestamp.
    signature = _sign(_TIMESTAMP, body)
    response = await client.post(
        _URL,
        content=body,
        headers={"x-webhook-signature": signature, "Content-Type": "application/json"},
    )
    assert response.status_code == 401


async def test_modified_payload_is_rejected(db_session: AsyncSession, client: AsyncClient) -> None:
    await _make_cashfree_integration(db_session)
    payload = _webhook_payload(
        event_type="PAYMENT_SUCCESS_WEBHOOK",
        order_id="AWL1001",
        cf_payment_id="pay_1",
        payment_status="SUCCESS",
    )
    body = json.dumps(payload).encode()
    signature = _sign(_TIMESTAMP, body)
    tampered = json.dumps({**payload, "data": {**payload["data"], "order": {"order_id": "HACKED"}}})

    response = await client.post(
        _URL,
        content=tampered.encode(),
        headers={
            "x-webhook-signature": signature,
            "x-webhook-timestamp": _TIMESTAMP,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 401


async def test_webhook_secret_override_takes_precedence_over_client_secret(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    override_secret = "distinct-webhook-secret"
    monkeypatch.setattr(settings, "CASHFREE_WEBHOOK_SECRET", override_secret)
    order = await _make_order(db_session, order_number="#AWL-WHSECRET")
    await _make_cashfree_payment(db_session, order=order, cashfree_order_id="AWL-WHSECRET")
    await _make_cashfree_integration(db_session)

    payload = _webhook_payload(
        event_type="PAYMENT_SUCCESS_WEBHOOK",
        order_id="AWL-WHSECRET",
        cf_payment_id="pay_1",
        payment_status="SUCCESS",
    )
    # Signed with the client secret -- must now be REJECTED since the
    # override takes precedence.
    rejected = await _post(client, payload, secret=_SECRET)
    assert rejected.status_code == 401

    # Signed with the override -- accepted.
    accepted = await _post(client, payload, secret=override_secret)
    assert accepted.status_code == 200


async def test_unconfigured_secret_rejects_every_request(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "CASHFREE_CLIENT_SECRET", None)
    await _make_cashfree_integration(db_session)
    payload = _webhook_payload(
        event_type="PAYMENT_SUCCESS_WEBHOOK",
        order_id="AWL1001",
        cf_payment_id="pay_1",
        payment_status="SUCCESS",
    )
    response = await _post(client, payload)
    assert response.status_code == 401


# --- 11. Malformed payload ---------------------------------------------


async def test_malformed_json_payload_is_rejected(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_cashfree_integration(db_session)
    raw_body = b"{not valid json"
    signature = _sign(_TIMESTAMP, raw_body)
    response = await client.post(
        _URL,
        content=raw_body,
        headers={
            "x-webhook-signature": signature,
            "x-webhook-timestamp": _TIMESTAMP,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 400


async def test_payload_missing_order_id_is_rejected(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_cashfree_integration(db_session)
    payload = {
        "type": "PAYMENT_SUCCESS_WEBHOOK",
        "event_time": "2026-02-01T10:00:05+05:30",
        "data": {"payment": {"cf_payment_id": "pay_1", "payment_status": "SUCCESS"}},
    }
    response = await _post(client, payload)
    assert response.status_code == 400


async def test_unrecognized_type_without_order_id_is_acked_not_rejected(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """Production incident: Cashfree's dashboard "Test" delivery (and any
    other signature-valid `type` this integration doesn't process) is not
    one of the three documented payment-outcome webhooks and carries no
    `data.order` at all -- it must be acked 200 and recorded IGNORED, the
    same as any other unrecognized event, rather than a hard 400. This
    must never require `data.order.order_id`, which only a genuine
    payment-outcome webhook needs (see
    `test_payload_missing_order_id_is_rejected` above, still a 400).
    """
    await _make_cashfree_integration(db_session)
    payload = {
        "type": "SOME_NON_PAYMENT_EVENT",
        "event_time": "2026-02-01T10:00:05+05:30",
        "data": {},
    }
    response = await _post(client, payload)

    assert response.status_code == 200
    assert response.json()["success"] is True

    event = await db_session.scalar(select(WebhookEvent))
    assert event is not None
    assert event.status == "ignored"
    assert event.external_resource_id is None


async def test_payload_with_no_type_field_and_no_order_id_is_acked(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """A signature-valid delivery with no `type` field at all (another
    plausible shape for Cashfree's undocumented dashboard "Test" action)
    must not 400 either -- `event_type` resolves to `None`, which is not
    one of the three recognized payment-outcome types, so it's ignored
    exactly like `test_unrecognized_type_without_order_id_is_acked_not_rejected`.
    """
    await _make_cashfree_integration(db_session)
    payload = {"event_time": "2026-02-01T10:00:05+05:30", "data": {}}
    response = await _post(client, payload)

    assert response.status_code == 200
    assert response.json()["success"] is True

    event = await db_session.scalar(select(WebhookEvent))
    assert event is not None
    assert event.event_type == "unknown"
    assert event.status == "ignored"


async def test_integration_not_configured_returns_404(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    payload = _webhook_payload(
        event_type="PAYMENT_SUCCESS_WEBHOOK",
        order_id="AWL1001",
        cf_payment_id="pay_1",
        payment_status="SUCCESS",
    )
    response = await _post(client, payload)
    assert response.status_code == 404


# --- C. Payment events ----------------------------------------------------


async def test_success_event_marks_payment_and_order_paid(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    order = await _make_order(db_session, order_number="#AWL2001")
    payment = await _make_cashfree_payment(db_session, order=order, cashfree_order_id="AWL2001")
    await _make_cashfree_integration(db_session)

    payload = _webhook_payload(
        event_type="PAYMENT_SUCCESS_WEBHOOK",
        order_id="AWL2001",
        cf_payment_id="pay_1",
        payment_status="SUCCESS",
    )
    response = await _post(client, payload)
    assert response.status_code == 200

    refreshed_payment = await db_session.get(Payment, payment.id)
    assert refreshed_payment.status == PaymentStatus.PAID
    assert refreshed_payment.paid_at is not None
    assert refreshed_payment.external_transaction_id == "pay_1"

    refreshed_order = await db_session.get(Order, order.id)
    assert refreshed_order.payment_status == PaymentStatus.PAID
    assert refreshed_order.status == OrderStatus.CONFIRMED  # PENDING -> CONFIRMED on payment

    transaction = await db_session.scalar(
        select(PaymentTransaction).where(PaymentTransaction.payment_id == payment.id)
    )
    assert transaction is not None
    assert transaction.status == PaymentStatus.PAID
    assert transaction.gateway_transaction_id == "pay_1"
    assert "customer_details" not in (transaction.raw_payload or {})


async def test_failed_event_records_failure_without_marking_order_paid(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    order = await _make_order(db_session, order_number="#AWL2002")
    payment = await _make_cashfree_payment(db_session, order=order, cashfree_order_id="AWL2002")
    await _make_cashfree_integration(db_session)

    payload = _webhook_payload(
        event_type="PAYMENT_FAILED_WEBHOOK",
        order_id="AWL2002",
        cf_payment_id="pay_1",
        payment_status="FAILED",
        error={
            "error_code": "GATEWAY_ERROR",
            "error_description": "Card declined",
            "error_reason": "card_declined",
            "error_source": "cashfree",
        },
    )
    response = await _post(client, payload)
    assert response.status_code == 200

    refreshed_payment = await db_session.get(Payment, payment.id)
    assert refreshed_payment.status == PaymentStatus.FAILED
    assert refreshed_payment.paid_at is None

    refreshed_order = await db_session.get(Order, order.id)
    assert refreshed_order.payment_status == PaymentStatus.PENDING  # never marked paid
    assert refreshed_order.status == OrderStatus.PENDING  # never force-confirmed


async def test_user_dropped_event_is_treated_as_a_failure_not_success(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    order = await _make_order(db_session, order_number="#AWL2003")
    payment = await _make_cashfree_payment(db_session, order=order, cashfree_order_id="AWL2003")
    await _make_cashfree_integration(db_session)

    payload = _webhook_payload(
        event_type="PAYMENT_USER_DROPPED_WEBHOOK",
        order_id="AWL2003",
        cf_payment_id="pay_1",
        payment_status="USER_DROPPED",
    )
    response = await _post(client, payload)
    assert response.status_code == 200

    refreshed_payment = await db_session.get(Payment, payment.id)
    assert refreshed_payment.status == PaymentStatus.FAILED

    refreshed_order = await db_session.get(Order, order.id)
    assert refreshed_order.payment_status == PaymentStatus.PENDING


async def test_unknown_event_type_is_ignored_never_treated_as_success(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    order = await _make_order(db_session, order_number="#AWL2004")
    payment = await _make_cashfree_payment(db_session, order=order, cashfree_order_id="AWL2004")
    await _make_cashfree_integration(db_session)

    payload = _webhook_payload(
        event_type="SOME_FUTURE_WEBHOOK_TYPE",
        order_id="AWL2004",
        cf_payment_id="pay_1",
        payment_status="SUCCESS",  # even if it LOOKS successful, the type isn't recognized
    )
    response = await _post(client, payload)
    assert response.status_code == 200

    refreshed_payment = await db_session.get(Payment, payment.id)
    assert refreshed_payment.status == PaymentStatus.PENDING  # untouched

    event = await db_session.scalar(select(WebhookEvent))
    assert event.status == "ignored"
    assert "unrecognized_event_type" in event.error_message


# --- D. Idempotency ---------------------------------------------------


async def test_duplicate_success_webhook_is_idempotent(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    order = await _make_order(db_session, order_number="#AWL3001")
    payment = await _make_cashfree_payment(db_session, order=order, cashfree_order_id="AWL3001")
    await _make_cashfree_integration(db_session)

    payload = _webhook_payload(
        event_type="PAYMENT_SUCCESS_WEBHOOK",
        order_id="AWL3001",
        cf_payment_id="pay_1",
        payment_status="SUCCESS",
    )
    first = await _post(client, payload)
    second = await _post(client, payload)

    assert first.status_code == 200
    assert second.status_code == 200

    total_events = await db_session.execute(select(func.count()).select_from(WebhookEvent))
    assert total_events.scalar_one() == 1

    total_transactions = await db_session.execute(
        select(func.count())
        .select_from(PaymentTransaction)
        .where(PaymentTransaction.payment_id == payment.id)
    )
    assert total_transactions.scalar_one() == 1


async def test_duplicate_failed_webhook_is_idempotent(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    order = await _make_order(db_session, order_number="#AWL3002")
    await _make_cashfree_payment(db_session, order=order, cashfree_order_id="AWL3002")
    await _make_cashfree_integration(db_session)

    payload = _webhook_payload(
        event_type="PAYMENT_FAILED_WEBHOOK",
        order_id="AWL3002",
        cf_payment_id="pay_1",
        payment_status="FAILED",
    )
    await _post(client, payload)
    await _post(client, payload)

    total_events = await db_session.execute(select(func.count()).select_from(WebhookEvent))
    assert total_events.scalar_one() == 1


async def test_a_second_distinct_payment_attempt_is_not_treated_as_a_duplicate(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """Different `cf_payment_id` values for the same order_id (a customer
    retrying with a different instrument) are genuinely different events
    — each recorded, not deduplicated against each other.
    """
    order = await _make_order(db_session, order_number="#AWL3003")
    payment = await _make_cashfree_payment(db_session, order=order, cashfree_order_id="AWL3003")
    await _make_cashfree_integration(db_session)

    failed = _webhook_payload(
        event_type="PAYMENT_FAILED_WEBHOOK",
        order_id="AWL3003",
        cf_payment_id="pay_1",
        payment_status="FAILED",
    )
    success = _webhook_payload(
        event_type="PAYMENT_SUCCESS_WEBHOOK",
        order_id="AWL3003",
        cf_payment_id="pay_2",
        payment_status="SUCCESS",
    )
    await _post(client, failed)
    await _post(client, success)

    refreshed = await db_session.get(Payment, payment.id)
    assert refreshed.status == PaymentStatus.PAID  # the later, genuinely-successful retry wins

    total_transactions = await db_session.execute(
        select(func.count())
        .select_from(PaymentTransaction)
        .where(PaymentTransaction.payment_id == payment.id)
    )
    assert total_transactions.scalar_one() == 2


async def test_paid_payment_is_never_downgraded_by_a_later_failed_event(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    order = await _make_order(db_session, order_number="#AWL3004")
    payment = await _make_cashfree_payment(db_session, order=order, cashfree_order_id="AWL3004")
    await _make_cashfree_integration(db_session)

    success = _webhook_payload(
        event_type="PAYMENT_SUCCESS_WEBHOOK",
        order_id="AWL3004",
        cf_payment_id="pay_1",
        payment_status="SUCCESS",
    )
    late_failed = _webhook_payload(
        event_type="PAYMENT_FAILED_WEBHOOK",
        order_id="AWL3004",
        cf_payment_id="pay_2",
        payment_status="FAILED",
    )
    await _post(client, success)
    await _post(client, late_failed)

    refreshed = await db_session.get(Payment, payment.id)
    assert refreshed.status == PaymentStatus.PAID  # never downgraded

    refreshed_order = await db_session.get(Order, order.id)
    assert refreshed_order.payment_status == PaymentStatus.PAID


# --- E. Amount validation -----------------------------------------------


async def test_matching_amount_is_accepted(db_session: AsyncSession, client: AsyncClient) -> None:
    order = await _make_order(db_session, order_number="#AWL4001", total_amount=Decimal("749.50"))
    payment = await _make_cashfree_payment(
        db_session, order=order, cashfree_order_id="AWL4001", amount=Decimal("749.50")
    )
    await _make_cashfree_integration(db_session)

    payload = _webhook_payload(
        event_type="PAYMENT_SUCCESS_WEBHOOK",
        order_id="AWL4001",
        cf_payment_id="pay_1",
        payment_status="SUCCESS",
        amount="749.50",
    )
    response = await _post(client, payload)
    assert response.status_code == 200

    refreshed = await db_session.get(Payment, payment.id)
    assert refreshed.status == PaymentStatus.PAID


async def test_mismatched_amount_is_rejected_and_not_marked_paid(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    order = await _make_order(db_session, order_number="#AWL4002", total_amount=Decimal("999.00"))
    payment = await _make_cashfree_payment(
        db_session, order=order, cashfree_order_id="AWL4002", amount=Decimal("999.00")
    )
    await _make_cashfree_integration(db_session)

    payload = _webhook_payload(
        event_type="PAYMENT_SUCCESS_WEBHOOK",
        order_id="AWL4002",
        cf_payment_id="pay_1",
        payment_status="SUCCESS",
        amount="1.00",  # attacker-style underpayment
    )
    response = await _post(client, payload)
    assert response.status_code == 200  # still acked, so Cashfree doesn't hammer retries

    refreshed = await db_session.get(Payment, payment.id)
    assert refreshed.status == PaymentStatus.PENDING  # never marked paid

    refreshed_order = await db_session.get(Order, order.id)
    assert refreshed_order.payment_status == PaymentStatus.PENDING

    event = await db_session.scalar(select(WebhookEvent))
    assert event.status == "ignored"
    assert "amount_mismatch" in event.error_message


async def test_mismatched_currency_is_rejected(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    order = await _make_order(db_session, order_number="#AWL4003")
    payment = await _make_cashfree_payment(db_session, order=order, cashfree_order_id="AWL4003")
    await _make_cashfree_integration(db_session)

    payload = _webhook_payload(
        event_type="PAYMENT_SUCCESS_WEBHOOK",
        order_id="AWL4003",
        cf_payment_id="pay_1",
        payment_status="SUCCESS",
        currency="USD",
    )
    response = await _post(client, payload)
    assert response.status_code == 200

    refreshed = await db_session.get(Payment, payment.id)
    assert refreshed.status == PaymentStatus.PENDING


# --- F. Order mapping ------------------------------------------------


async def test_unknown_cashfree_order_id_does_not_fabricate_a_match(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_cashfree_integration(db_session)
    payload = _webhook_payload(
        event_type="PAYMENT_SUCCESS_WEBHOOK",
        order_id="AWL-DOES-NOT-EXIST",
        cf_payment_id="pay_1",
        payment_status="SUCCESS",
    )
    response = await _post(client, payload)
    assert response.status_code == 200

    total_payments = await db_session.execute(select(func.count()).select_from(Payment))
    assert total_payments.scalar_one() == 0

    event = await db_session.scalar(select(WebhookEvent))
    assert event.status == "ignored"
    assert "unknown_cashfree_order" in event.error_message


# --- G. State transitions (see also idempotency tests above) -----------


async def test_pending_to_success_transition(db_session: AsyncSession, client: AsyncClient) -> None:
    order = await _make_order(db_session, order_number="#AWL5001")
    payment = await _make_cashfree_payment(db_session, order=order, cashfree_order_id="AWL5001")
    assert payment.status == PaymentStatus.PENDING
    await _make_cashfree_integration(db_session)

    payload = _webhook_payload(
        event_type="PAYMENT_SUCCESS_WEBHOOK",
        order_id="AWL5001",
        cf_payment_id="pay_1",
        payment_status="SUCCESS",
    )
    await _post(client, payload)

    refreshed = await db_session.get(Payment, payment.id)
    assert refreshed.status == PaymentStatus.PAID


async def test_pending_to_failed_transition(db_session: AsyncSession, client: AsyncClient) -> None:
    order = await _make_order(db_session, order_number="#AWL5002")
    payment = await _make_cashfree_payment(db_session, order=order, cashfree_order_id="AWL5002")
    await _make_cashfree_integration(db_session)

    payload = _webhook_payload(
        event_type="PAYMENT_FAILED_WEBHOOK",
        order_id="AWL5002",
        cf_payment_id="pay_1",
        payment_status="FAILED",
    )
    await _post(client, payload)

    refreshed = await db_session.get(Payment, payment.id)
    assert refreshed.status == PaymentStatus.FAILED


async def test_pending_to_dropped_transition(db_session: AsyncSession, client: AsyncClient) -> None:
    order = await _make_order(db_session, order_number="#AWL5003")
    payment = await _make_cashfree_payment(db_session, order=order, cashfree_order_id="AWL5003")
    await _make_cashfree_integration(db_session)

    payload = _webhook_payload(
        event_type="PAYMENT_USER_DROPPED_WEBHOOK",
        order_id="AWL5003",
        cf_payment_id="pay_1",
        payment_status="USER_DROPPED",
    )
    await _post(client, payload)

    refreshed = await db_session.get(Payment, payment.id)
    assert refreshed.status == PaymentStatus.FAILED


async def test_failed_payment_can_still_succeed_on_a_genuine_retry(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """FAILED -> SUCCESS is allowed when driven by a trusted (signature
    -verified) event for a genuinely new payment attempt (spec: "do not
    allow FAILED -> SUCCESS unless a trusted Cashfree event... proves
    it" — this webhook IS that trusted event).
    """
    order = await _make_order(db_session, order_number="#AWL5004")
    payment = await _make_cashfree_payment(
        db_session, order=order, cashfree_order_id="AWL5004", status=PaymentStatus.FAILED
    )
    await _make_cashfree_integration(db_session)

    payload = _webhook_payload(
        event_type="PAYMENT_SUCCESS_WEBHOOK",
        order_id="AWL5004",
        cf_payment_id="pay_retry",
        payment_status="SUCCESS",
    )
    response = await _post(client, payload)
    assert response.status_code == 200

    refreshed = await db_session.get(Payment, payment.id)
    assert refreshed.status == PaymentStatus.PAID


# --- H. Database/update failure handling ---------------------------------


async def test_processing_failure_returns_5xx_and_marks_event_failed(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    order = await _make_order(db_session, order_number="#AWL6001")
    await _make_cashfree_payment(db_session, order=order, cashfree_order_id="AWL6001")
    await _make_cashfree_integration(db_session)

    from app.services.cashfree_payment_service import CashfreePaymentService

    async def _boom(self, **kwargs):  # noqa: ANN001, ARG001
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(CashfreePaymentService, "apply_payment_event", _boom)

    payload = _webhook_payload(
        event_type="PAYMENT_SUCCESS_WEBHOOK",
        order_id="AWL6001",
        cf_payment_id="pay_1",
        payment_status="SUCCESS",
    )
    response = await _post(client, payload)
    assert response.status_code == 500

    event = await db_session.scalar(select(WebhookEvent))
    assert event is not None
    assert event.status == "failed"
    assert "simulated database failure" in event.error_message
