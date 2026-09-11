"""Tests for the Shiprocket-style address-validation feature:
- `app.integrations.address_validation.heuristic_provider` (the default,
  no-credentials scorer)
- `app.services.address_validation_service.AddressValidationService`
  (persistence, staleness/hash-based revalidation policy)
- `OrderService.upsert_synced_order`/`update_shipping_address` triggering
  validation as a write-time side effect
- `POST /orders/{id}/validate-address` (the explicit single-order action)
- that listing endpoints (`GET /orders`, `GET /shipments/queue`) never
  call the validation provider -- the N+1 prevention requirement.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.db.session import get_db
from app.integrations.address_validation.heuristic_provider import (
    HeuristicAddressValidationProvider,
    _score_address,
)
from app.integrations.address_validation.provider import AddressValidationResult
from app.main import app
from app.models.enums import (
    AddressValidationStatus,
    FulfillmentStatus,
    OrderStatus,
    PaymentStatus,
    PaymentType,
)
from app.repositories.order import OrderRepository
from app.services.address_validation_service import AddressValidationService
from app.services.order_service import OrderService
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import bearer_client, make_role, make_user

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clear_get_db_override():
    yield
    app.dependency_overrides.clear()


VALID_ADDRESS = {
    "line1": "273 House No Ganpati Chowk, Patidar Dharmsala",
    "line2": "Ganpati Chowk",
    "city": "Mandsaur",
    "state": "Madhya Pradesh",
    "country": "India",
    "pin_code": "458556",
    "contact_name": "Ravi Kumar",
    "contact_phone": "9990000001",
    "is_default": False,
}

AMBIGUOUS_ADDRESS = {
    "line1": "Near BS",  # short (<8 chars) -> -15
    "line2": None,
    "city": None,  # missing -> -25
    "state": None,  # missing -> -10
    "country": "India",
    "pin_code": "458556",
    "contact_name": None,
    "contact_phone": None,
    "is_default": False,
}  # total: 100 - 15 - 25 - 10 = 50 -> AMBIGUOUS band

JUNK_ADDRESS = {
    "line1": "test address asdf",
    "line2": None,
    "city": "—",
    "state": None,
    "country": "India",
    "pin_code": "",
    "contact_name": None,
    "contact_phone": None,
    "is_default": False,
}


# --- Heuristic provider (pure scoring logic, no I/O) ---------------------


def test_heuristic_provider_scores_a_complete_address_as_valid() -> None:
    score, issues = _score_address(VALID_ADDRESS)
    assert score >= 80
    assert issues == []


def test_heuristic_provider_scores_a_sparse_address_as_ambiguous() -> None:
    score, issues = _score_address(AMBIGUOUS_ADDRESS)
    assert 40 <= score < 80
    assert "Missing state" in issues


def test_heuristic_provider_scores_a_junk_address_as_junk() -> None:
    score, issues = _score_address(JUNK_ADDRESS)
    assert score < 40
    assert any("placeholder" in i.lower() for i in issues)
    assert any("PIN" in i for i in issues)


async def test_heuristic_provider_validate_batch_returns_one_result_per_address_in_order() -> (
    None
):
    provider = HeuristicAddressValidationProvider()
    results = await provider.validate_batch([VALID_ADDRESS, JUNK_ADDRESS, AMBIGUOUS_ADDRESS])
    assert len(results) == 3
    assert results[0].status == AddressValidationStatus.VALID
    assert results[1].status == AddressValidationStatus.JUNK
    assert results[2].status == AddressValidationStatus.AMBIGUOUS
    # Every result carries a human-readable reason -- never a bare number.
    assert all(r.reason for r in results)


# --- AddressValidationService: persistence, staleness, hash policy ------


async def _make_synced_order(
    session: AsyncSession,
    *,
    order_number: str,
    shipping_address: dict | None,
    external_id: str | None = None,
) -> tuple:
    order, _created = await OrderService(session).upsert_synced_order(
        source_system="shopify",
        external_id=external_id or order_number,
        order_number=order_number,
        order_datetime=datetime.now(UTC),
        currency="INR",
        payment_type=PaymentType.PREPAID,
        payment_status=PaymentStatus.PENDING,
        fulfillment_status=FulfillmentStatus.UNFULFILLED,
        shipping_charge=Decimal("0"),
        subtotal=Decimal("500.00"),
        discount_amount=Decimal("0"),
        tax_amount=Decimal("0"),
        total_amount=Decimal("500.00"),
        shipping_address=shipping_address,
        billing_address=None,
        items=[],
        customer_external_id=None,
        is_cancelled=False,
    )
    return await OrderRepository(session).get_by_id(order.id), order


async def test_missing_shipping_address_is_never_validated(db_session: AsyncSession) -> None:
    order, _ = await _make_synced_order(
        db_session, order_number="AWL-AV-1", shipping_address=None
    )
    assert order.shipping_address_validation_status is None
    assert order.shipping_address_validation_score is None

    service = AddressValidationService(db_session)
    assert service.needs_validation(order) is False
    await service.validate_order(order)
    await db_session.refresh(order)
    assert order.shipping_address_validation_status is None


async def test_validation_persists_status_score_reason_and_timestamp(
    db_session: AsyncSession,
) -> None:
    order, _ = await _make_synced_order(
        db_session, order_number="AWL-AV-2", shipping_address=VALID_ADDRESS
    )
    await db_session.refresh(order)

    assert order.shipping_address_validation_status == AddressValidationStatus.VALID
    assert order.shipping_address_validation_score is not None
    assert order.shipping_address_validation_score >= 80
    assert order.shipping_address_validation_reason is not None
    assert order.shipping_address_validated_at is not None
    assert order.shipping_address_validation_hash is not None


async def test_unchanged_address_never_revalidates_or_calls_the_provider_again(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The N+1-prevention contract at the service level: calling
    `validate_order` a second time for an order whose address hasn't
    changed must be a real no-op -- no provider call at all.
    """
    order, _ = await _make_synced_order(
        db_session, order_number="AWL-AV-3", shipping_address=VALID_ADDRESS
    )
    await db_session.refresh(order)
    first_validated_at = order.shipping_address_validated_at

    call_count = {"n": 0}

    class _PoisonProvider:
        async def validate(self, address):  # noqa: ANN001, ANN202
            call_count["n"] += 1
            raise AssertionError("provider must not be called for an unchanged address")

        async def validate_batch(self, addresses):  # noqa: ANN001, ANN202
            call_count["n"] += 1
            raise AssertionError("provider must not be called for an unchanged address")

    monkeypatch.setattr(
        "app.services.address_validation_service.get_address_validation_provider",
        lambda: _PoisonProvider(),
    )

    service = AddressValidationService(db_session)
    assert service.needs_validation(order) is False
    await service.validate_order(order)  # must short-circuit before touching the provider

    await db_session.refresh(order)
    assert call_count["n"] == 0
    assert order.shipping_address_validated_at == first_validated_at


