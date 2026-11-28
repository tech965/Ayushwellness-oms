"""COD/Prepaid revenue split + Shiprocket COD remittance reconciliation."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.integrations.shiprocket.remittance import normalize_cod_remittance
from app.models.cod_remittance import CodRemittance
from app.models.enums import FulfillmentStatus, PaymentType, ShipmentStatus
from app.models.shipment import Shipment
from app.repositories.order import OrderRepository
from app.schemas.order import OrderItemCreateRequest
from app.services.cod_reconciliation_service import CodReconciliationService
from app.services.order_service import OrderService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _order(
    session: AsyncSession,
    number: str,
    ptype: PaymentType,
    amount: str,
    *,
    fulfillment: FulfillmentStatus = FulfillmentStatus.UNFULFILLED,
    awb: str | None = None,
):
    order = await OrderService(session).create_order(
        actor=None,
        order_number=number,
        customer_id=None,
        order_datetime=datetime.now(UTC),
        currency="INR",
        payment_type=ptype,
        shipping_charge=0,
        notes=None,
        items=[
            OrderItemCreateRequest(sku="S", product_name="Product", quantity=1, unit_price=amount)
        ],
    )
    await OrderRepository(session).update(order, fulfillment_status=fulfillment)
    if awb:
        session.add(Shipment(order_id=order.id, awb=awb, current_status=ShipmentStatus.DELIVERED))
    await session.commit()
    return order


class _FakeAdapter:
    """Stands in for ShiprocketAdapter.fetch_cod_remittances."""

    def __init__(self, pages: list[list[dict]]) -> None:
        self.pages = pages

    async def fetch_cod_remittances(self, *, page: int = 1, limit: int = 100):
        records = self.pages[page - 1]
        return records, page < len(self.pages)


def test_normalizer_reads_known_keys_and_never_invents_values() -> None:
    row = normalize_cod_remittance(
        {
            "awb_code": "AWB1",
            "cod_amount": "1,200.50",
            "remittance_amount": 1000,
            "utr": "UTR9",
            "remittance_date": "2026-11-10",
            "status": "Processed",
        }
    )
    assert row["awb"] == "AWB1"
    assert row["cod_amount"] == Decimal("1200.50")
    assert row["remitted_amount"] == Decimal("1000")
    assert row["collected_amount"] is None  # not reported -> stays None
    assert row["remittance_reference"] == "UTR9"
    assert row["settlement_date"].year == 2026
    assert normalize_cod_remittance({"cod_amount": 5}) is None  # no AWB


def test_normalizer_never_invents_fields_the_confirmed_endpoint_does_not_return() -> None:
    """The CONFIRMED real `/cod/remittance` response carries exactly
    `awb`/`amount`/`status` -- `crf_id`, `utr` (reference), transaction
    date, and order id must all come back `None`, never guessed or
    derived from the fields that ARE present."""
    row = normalize_cod_remittance({"awb": "AWB-REAL", "amount": "750.00", "status": "processed"})
    assert row["awb"] == "AWB-REAL"
    assert row["remitted_amount"] == Decimal("750.00")
    assert row["remittance_status"] == "processed"
    assert row["crf_id"] is None
    assert row["remittance_reference"] is None
    assert row["settlement_date"] is None
    assert row["shiprocket_order_id"] is None
    assert row["channel_order_id"] is None
    assert row["cod_amount"] is None
    assert row["collected_amount"] is None
    assert row["collected_at"] is None


async def test_scheduled_task_syncs_without_legacy_remittance_path(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ROOT CAUSE REGRESSION: the Celery task used to return early when the
    legacy `SHIPROCKET_REMITTANCE_PATH` was empty (its default, unset in
    render.yaml), so no remittance was ever fetched or written. It must now
    reach the adapter with the setting empty."""
    from app.core.config import settings
    from app.tasks import cod_remittance_sync

    monkeypatch.setattr(settings, "SHIPROCKET_REMITTANCE_PATH", "")
    calls: list[int] = []

    class _Adapter(ShiprocketAdapter):
        def __init__(self) -> None:  # no client/credentials needed
            pass

        async def fetch_cod_remittances(self, *, page: int = 1, limit: int = 100):
            calls.append(page)
            return [{"awb": "AWB-T", "amount": 10, "status": "pending"}], False

    monkeypatch.setattr(cod_remittance_sync, "get_adapter", lambda code: _Adapter())

    class _Ctx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(cod_remittance_sync, "AsyncSessionLocal", lambda: _Ctx())
    await cod_remittance_sync._run()

    assert calls == [1]
    rows = (await db_session.execute(select(CodRemittance))).scalars().all()
    assert [r.awb for r in rows] == ["AWB-T"]


