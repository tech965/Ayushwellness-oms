"""CashfreeSyncService: bulk transaction sync (`POST /recon`) and
settlement sync (`POST /settlements`) -- pagination, idempotency,
provider isolation, PAID-amount semantics, and API-failure handling.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.core.config import settings
from app.integrations.cashfree.client import CashfreeClient
from app.integrations.cashfree.errors import CashfreeApiError
from app.integrations.registry import clear_adapters
from app.models.enums import FulfillmentStatus, PaymentStatus, PaymentType
from app.repositories.cashfree_settlement import CashfreeSettlementRepository
from app.repositories.payment import PaymentRepository
from app.services.cashfree_sync_service import CashfreeSyncService
from app.services.order_service import OrderService
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_SECRET = "test-cashfree-client-secret"
_FROM = datetime(2026, 9, 3, 0, 0, 0, tzinfo=UTC)
_TO = datetime(2026, 9, 3, 23, 59, 59, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _configure_cashfree(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "CASHFREE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(settings, "CASHFREE_CLIENT_SECRET", _SECRET)
    monkeypatch.setattr(CashfreeClient, "aclose", lambda self: _noop())
    yield
    clear_adapters()


async def _noop() -> None:
    return None


async def _make_order(
    session: AsyncSession,
    *,
    order_number: str,
    total_amount: Decimal = Decimal("500.00"),
    payment_type: PaymentType = PaymentType.PREPAID,
) -> uuid.UUID:
    order = await OrderService(session).create_order(
        actor=None,
        order_number=order_number,
        customer_id=None,
        order_datetime=datetime.now(UTC),
        currency="INR",
        payment_type=payment_type,
        shipping_charge=0,
        notes=None,
        items=[],
    )
    from app.repositories.order import OrderRepository

    await OrderRepository(session).update(order, total_amount=total_amount)
    await session.commit()
    return order.id


def _recon_row(
    *,
    order_id: str,
    cf_payment_id: str,
    status: str = "SUCCESS",
    amount: str = "500.00",
    event_type: str | None = "PAYMENT",
) -> dict:
    return {
        "order_id": order_id,
        "cf_payment_id": cf_payment_id,
        "payment_amount": amount,
        "payment_currency": "INR",
        "payment_time": "2026-09-03T10:00:00Z",
        "status": status,
        "event_type": event_type,
        "bank_reference": "BANKREF1",
    }


def _paginated_stub(pages: list[list[dict]]):
    """Monkeypatches `CashfreeClient.get_reconciliation` to serve `pages`
    in order, keyed off the `cursor` the caller actually passes back
    (`None` -> page 0, `"cursor-N"` -> page N) -- not a global call
    counter, so this stub behaves correctly even when the same `fake` is
    invoked across two independent, top-level sync calls in one test
    (each of which legitimately starts from `cursor=None` again).
    """
    calls: list[dict] = []

    async def _fake(self, *, start_date, end_date, cursor=None, limit=1000):  # noqa: ANN001
        calls.append({"start_date": start_date, "end_date": end_date, "cursor": cursor})
        index = int(cursor.split("-")[1]) if cursor else 0
        rows = pages[index]
        next_cursor = f"cursor-{index + 1}" if index + 1 < len(pages) else None
        return {"data": rows, "cursor": next_cursor}

    return _fake, calls


# --- Transaction sync: basic + pagination -----------------------------


async def test_sync_transactions_creates_a_new_payment_from_a_single_page(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    order_id = await _make_order(
        db_session, order_number="#AWLSYNC1", total_amount=Decimal("500.00")
    )
    fake, calls = _paginated_stub(
        [[_recon_row(order_id="AWLSYNC1", cf_payment_id="pay_1", amount="500.00")]]
    )
    monkeypatch.setattr(CashfreeClient, "get_reconciliation", fake)

    result = await CashfreeSyncService(db_session).sync_transactions(_FROM, _TO)

    assert result.fetched == 1
    assert result.processed == 1
    assert result.applied == 1
    assert result.duplicates == 0
    assert result.failures == 0
    assert len(calls) == 1

    payment = await PaymentRepository(db_session).get_by_source_external_id(
        source_system="cashfree", external_id="AWLSYNC1"
    )
    assert payment is not None
    assert payment.status == PaymentStatus.PAID
    assert payment.amount == Decimal("500.00")
    assert payment.order_id == order_id


async def test_sync_transactions_follows_cursor_across_multiple_pages(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _make_order(db_session, order_number="#AWLSYNC2A", total_amount=Decimal("100.00"))
    await _make_order(db_session, order_number="#AWLSYNC2B", total_amount=Decimal("200.00"))
    fake, calls = _paginated_stub(
        [
            [_recon_row(order_id="AWLSYNC2A", cf_payment_id="pay_a", amount="100.00")],
            [_recon_row(order_id="AWLSYNC2B", cf_payment_id="pay_b", amount="200.00")],
        ]
    )
    monkeypatch.setattr(CashfreeClient, "get_reconciliation", fake)

    result = await CashfreeSyncService(db_session).sync_transactions(_FROM, _TO)

    assert len(calls) == 2
    assert calls[1]["cursor"] == "cursor-1"
    assert result.fetched == 2
    assert result.applied == 2


async def test_sync_transactions_stops_on_an_empty_page(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, calls = _paginated_stub([[]])
    monkeypatch.setattr(CashfreeClient, "get_reconciliation", fake)

    result = await CashfreeSyncService(db_session).sync_transactions(_FROM, _TO)

    assert len(calls) == 1
    assert result.fetched == 0
    assert result.applied == 0


# --- Idempotency / duplicate protection --------------------------------


async def test_running_sync_twice_for_the_same_range_does_not_duplicate(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _make_order(db_session, order_number="#AWLSYNC3", total_amount=Decimal("500.00"))
    fake, _ = _paginated_stub(
        [[_recon_row(order_id="AWLSYNC3", cf_payment_id="pay_dup", amount="500.00")]]
    )
    monkeypatch.setattr(CashfreeClient, "get_reconciliation", fake)

    service = CashfreeSyncService(db_session)
    first = await service.sync_transactions(_FROM, _TO)
    second = await service.sync_transactions(_FROM, _TO)

    assert first.applied == 1
    assert second.applied == 0
    assert second.duplicates == 1

    from app.models.payment import Payment, PaymentTransaction
    from sqlalchemy import func, select

    payment_count = await db_session.scalar(
        select(func.count()).select_from(Payment).where(Payment.external_id == "AWLSYNC3")
    )
    txn_count = await db_session.scalar(select(func.count()).select_from(PaymentTransaction))
    assert payment_count == 1
    assert txn_count == 1


async def test_sync_never_downgrades_an_already_paid_payment(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _make_order(db_session, order_number="#AWLSYNC4", total_amount=Decimal("500.00"))
    fake, _ = _paginated_stub(
        [[_recon_row(order_id="AWLSYNC4", cf_payment_id="pay_ok", status="SUCCESS")]]
    )
    monkeypatch.setattr(CashfreeClient, "get_reconciliation", fake)
    service = CashfreeSyncService(db_session)
    await service.sync_transactions(_FROM, _TO)

    # A later, weaker signal (e.g. a stale/out-of-order recon row) for
    # the SAME payment id must never downgrade an already-PAID payment.
    fake2, _ = _paginated_stub(
        [[_recon_row(order_id="AWLSYNC4", cf_payment_id="pay_dropped", status="FAILED")]]
    )
    monkeypatch.setattr(CashfreeClient, "get_reconciliation", fake2)
    await service.sync_transactions(_FROM, _TO)

    payment = await PaymentRepository(db_session).get_by_source_external_id(
        source_system="cashfree", external_id="AWLSYNC4"
    )
    assert payment.status == PaymentStatus.PAID


# --- REFUND/DISPUTE rows are skipped, never applied as a payment -------


async def test_refund_and_dispute_rows_are_skipped_not_applied(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, _ = _paginated_stub(
        [
            [
                _recon_row(order_id="X", cf_payment_id="r1", event_type="REFUND"),
                _recon_row(order_id="Y", cf_payment_id="d1", event_type="DISPUTE"),
            ]
        ]
    )
    monkeypatch.setattr(CashfreeClient, "get_reconciliation", fake)

    result = await CashfreeSyncService(db_session).sync_transactions(_FROM, _TO)

    assert result.fetched == 2
    assert result.skipped == 2
    assert result.applied == 0


# --- Provider isolation: sync never touches Shopify/COD payment rows ---


async def test_sync_does_not_touch_shopify_cod_payment_rows(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    cod_order_id = await _make_order(
        db_session, order_number="#AWLCOD1", total_amount=Decimal("999.00"),
        payment_type=PaymentType.COD,
    )
    from app.repositories.order import OrderRepository

    order_repo = OrderRepository(db_session)
    order = await order_repo.get_by_id(cod_order_id)
    await order_repo.update(order, fulfillment_status=FulfillmentStatus.FULFILLED)
    cod_payment_before = (await PaymentRepository(db_session).list_for_order(cod_order_id))[0]

    await _make_order(db_session, order_number="#AWLPREPAID1", total_amount=Decimal("500.00"))
    fake, _ = _paginated_stub(
        [[_recon_row(order_id="AWLPREPAID1", cf_payment_id="pay_prepaid", amount="500.00")]]
    )
    monkeypatch.setattr(CashfreeClient, "get_reconciliation", fake)

    await CashfreeSyncService(db_session).sync_transactions(_FROM, _TO)

    cod_payment_after = (await PaymentRepository(db_session).list_for_order(cod_order_id))[0]
    assert cod_payment_after.status == cod_payment_before.status
    assert cod_payment_after.provider != "cashfree"

    prepaid_payment = await PaymentRepository(db_session).get_by_source_external_id(
        source_system="cashfree", external_id="AWLPREPAID1"
    )
    assert prepaid_payment is not None
    assert prepaid_payment.provider == "cashfree"


# --- API failure handling -----------------------------------------------


async def test_sync_transactions_raises_a_clean_error_on_api_failure(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fail(self, *, start_date, end_date, cursor=None, limit=1000):  # noqa: ANN001
        raise CashfreeApiError("boom", error_type="http_500", status_code=500)

    monkeypatch.setattr(CashfreeClient, "get_reconciliation", _fail)

    from app.core.exceptions import IntegrationError

    with pytest.raises(IntegrationError) as exc_info:
        await CashfreeSyncService(db_session).sync_transactions(_FROM, _TO)
    assert exc_info.value.details["error_type"] == "http_500"


async def test_sync_transactions_not_configured_raises_cleanly(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "CASHFREE_CLIENT_ID", None)
    monkeypatch.setattr(settings, "CASHFREE_CLIENT_SECRET", None)

    from app.core.exceptions import IntegrationError

    with pytest.raises(IntegrationError) as exc_info:
        await CashfreeSyncService(db_session).sync_transactions(_FROM, _TO)
    assert exc_info.value.details["error_type"] == "not_configured"


async def test_one_bad_row_does_not_abort_the_rest_of_the_page(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _make_order(db_session, order_number="#AWLSYNC5", total_amount=Decimal("500.00"))
    # A row with a genuinely malformed amount alongside a valid one --
    # the malformed one must not prevent the valid one from applying.
    bad_row = _recon_row(order_id="AWLSYNC5-BAD", cf_payment_id="pay_bad", amount="not-a-number")
    good_row = _recon_row(order_id="AWLSYNC5", cf_payment_id="pay_good", amount="500.00")
    fake, _ = _paginated_stub([[bad_row, good_row]])
    monkeypatch.setattr(CashfreeClient, "get_reconciliation", fake)

    result = await CashfreeSyncService(db_session).sync_transactions(_FROM, _TO)

    assert result.processed == 2
    assert result.failures == 1
    assert result.applied == 1


# --- Settlement sync -----------------------------------------------------


def _settlement_row(
    *, cf_settlement_id: str, status: str = "SUCCESS", amount_settled: str = "9500.00"
) -> dict:
    return {
        "cf_settlement_id": cf_settlement_id,
        "status": status,
        "status_description": status.title(),
        "settlement_utr": f"UTR{cf_settlement_id}",
        "settlement_initiated_on": "2026-09-02T10:00:00Z",
        "settlement_processed_on": "2026-09-03T10:00:00Z" if status == "SUCCESS" else None,
        "payment_amount": "10000.00",
        "pg_service_charge": "400.00",
        "pg_service_tax": "72.00",
        "adjustment": "0.00",
        "settlement_charge": "20.00",
        "settlement_tax": "8.00",
        "amount_settled": amount_settled,
    }


def _settlement_paginated_stub(pages: list[list[dict]]):
    """Same cursor-keyed (not call-count-keyed) design as `_paginated_stub`
    above -- see its docstring."""
    calls: list[dict] = []

    async def _fake(
        self, *, start_date=None, end_date=None, settlement_status=None, cursor=None, limit=1000
    ):  # noqa: ANN001
        calls.append({"cursor": cursor})
        index = int(cursor.split("-")[1]) if cursor else 0
        rows = pages[index]
        next_cursor = f"cursor-{index + 1}" if index + 1 < len(pages) else None
        return {"data": rows, "cursor": next_cursor}

    return _fake, calls


async def test_sync_settlements_creates_and_is_idempotent(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, _ = _settlement_paginated_stub([[_settlement_row(cf_settlement_id="stl_1")]])
    monkeypatch.setattr(CashfreeClient, "get_settlements", fake)

    service = CashfreeSyncService(db_session)
    first = await service.sync_settlements(_FROM, _TO)
    assert first.applied == 1
    assert first.duplicates == 0

    second = await service.sync_settlements(_FROM, _TO)
    assert second.applied == 0
    assert second.duplicates == 1

    from app.models.cashfree_settlement import CashfreeSettlement
    from sqlalchemy import func, select

    count = await db_session.scalar(select(func.count()).select_from(CashfreeSettlement))
    assert count == 1


async def test_sync_settlements_updates_status_in_place_not_as_duplicate(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A settlement's own status genuinely progresses over time
    (PENDING -> SUCCESS) -- re-syncing must update the existing row, not
    create a second one, and not treat the update as a 'duplicate'.
    """
    fake_pending, _ = _settlement_paginated_stub(
        [[_settlement_row(cf_settlement_id="stl_2", status="PENDING", amount_settled="0.00")]]
    )
    monkeypatch.setattr(CashfreeClient, "get_settlements", fake_pending)
    service = CashfreeSyncService(db_session)
    await service.sync_settlements(_FROM, _TO)

    fake_success, _ = _settlement_paginated_stub(
        [[_settlement_row(cf_settlement_id="stl_2", status="SUCCESS", amount_settled="9500.00")]]
    )
    monkeypatch.setattr(CashfreeClient, "get_settlements", fake_success)
    await service.sync_settlements(_FROM, _TO)

    settlement = await CashfreeSettlementRepository(db_session).get_by_cf_settlement_id("stl_2")
    assert settlement is not None
    assert settlement.status == "SUCCESS"
    assert settlement.amount_settled == Decimal("9500.00")

    from app.models.cashfree_settlement import CashfreeSettlement
    from sqlalchemy import func, select

    count = await db_session.scalar(
        select(func.count())
        .select_from(CashfreeSettlement)
        .where(CashfreeSettlement.cf_settlement_id == "stl_2")
    )
    assert count == 1