async def test_address_change_triggers_revalidation(db_session: AsyncSession) -> None:
    order, _ = await _make_synced_order(
        db_session, order_number="AWL-AV-4", shipping_address=JUNK_ADDRESS
    )
    await db_session.refresh(order)
    assert order.shipping_address_validation_status == AddressValidationStatus.JUNK
    first_hash = order.shipping_address_validation_hash

    # A real Shopify resync with a genuinely different (now complete)
    # address -- same order_number/external_id, so this UPDATES the
    # existing row rather than creating a second one.
    await OrderService(db_session).upsert_synced_order(
        source_system="shopify",
        external_id="AWL-AV-4",
        order_number="AWL-AV-4",
        order_datetime=datetime.now(UTC),
        currency="INR",
        payment_type=PaymentType.PREPAID,
        payment_status=PaymentStatus.PENDING,
        fulfillment_status=FulfillmentStatus.UNFULFILLED,
        shipping_charge=Decimal("0"),
        subtotal=Decimal("500.00"),
        discount_amount=Decimal("0"),
        tax_amount=Decimal("0"),
        total_amount=Decimal("500.00"),
        shipping_address=VALID_ADDRESS,
        billing_address=None,
        items=[],
        customer_external_id=None,
        is_cancelled=False,
    )

    updated = await OrderRepository(db_session).get_by_id(order.id)
    assert updated is not None
    assert updated.shipping_address_validation_status == AddressValidationStatus.VALID
    assert updated.shipping_address_validation_hash != first_hash


async def test_manual_shipping_address_edit_triggers_revalidation(
    db_session: AsyncSession,
) -> None:
    role = await make_role(db_session, name="OPS_AV", permission_codes=["orders.update"])
    user = await make_user(db_session, email="ops-av@example.com", role=role)
    order, _ = await _make_synced_order(
        db_session, order_number="AWL-AV-5", shipping_address=JUNK_ADDRESS
    )

    await OrderService(db_session).update_shipping_address(
        order.id, actor=user, address=VALID_ADDRESS
    )

    updated = await OrderRepository(db_session).get_by_id(order.id)
    assert updated is not None
    assert updated.shipping_address_validation_status == AddressValidationStatus.VALID