async def test_end_to_end_sync_paginates_and_keeps_multiple_entries_per_awb(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real adapter (stub HTTP client) -> service -> DB with the legacy flag
    empty: two full 100-record pages then a short page are walked via
    POST {"page", "limit": 100}; one AWB with two distinct `processed`
    entries yields two lines; pending/failed land on the summary row."""
    from app.core.config import settings
    from app.models.remittance_reconciliation import CodRemittanceLine

    monkeypatch.setattr(settings, "SHIPROCKET_REMITTANCE_PATH", "")
    page1 = [{"awb": f"A{i}", "amount": 1, "status": "pending"} for i in range(99)]
    page1.append({"awb": "MULTI", "amount": 100, "status": "processed"})
    page2 = [{"awb": f"B{i}", "amount": 1, "status": "pending"} for i in range(99)]
    page2.append({"awb": "MULTI", "amount": 250, "status": "processed"})
    page3 = [{"awb": "FAIL1", "amount": 75, "status": "failed"}]

    class _Client:
        def __init__(self) -> None:
            self.calls: list[tuple] = []
            self._pages = [{"data": page1}, {"data": page2}, {"data": page3}]

        async def request(self, method, path, *, json=None, params=None):
            self.calls.append((method, path, json))
            return self._pages[json["page"] - 1]

    client = _Client()
    result = await CodReconciliationService(db_session).sync_remittances(
        ShiprocketAdapter(client=client)
    )

    assert client.calls == [
        ("POST", "https://api.shiprocket.in/v1/cod/remittance", {"page": n, "limit": 100})
        for n in (1, 2, 3)
    ]
    assert result["lines_created"] == 2
    stmt = select(CodRemittanceLine).where(CodRemittanceLine.awb == "MULTI")
    lines = (await db_session.execute(stmt)).scalars().all()
    assert sorted(line.remittance_amount for line in lines) == [Decimal("100"), Decimal("250")]
    failed = (
        await db_session.execute(select(CodRemittance).where(CodRemittance.awb == "FAIL1"))
    ).scalar_one()
    assert failed.remittance_status == "failed" and failed.remitted_amount is None
    assert (await db_session.execute(select(CodRemittance))).scalars().all().__len__() == 199


async def test_summary_splits_cod_prepaid_and_fulfilment(db_session: AsyncSession) -> None:
    await _order(db_session, "C1", PaymentType.COD, "500", fulfillment=FulfillmentStatus.FULFILLED)
    await _order(db_session, "C2", PaymentType.COD, "300")
    await _order(
        db_session, "P1", PaymentType.PREPAID, "200", fulfillment=FulfillmentStatus.FULFILLED
    )

    s = await CodReconciliationService(db_session).summary(date_from=None, date_to=None)

    assert s.total_revenue == Decimal("1000")
    assert (s.cod.total.orders, s.cod.total.amount) == (2, Decimal("800"))
    assert s.cod.fulfilled.amount == Decimal("500")
    assert s.cod.unfulfilled.amount == Decimal("300")
    assert s.prepaid.fulfilled.orders == 1
    assert s.prepaid.unfulfilled.orders == 0


async def test_delivered_cod_is_not_assumed_collected_or_remitted(
    db_session: AsyncSession,
) -> None:
    await _order(
        db_session, "C1", PaymentType.COD, "500", fulfillment=FulfillmentStatus.FULFILLED, awb="A1"
    )
    svc = CodReconciliationService(db_session)

    s = await svc.summary(date_from=None, date_to=None)
    assert s.cod_financials.expected_amount == Decimal("500")
    assert s.cod_financials.collected_amount == 0
    assert s.cod_financials.remitted_amount == 0
    assert s.cod_financials.orders_without_shiprocket_data == 1

    rows, _ = await svc.list_orders(page=1, page_size=10, payment_type="cod")
    assert rows[0].financial_data == "no_data"
    assert rows[0].collected_amount is None and rows[0].remitted_amount is None
    assert rows[0].bank_verified is False


async def test_sync_is_idempotent_and_keeps_bank_verification(db_session: AsyncSession) -> None:
    await _order(
        db_session, "C1", PaymentType.COD, "500", fulfillment=FulfillmentStatus.FULFILLED, awb="A1"
    )
    svc = CodReconciliationService(db_session)
    record = {
        "awb_code": "A1",
        "cod_amount": 500,
        "collected_amount": 500,
        "remitted_amount": 300,
        "utr": "UTR1",
        "remittance_date": "2026-11-10",
    }

    first = await svc.sync_remittances(_FakeAdapter([[record]]))
    assert first == {
        "created": 1,
        "updated": 0,
        "skipped": 0,
        "lines_created": 0,
        "lines_updated": 0,
        "lines_matched": 0,
        "lines_unmatched": 0,
    }

    await svc.verify_bank_credit(
        "A1",
        amount=Decimal("300"),
        reference="BANK-77",
        credited_on=datetime(2026, 11, 12, tzinfo=UTC),
        reason="Checked against the bank statement",
        user=None,
    )
    second = await svc.sync_remittances(_FakeAdapter([[record], [{"no_awb": 1}]]))
    assert second["created"] == 0 and second["updated"] == 1 and second["skipped"] == 1

    rems = (await db_session.execute(select(CodRemittance))).scalars().all()
    assert len(rems) == 1  # no duplicates
    assert rems[0].bank_credit_reference == "BANK-77"  # sync never wipes verification

    s = await svc.summary(date_from=None, date_to=None)
    f = s.cod_financials
    assert (f.collected_amount, f.remitted_amount) == (Decimal("500"), Decimal("300"))
    assert f.pending_remittance == Decimal("200")
    assert f.bank_verified_amount == Decimal("300")

    rows, total = await svc.list_orders(page=1, page_size=10, payment_type="cod", q="A1")
    assert total == 1
    r = rows[0]
    assert r.financial_data == "reported"
    assert r.settlement_reference == "UTR1"
    assert r.pending_remittance == Decimal("200")
    assert r.bank_verified is True
    assert r.tracking_url.endswith("/A1")


async def test_reported_remittance_alone_is_not_bank_verified(db_session: AsyncSession) -> None:
    await _order(db_session, "C1", PaymentType.COD, "500", awb="A1")
    svc = CodReconciliationService(db_session)
    await svc.sync_remittances(
        _FakeAdapter([[{"awb_code": "A1", "collected_amount": 500, "remitted_amount": 500}]])
    )
    s = await svc.summary(date_from=None, date_to=None)
    assert s.cod_financials.remitted_amount == Decimal("500")
    assert s.cod_financials.bank_verified_amount == 0


async def test_crf_success_record_creates_a_summed_remittance_line(
    db_session: AsyncSession,
) -> None:
    """A CRF record (from the `POST /cod/remittance` feed, CONFIRMED
    DIRECTLY BY SHIPROCKET SUPPORT) with `status: "processed"` must be
    stored as a `CodRemittanceLine`, not overwrite the single-row
    `CodRemittance` summary -- that is what lets two distinct, processed
    entries for the same AWB (a partial payout then the final one) both
    count, instead of the second silently replacing the first. (This
    fixture includes a `crf_id` to exercise the future-proofing path --
    see `test_processed_entries_without_crf_id_use_fingerprint_dedupe`
    for the actual confirmed response shape, which never includes one.)
    """
    from app.models.remittance_reconciliation import CodRemittanceLine

    await _order(
        db_session, "C1", PaymentType.COD, "1500", fulfillment=FulfillmentStatus.FULFILLED, awb="A1"
    )
    svc = CodReconciliationService(db_session)

    partial = {
        "crf_id": "CRF-1",
        "awb": "A1",
        "order_id": "SR-ORDER-1",
        "amount": 500,
        "date": "2026-11-10",
        "status": "processed",
        "utr": "UTR-PARTIAL",
    }
    final = {
        "crf_id": "CRF-2",
        "awb": "A1",
        "order_id": "SR-ORDER-1",
        "amount": 1000,
        "date": "2026-11-15",
        "status": "processed",
        "utr": "UTR-FINAL",
    }
    result = await svc.sync_remittances(_FakeAdapter([[partial, final]]))
    assert result["lines_created"] == 2
    assert result["lines_matched"] == 2
    assert result["created"] == 0  # nothing went onto the CodRemittance summary row

    lines = (
        (await db_session.execute(select(CodRemittanceLine).where(CodRemittanceLine.awb == "A1")))
        .scalars()
        .all()
    )
    assert {ln.dedupe_key for ln in lines} == {"shiprocket_crf:CRF-1", "shiprocket_crf:CRF-2"}
    assert sum(ln.remittance_amount for ln in lines) == Decimal("1500")
    assert all(ln.matched_order_id is not None for ln in lines)

    # Re-syncing the exact same two CRFs must update the same two rows in
    # place, never insert duplicates or add their amounts again.
    again = await svc.sync_remittances(_FakeAdapter([[partial, final]]))
    assert again == {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "lines_created": 0,
        "lines_updated": 2,
        "lines_matched": 0,
        "lines_unmatched": 0,
    }
    lines_after = (
        (await db_session.execute(select(CodRemittanceLine).where(CodRemittanceLine.awb == "A1")))
        .scalars()
        .all()
    )
    assert len(lines_after) == 2
    assert sum(ln.remittance_amount for ln in lines_after) == Decimal("1500")


async def test_processed_fully_remitted_does_not_count_as_bank_confirmed_success(
    db_session: AsyncSession,
) -> None:
    """CRITICAL distinction: Shiprocket `"processed"` means the COURIER
    remitted the money to the business -- it is NOT the same as a human
    verifying that money actually landed in the bank (`BankCredit`).
    Even a `"processed"` CRF amount that exactly equals the order's full
    expected COD value must NOT by itself increase `payment_success` --
    it lands on `payment_initiated` (reported, not yet bank-confirmed)
    until a real `BankCredit` is matched. See `remittance_common.
    cod_status()` (`app/services/remittance_common.py:357-381`): `matched`
    requires `bank_confirmed > 0`, a value that only ever comes from
    `line_bank` (`CodRemittanceReconciliationService._records()`,
    `app/services/cod_remittance_recon_service.py:743,747`) -- never from
    the CRF-reported `remitted` amount itself.
    """
    from app.services.cod_remittance_recon_service import (
        CodRemittanceReconciliationService,
        Filters,
    )

    await _order(
        db_session, "C9", PaymentType.COD, "500", fulfillment=FulfillmentStatus.FULFILLED, awb="A9"
    )
    svc = CodReconciliationService(db_session)
    await svc.sync_remittances(
        _FakeAdapter(
            [[{"awb": "A9", "amount": 500, "status": "processed"}]]  # exactly the full amount
        )
    )

    dash = CodRemittanceReconciliationService(db_session)
    s = await dash.summary(Filters())
    assert s["kpis"]["payment_success"]["count"] == 0
    assert s["kpis"]["payment_initiated"]["count"] == 1
    assert s["kpis"]["payment_failed"]["count"] == 0
    assert s["kpis"]["payment_pending"]["count"] == 0


async def test_crf_partial_payouts_aggregate_across_separate_pages(
    db_session: AsyncSession,
) -> None:
    """Pinned regression for the per_page=100 -> per_page=1 fix
    (`ShiprocketAdapter.fetch_cod_remittances`): production's real
    Shiprocket calls now return at most one CRF record per page. Partial
    + final payouts for the same AWB arriving on SEPARATE pages must
    still sum to the full remitted amount, exactly as they already do
    when returned together on one page.
    """
    from app.models.remittance_reconciliation import CodRemittanceLine

    await _order(
        db_session, "C2", PaymentType.COD, "1500", fulfillment=FulfillmentStatus.FULFILLED, awb="A2"
    )
    svc = CodReconciliationService(db_session)
    partial = {
        "crf_id": "CRF-P1",
        "awb": "A2",
        "order_id": "SR-ORDER-2",
        "amount": 600,
        "date": "2026-11-10",
        "status": "processed",
        "utr": "UTR-P1",
    }
    final = {
        "crf_id": "CRF-P2",
        "awb": "A2",
        "order_id": "SR-ORDER-2",
        "amount": 900,
        "date": "2026-11-16",
        "status": "processed",
        "utr": "UTR-P2",
    }
    # One record per page -- exactly what per_page=1 pagination produces.
    result = await svc.sync_remittances(_FakeAdapter([[partial], [final]]))
    assert result["lines_created"] == 2
    assert result["lines_matched"] == 2

    lines = (
        (await db_session.execute(select(CodRemittanceLine).where(CodRemittanceLine.awb == "A2")))
        .scalars()
        .all()
    )
    assert {ln.dedupe_key for ln in lines} == {"shiprocket_crf:CRF-P1", "shiprocket_crf:CRF-P2"}
    assert sum(ln.remittance_amount for ln in lines) == Decimal("1500")


async def test_processed_entries_without_crf_id_use_fingerprint_dedupe(
    db_session: AsyncSession,
) -> None:
    """The CONFIRMED real `/cod/remittance` response never includes a
    `crf_id` -- only `awb`/`amount`/`status`. Two genuinely distinct
    `"processed"` entries for the same AWB with DIFFERENT amounts (a
    partial payout then the final one) must still both be stored and
    summed, deduped by the (awb, amount, status) fingerprint instead of
    an invented identifier -- never overwriting the earlier payout.
    """
    from app.models.remittance_reconciliation import CodRemittanceLine

    await _order(
        db_session, "C3", PaymentType.COD, "1500", fulfillment=FulfillmentStatus.FULFILLED, awb="A3"
    )
    svc = CodReconciliationService(db_session)
    partial = {"awb": "A3", "amount": 600, "status": "processed"}
    final = {"awb": "A3", "amount": 900, "status": "processed"}

    result = await svc.sync_remittances(_FakeAdapter([[partial, final]]))
    assert result["lines_created"] == 2
    assert result["created"] == 0  # nothing went onto the CodRemittance summary row

    lines = (
        (await db_session.execute(select(CodRemittanceLine).where(CodRemittanceLine.awb == "A3")))
        .scalars()
        .all()
    )
    assert len(lines) == 2
    assert all(ln.dedupe_key.startswith("shiprocket_cod:") for ln in lines)
    assert sum(ln.remittance_amount for ln in lines) == Decimal("1500")

    # Re-syncing the identical two entries must update the same two rows
    # in place (idempotent), never insert duplicates or double the total.
    again = await svc.sync_remittances(_FakeAdapter([[partial, final]]))
    assert again["lines_created"] == 0
    assert again["lines_updated"] == 2
    lines_after = (
        (await db_session.execute(select(CodRemittanceLine).where(CodRemittanceLine.awb == "A3")))
        .scalars()
        .all()
    )
    assert len(lines_after) == 2
    assert sum(ln.remittance_amount for ln in lines_after) == Decimal("1500")


async def test_crf_initiated_counts_as_payment_initiated_not_success(
    db_session: AsyncSession,
) -> None:
    """Backward-compatibility regression: `"initiated"` is a status from
    the EARLIER, now-superseded CRF vocabulary (`pending`/`initiated`/
    `success`) -- no current sync ever writes it again (the CONFIRMED
    real vocabulary is `pending`/`processed`/`failed`), but a row already
    synced under the old vocabulary must keep classifying the same way:
    it has no settled amount, so it must drive Payment Initiated through
    the existing `payout_reported` signal, and must never be read as a
    `remitted_amount` (Shiprocket's own word that a transfer is in
    progress is not proof it completed)."""
    from app.services.cod_remittance_recon_service import (
        CodRemittanceReconciliationService,
        Filters,
    )

    await _order(
        db_session, "C1", PaymentType.COD, "500", fulfillment=FulfillmentStatus.FULFILLED, awb="A1"
    )
    sync = CodReconciliationService(db_session)
    result = await sync.sync_remittances(
        _FakeAdapter(
            [
                [
                    {
                        "crf_id": "CRF-3",
                        "awb": "A1",
                        "status": "initiated",
                        "utr": "UTR-INPROGRESS",
                        "date": "2026-11-10",
                    }
                ]
            ]
        )
    )
    assert result["created"] == 1
    assert result["lines_created"] == 0  # no settled line -- nothing to sum yet

    existing = (
        await db_session.execute(select(CodRemittance).where(CodRemittance.awb == "A1"))
    ).scalar_one()
    assert existing.remitted_amount is None  # never stored as settled money

    dash = CodRemittanceReconciliationService(db_session)
    s = await dash.summary(Filters())
    assert s["kpis"]["payment_initiated"]["count"] == 1
    assert s["kpis"]["payment_success"]["count"] == 0
    assert s["kpis"]["payment_pending"]["count"] == 0


async def test_crf_pending_is_real_data_not_no_data(db_session: AsyncSession) -> None:
    """A CRF `status: "pending"` must still count as Payment Pending (no
    payout evidence yet), but is a *known* pending -- `data_coverage` must
    tell it apart from having no remittance data loaded at all."""
    from app.services.cod_remittance_recon_service import (
        CodRemittanceReconciliationService,
        Filters,
    )

    await _order(
        db_session, "C1", PaymentType.COD, "500", fulfillment=FulfillmentStatus.FULFILLED, awb="A1"
    )
    sync = CodReconciliationService(db_session)
    await sync.sync_remittances(
        _FakeAdapter([[{"crf_id": "CRF-4", "awb": "A1", "status": "pending"}]])
    )

    dash = CodRemittanceReconciliationService(db_session)
    s = await dash.summary(Filters())
    assert s["kpis"]["payment_pending"]["count"] == 1
    assert s["kpis"]["payment_initiated"]["count"] == 0
    assert s["data_coverage"]["pending_is_data_limited"] is False


async def test_crf_failed_counts_as_payment_failed_never_pending_or_success(
    db_session: AsyncSession,
) -> None:
    """CONFIRMED DIRECTLY BY SHIPROCKET SUPPORT: a `status: "failed"` CRF
    entry means the payout attempt FAILED -- it must drive a dedicated
    `payment_failed` KPI, and must NEVER be counted as Payment Pending
    ("no data yet" is not the same as "we tried and it failed") or
    Payment Success. The confirmed real response has no `crf_id` at all
    (only `awb`/`amount`/`status`), so this fixture deliberately omits
    one -- the sync must still work via the (awb, amount, status)
    fallback dedupe key.
    """
    from app.services.cod_remittance_recon_service import (
        CodRemittanceReconciliationService,
        Filters,
    )

    await _order(
        db_session, "C1", PaymentType.COD, "500", fulfillment=FulfillmentStatus.FULFILLED, awb="A1"
    )
    sync = CodReconciliationService(db_session)
    result = await sync.sync_remittances(
        _FakeAdapter([[{"awb": "A1", "amount": 500, "status": "failed"}]])
    )
    assert result["created"] == 1
    assert result["lines_created"] == 0  # a failed attempt is never a settled line

    existing = (
        await db_session.execute(select(CodRemittance).where(CodRemittance.awb == "A1"))
    ).scalar_one()
    assert existing.remitted_amount is None  # never stored as settled money
    assert existing.remittance_status == "failed"

    dash = CodRemittanceReconciliationService(db_session)
    s = await dash.summary(Filters())
    assert s["kpis"]["payment_failed"]["count"] == 1
    assert s["kpis"]["payment_pending"]["count"] == 0
    assert s["kpis"]["payment_success"]["count"] == 0
    assert s["kpis"]["payment_initiated"]["count"] == 0


async def test_crf_failed_is_never_downgraded_back_to_pending(db_session: AsyncSession) -> None:
    """A later sync page reporting the SAME AWB as `"pending"` (e.g. a
    stale/duplicate page, or Shiprocket's own eventual-consistency
    ordering) must never overwrite an already-recorded `"failed"` status
    -- `failed` is the more protective, harder-to-miss state."""
    sync = CodReconciliationService(db_session)
    await _order(
        db_session, "C1", PaymentType.COD, "500", fulfillment=FulfillmentStatus.FULFILLED, awb="A1"
    )
    await sync.sync_remittances(
        _FakeAdapter([[{"awb": "A1", "amount": 500, "status": "failed"}]])
    )
    await sync.sync_remittances(
        _FakeAdapter([[{"awb": "A1", "amount": 500, "status": "pending"}]])
    )

    existing = (
        await db_session.execute(select(CodRemittance).where(CodRemittance.awb == "A1"))
    ).scalar_one()
    assert existing.remittance_status == "failed"


async def test_mixed_pending_and_failed_entries_same_awb_different_amounts(
    db_session: AsyncSession,
) -> None:
    """Multiple non-`processed` entries for the SAME AWB with DIFFERENT
    amounts, across several pages: `CodRemittance` is one row per AWB,
    so only the latest raw payload's `amount`/status combination is kept
    on that row -- this is a real, documented property (not a bug): no
    money is ever "lost" by it, because `remitted_amount` is deliberately
    NEVER set for `pending`/`failed` statuses in the first place (see
    `sync_remittances`'s `if status in ("pending", "failed"):` guard) --
    there is no settled amount to lose. What IS preserved precisely:
    (1) a `"failed"` status, once recorded, always wins over a later
    `"pending"` for the same AWB (tested separately above), and (2) the
    raw JSON of the most recent entry is retained verbatim in
    `raw_external_payload` for audit/transparency, not silently dropped.
    """
    sync = CodReconciliationService(db_session)
    await _order(
        db_session, "C1", PaymentType.COD, "500", fulfillment=FulfillmentStatus.FULFILLED, awb="A1"
    )
    r1 = await sync.sync_remittances(
        _FakeAdapter([[{"awb": "A1", "amount": 300, "status": "pending"}]])
    )
    r2 = await sync.sync_remittances(
        _FakeAdapter([[{"awb": "A1", "amount": 500, "status": "failed"}]])
    )
    r3 = await sync.sync_remittances(
        _FakeAdapter([[{"awb": "A1", "amount": 700, "status": "pending"}]])
    )
    assert r1["created"] == 1
    assert r2["updated"] == 1
    assert r3["updated"] == 1

    existing = (
        await db_session.execute(select(CodRemittance).where(CodRemittance.awb == "A1"))
    ).scalar_one()
    # Exactly one row ever exists for this AWB -- never duplicated.
    assert existing.remittance_status == "failed"  # still wins over the later "pending"
    assert existing.remitted_amount is None  # no amount was ever counted as settled
    assert existing.raw_external_payload["amount"] == 500  # the failed entry's own payload kept
    all_rows = (
        (await db_session.execute(select(CodRemittance).where(CodRemittance.awb == "A1")))
        .scalars()
        .all()
    )
    assert len(all_rows) == 1


async def test_duplicate_failed_entries_do_not_double_count(db_session: AsyncSession) -> None:
    """Re-syncing the exact same `"failed"` entry (same awb/amount/status,
    e.g. a repeated page) must remain idempotent -- it is still a single
    `CodRemittance` row, never duplicated."""
    sync = CodReconciliationService(db_session)
    await _order(
        db_session, "C1", PaymentType.COD, "500", fulfillment=FulfillmentStatus.FULFILLED, awb="A1"
    )
    entry = {"awb": "A1", "amount": 500, "status": "failed"}
    first = await sync.sync_remittances(_FakeAdapter([[entry]]))
    second = await sync.sync_remittances(_FakeAdapter([[entry]]))

    assert first["created"] == 1
    assert second["created"] == 0
    assert second["updated"] == 1

    rows = (
        (await db_session.execute(select(CodRemittance).where(CodRemittance.awb == "A1")))
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_crf_record_with_no_matching_order_is_reported_unmatched(
    db_session: AsyncSession,
) -> None:
    """A CRF for an AWB/order this OMS has no record of must be stored and
    counted as unmatched, never dropped and never crash the sync."""
    svc = CodReconciliationService(db_session)
    result = await svc.sync_remittances(
        _FakeAdapter(
            [[{"crf_id": "CRF-5", "awb": "NO-SUCH-AWB", "amount": 250, "status": "processed"}]]
        )
    )
    assert result["lines_created"] == 1
    assert result["lines_unmatched"] == 1
    assert result["lines_matched"] == 0


async def test_sync_handles_an_empty_response_safely(db_session: AsyncSession) -> None:
    """A single empty page (no records at all, e.g. nothing to sync yet)
    must complete cleanly with all-zero counters, never raise."""
    svc = CodReconciliationService(db_session)
    result = await svc.sync_remittances(_FakeAdapter([[]]))
    assert result == {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "lines_created": 0,
        "lines_updated": 0,
        "lines_matched": 0,
        "lines_unmatched": 0,
    }


async def test_sync_skips_a_malformed_record_with_no_awb(db_session: AsyncSession) -> None:
    """A record missing `awb` entirely (malformed/unexpected shape) has
    nothing to attach it to -- it must be counted as `skipped`, never
    crash the rest of the page or be silently dropped without a trace."""
    svc = CodReconciliationService(db_session)
    result = await svc.sync_remittances(
        _FakeAdapter(
            [
                [
                    {"amount": 500, "status": "processed"},  # no awb
                    {"awb": "A9", "amount": 100, "status": "pending"},
                ]
            ]
        )
    )
    assert result["skipped"] == 1
    assert result["created"] == 1  # the second, well-formed record still syncs


async def test_sync_propagates_an_integration_error_without_silently_swallowing_it(
    db_session: AsyncSession,
) -> None:
    """An API-level failure from the adapter (e.g. a real Shiprocket
    error) must surface as a real exception, never be swallowed into a
    fake "0 synced" success."""
    from app.core.exceptions import IntegrationError

    class _FailingAdapter:
        async def fetch_cod_remittances(self, *, page: int = 1, limit: int = 100):
            raise IntegrationError(
                "Shiprocket API returned a server error (500).",
                details={"error_type": "http_500", "status_code": 500},
            )

    svc = CodReconciliationService(db_session)
    with pytest.raises(IntegrationError):
        await svc.sync_remittances(_FailingAdapter())


async def test_prepaid_rows_have_no_cod_fields_and_filters_apply(
    db_session: AsyncSession,
) -> None:
    await _order(
        db_session, "P1", PaymentType.PREPAID, "200", fulfillment=FulfillmentStatus.FULFILLED
    )
    await _order(db_session, "P2", PaymentType.PREPAID, "100")
    svc = CodReconciliationService(db_session)

    rows, total = await svc.list_orders(
        page=1, page_size=10, payment_type="prepaid", fulfillment_status="fulfilled"
    )
    assert total == 1 and rows[0].order_number == "P1"
    assert rows[0].expected_cod_amount is None and rows[0].financial_data is None

    rows, total = await svc.list_orders(page=1, page_size=10, payment_type="prepaid", q="P2")
    assert total == 1


async def test_export_and_http_endpoints_are_permission_gated(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    await _order(db_session, "C1", PaymentType.COD, "500", awb="A1")
    async with await make_authenticated_client(
        db_session, permission_codes=["analytics.read"], email="a@example.com"
    ) as c:
        assert (await c.get("/api/v1/revenue-reconciliation/summary")).status_code == 200
        r = await c.get("/api/v1/revenue-reconciliation/orders?payment_type=cod")
        assert r.status_code == 200 and r.json()["meta"]["total_items"] == 1
        x = await c.get("/api/v1/revenue-reconciliation/orders/export?payment_type=cod")
        assert x.status_code == 200 and x.content[:2] == b"PK"  # real xlsx (zip)
        # reading analytics does not allow triggering a sync or verifying money
        assert (await c.post("/api/v1/revenue-reconciliation/sync")).status_code == 403
        v = await c.post(
            "/api/v1/revenue-reconciliation/remittances/A1/verify-bank-credit",
            json={"amount": "1", "reference": "x", "credited_on": "2026-11-01T00:00:00Z"},
        )
        assert v.status_code == 403


async def test_sync_rejects_a_refresh_token_presented_as_bearer(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """Pinned regression: `get_current_user` requires `type == "access"`
    (`app/dependencies/auth.py:39-40`). Presenting the REFRESH token
    (returned alongside the access token by `/auth/login`) as the
    Authorization bearer must be rejected with 401 "Access token
    required.", not treated as a valid credential -- this is the correct,
    intended behaviour, not a bug: it stops a leaked/longer-lived refresh
    token from being used to call authenticated endpoints directly. (The
    type check runs before the token's subject is even looked up, so the
    refresh token below doesn't need to belong to a real user.)
    """
    import uuid

    from app.core.security import create_refresh_token

    async with await make_authenticated_client(
        db_session, is_superuser=True, email="super@example.com"
    ) as c:
        # Sanity-check: the fixture's real access token is accepted.
        ok = await c.post("/api/v1/revenue-reconciliation/sync")
        assert ok.status_code != 401

        refresh_token, _jti = create_refresh_token(subject=str(uuid.uuid4()))
        c.headers["Authorization"] = f"Bearer {refresh_token}"
        resp = await c.post("/api/v1/revenue-reconciliation/sync")
        assert resp.status_code == 401
        assert "Access token required" in resp.json()["error"]["message"]