# --- Settlement analytics (derived summary) ------------------------------


async def test_settlement_summary_derives_unsettled_and_last_settled(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, _ = _settlement_paginated_stub(
        [
            [
                _settlement_row(
                    cf_settlement_id="stl_done", status="SUCCESS", amount_settled="9500.00"
                ),
                _settlement_row(
                    cf_settlement_id="stl_pending", status="PENDING", amount_settled="0.00"
                ),
            ]
        ]
    )
    monkeypatch.setattr(CashfreeClient, "get_settlements", fake)
    service = CashfreeSyncService(db_session)
    await service.sync_settlements(_FROM, _TO)

    summary = await service.get_settlement_summary()

    assert summary.last_settled_amount == Decimal("9500.00")
    assert summary.last_settlement_utr == "UTRstl_done"
    # The pending settlement's own gross `payment_amount` (10000.00) --
    # `amount_settled` is 0.00 until Cashfree actually finalizes it.
    assert summary.unsettled_amount == Decimal("10000.00")
    assert len(summary.history) == 2


async def test_settlement_summary_with_no_data_returns_honest_empty_values(
    db_session: AsyncSession,
) -> None:
    summary = await CashfreeSyncService(db_session).get_settlement_summary()

    assert summary.unsettled_amount == Decimal("0")
    assert summary.last_settled_amount is None
    assert summary.last_settled_date is None
    assert summary.history == []