async def test_provider_failure_never_marks_the_address_valid(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even for a genuinely well-formed address, a provider that raises
    must result in UNKNOWN, never VALID -- the exact "do not silently
    classify as VALID when the provider failed" requirement.
    """

    class _FailingProvider:
        async def validate(self, address):  # noqa: ANN001, ANN202
            raise RuntimeError("provider is down")

        async def validate_batch(self, addresses):  # noqa: ANN001, ANN202
            raise RuntimeError("provider is down")

    monkeypatch.setattr(
        "app.services.address_validation_service.get_address_validation_provider",
        lambda: _FailingProvider(),
    )

    order, _ = await _make_synced_order(
        db_session, order_number="AWL-AV-6", shipping_address=VALID_ADDRESS
    )
    await db_session.refresh(order)

    assert order.shipping_address_validation_status == AddressValidationStatus.UNKNOWN
    assert order.shipping_address_validation_score == 0
    assert "unexpected error" in (order.shipping_address_validation_reason or "").lower()


async def test_provider_returning_unknown_result_is_stored_as_unknown_not_valid(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _UnknownProvider:
        async def validate(self, address):  # noqa: ANN001, ANN202
            return AddressValidationResult(
                status=AddressValidationStatus.UNKNOWN, score=0, reason="Provider timed out."
            )

        async def validate_batch(self, addresses):  # noqa: ANN001, ANN202
            return [await self.validate(a) for a in addresses]

    monkeypatch.setattr(
        "app.services.address_validation_service.get_address_validation_provider",
        lambda: _UnknownProvider(),
    )

    order, _ = await _make_synced_order(
        db_session, order_number="AWL-AV-7", shipping_address=VALID_ADDRESS
    )
    await db_session.refresh(order)
    assert order.shipping_address_validation_status == AddressValidationStatus.UNKNOWN


async def test_address_removed_clears_the_stale_validation_result(
    db_session: AsyncSession,
) -> None:
    order, _ = await _make_synced_order(
        db_session, order_number="AWL-AV-8", shipping_address=VALID_ADDRESS
    )
    await db_session.refresh(order)
    assert order.shipping_address_validation_status == AddressValidationStatus.VALID

    await AddressValidationService(db_session).validate_order(
        (await OrderService(db_session).get_order(order.id)), force=False
    )
    order.shipping_address = None
    await db_session.commit()
    await AddressValidationService(db_session).validate_order(order)

    updated = await OrderRepository(db_session).get_by_id(order.id)
    assert updated is not None
    assert updated.shipping_address_validation_status is None


# --- POST /orders/{id}/validate-address ----------------------------------


async def test_validate_address_endpoint_updates_and_returns_the_order(
    db_session: AsyncSession,
) -> None:
    role = await make_role(db_session, name="OPS_AV_EP", permission_codes=["orders.update"])
    user = await make_user(db_session, email="ops-av-ep@example.com", role=role)
    order, _ = await _make_synced_order(
        db_session, order_number="AWL-AV-9", shipping_address=JUNK_ADDRESS
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.post(f"/api/v1/orders/{order.id}/validate-address")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["shipping_address_validation_status"] == "junk"
        assert data["shipping_address_validation_score"] is not None


async def test_validate_address_endpoint_requires_orders_update_permission(
    db_session: AsyncSession,
) -> None:
    role = await make_role(db_session, name="OPS_AV_RO", permission_codes=["orders.read"])
    user = await make_user(db_session, email="ops-av-ro@example.com", role=role)
    order, _ = await _make_synced_order(
        db_session, order_number="AWL-AV-10", shipping_address=VALID_ADDRESS
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.post(f"/api/v1/orders/{order.id}/validate-address")
        assert response.status_code == 403


# --- N+1 prevention: listing endpoints never call the provider ----------


async def test_orders_list_never_calls_the_validation_provider(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    role = await make_role(db_session, name="OPS_AV_LIST", permission_codes=["orders.read"])
    user = await make_user(db_session, email="ops-av-list@example.com", role=role)
    for i in range(5):
        await _make_synced_order(
            db_session, order_number=f"AWL-AV-LIST-{i}", shipping_address=VALID_ADDRESS
        )

    class _PoisonProvider:
        async def validate_batch(self, addresses):  # noqa: ANN001, ANN202
            raise AssertionError("GET /orders must never call the validation provider")

    monkeypatch.setattr(
        "app.services.address_validation_service.get_address_validation_provider",
        lambda: (_ for _ in ()).throw(
            AssertionError("GET /orders must never resolve a validation provider")
        ),
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/orders", params={"page_size": 50})
        assert response.status_code == 200
        rows = response.json()["data"]
        assert len(rows) >= 5
        # Every row carries whatever was persisted at write time -- read
        # straight off the `Order` row, no extra query, no provider call.
        assert all("shipping_address_validation_status" in r for r in rows)
        assert any(r["shipping_address_validation_status"] == "valid" for r in rows)


async def test_shipment_queue_exposes_validation_status_with_zero_extra_provider_calls(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    role = await make_role(
        db_session, name="OPS_AV_QUEUE", permission_codes=["shipments.read", "orders.update"]
    )
    user = await make_user(db_session, email="ops-av-queue@example.com", role=role)
    order, _ = await _make_synced_order(
        db_session, order_number="AWL-AV-QUEUE-1", shipping_address=JUNK_ADDRESS
    )
    await OrderService(db_session).transition_status(
        order.id,
        new_status=OrderStatus.CONFIRMED,
        actor=None,
        description="Confirmed for queue test.",
    )

    monkeypatch.setattr(
        "app.services.address_validation_service.get_address_validation_provider",
        lambda: (_ for _ in ()).throw(
            AssertionError("GET /shipments/queue must never resolve a validation provider")
        ),
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/shipments/queue")
        assert response.status_code == 200
        rows = response.json()["data"]
        row = next(r for r in rows if r["order_id"] == str(order.id))
        assert row["shipping_address_validation_status"] == "junk"
        assert row["shipping_address_validation_score"] is not None
