"""COD "Payment Success / Initiated / Pending" -- data coverage and the Shiprocket
remittance sources feeding them. Synthetic data only.

Pins four behaviours the dashboard depends on:
* `data_coverage` tells "no remittance reported" apart from "no remittance data
  has been loaded at all" (so a Pending count is never mistaken for confirmed
  non-payment when nothing was ever imported);
* a remittance synced from the Shiprocket API is `initiated`, and only becomes
  `success` after a bank confirmation;
* several shipments for one order never double-count the order or its remittance;
* the sync follows every page and is idempotent.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.db.session import get_db
from app.main import app
from app.models.cod_remittance import CodRemittance
from app.models.enums import ShipmentStatus
from app.models.remittance_reconciliation import (
    BankCredit,
    BankCreditLink,
    CodRemittanceLine,
)
from app.models.shipment import Shipment
from app.services.cod_reconciliation_service import CodReconciliationService
from app.services.cod_remittance_recon_service import (
    CodRemittanceReconciliationService,
    Filters,
)
from sqlalchemy import func, select

from tests.telecalling_test_utils import bearer_client
from tests.test_cod_reconciliation import _FakeAdapter
from tests.test_cod_remittance_reconciliation import (
    NOW,
    _cod_order,
    _records,
    _summary,
    _user,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clear_override():
    yield
    app.dependency_overrides.clear()


def _line(order_id, awb, reference, amount) -> CodRemittanceLine:
    return CodRemittanceLine(
        partner_name="Delhivery",
        awb=awb,
        remittance_reference=reference,
        remittance_amount=Decimal(amount),
        remittance_date=NOW - timedelta(days=3),
        dedupe_key=uuid.uuid4().hex,
        matched_order_id=order_id,
        match_method="awb" if order_id else None,
    )


async def test_crf_amount_kpis_and_transaction_list_are_real_synced_data(db_session):
    """The CRF amount KPIs (success/initiated/failed/pending) and the
    transaction-detail list must come from real, synced `CodRemittanceLine`
    / `CodRemittance` rows -- never a placeholder -- and must be exactly 0 /
    empty when nothing has been synced yet."""
    dash = CodRemittanceReconciliationService(db_session)

    # Nothing synced yet: all are genuinely zero, not a fabricated figure.
    empty = await dash.summary(Filters())
    assert empty["kpis"]["crf_success_amount"] == {"count": 0, "amount": Decimal("0.00")}
    assert empty["kpis"]["crf_initiated_amount"] == {"count": 0, "amount": Decimal("0.00")}
    assert empty["kpis"]["crf_failed_amount"] == {"count": 0, "amount": Decimal("0.00")}
    assert empty["kpis"]["crf_pending_amount"] == {"count": 0, "amount": Decimal("0.00")}
    rows, total = await dash.crf_transactions(status=None, page_params=_page_params())
    assert rows == [] and total == 0

    await _cod_order(db_session, "C1", "1500", awb="A1")
    # "failed" and "pending" are each the CRF feed's ONLY claim about a
    # given AWB at any one time (CodRemittance is one row per AWB), so a
    # realistic fixture puts them on separate orders/AWBs, not the same one.
    await _cod_order(db_session, "C2", "300", awb="A2")
    await _cod_order(db_session, "C3", "1500", awb="A3")
    sync = CodReconciliationService(db_session)
    await sync.sync_remittances(
        _FakeAdapter(
            [
                [
                    {
                        "crf_id": "CRF-S1",
                        "awb": "A1",
                        "amount": 500,
                        "status": "processed",
                        "utr": "UTR-S1",
                        "date": "2026-11-01",
                    },
                    {
                        "crf_id": "CRF-S2",
                        "awb": "A1",
                        "amount": 1000,
                        "status": "processed",
                        "utr": "UTR-S2",
                        "date": "2026-11-05",
                    },
                    # CONFIRMED real response shape: no crf_id at all.
                    {"awb": "A2", "amount": 300, "status": "failed"},
                    {"awb": "A3", "amount": 1500, "status": "pending"},
                ]
            ]
        )
    )

    s = await dash.summary(Filters())
    # Two real processed CRFs for the same AWB (a partial then a final
    # payout) sum to their real total -- not the latest one overwriting
    # the other.
    assert s["kpis"]["crf_success_amount"] == {"count": 2, "amount": Decimal("1500.00")}
    assert s["kpis"]["crf_initiated_amount"] == {"count": 0, "amount": Decimal("0.00")}
    assert s["kpis"]["crf_failed_amount"] == {"count": 1, "amount": Decimal("300.00")}
    assert s["kpis"]["crf_pending_amount"] == {"count": 1, "amount": Decimal("1500.00")}

    rows, total = await dash.crf_transactions(status="processed", page_params=_page_params())
    assert total == 2
    by_crf = {r["crf_id"]: r for r in rows}
    assert by_crf["CRF-S1"]["utr"] == "UTR-S1"
    assert by_crf["CRF-S1"]["amount"] == Decimal("500.00")
    assert by_crf["CRF-S1"]["awb"] == "A1"
    assert all(r["matched_order_id"] is not None for r in rows)

    rows_failed, total_f = await dash.crf_transactions(status="failed", page_params=_page_params())
    assert total_f == 1
    assert rows_failed[0]["awb"] == "A2"
    assert rows_failed[0]["amount"] == Decimal("300.00")
    assert rows_failed[0]["crf_id"] is None  # never invented -- not returned by the real endpoint

    # Status mapping is accurate, never blurred: processed CRFs never leak
    # into Pending/Failed/Initiated amounts, and these amounts never move
    # the order-level Payment Success/Initiated/Failed/Pending KPIs --
    # those still require bank evidence for Success, unchanged by this view.
    assert s["kpis"]["payment_success"]["count"] == 0
    assert s["kpis"]["payment_failed"]["count"] == 1


def _page_params():
    from app.schemas.common import PageParams

    return PageParams(page=1, page_size=50)


async def test_empty_database_reports_that_pending_is_data_limited(db_session):
    user = await _user(db_session, "cov1@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        s = await _summary(client)
    cov = s["data_coverage"]
    assert cov["has_remittance_data"] is False
    assert cov["has_bank_data"] is False
    assert cov["pending_is_data_limited"] is True
    assert cov["remittance_lines"] == 0 and cov["api_reported_remittances"] == 0
    assert cov["api_sync_configured"] is False  # Shiprocket credentials unset in tests
    assert cov["last_upload_at"] is None and cov["last_api_sync_at"] is None


async def test_delivered_orders_with_no_data_loaded_are_all_pending_and_flagged(db_session):
    """The production symptom: delivered COD orders, nothing imported -> 0 success,
    0 initiated, everything pending -- and the response says that is a data limit."""
    user = await _user(db_session, "cov2@example.com")
    for i in range(3):
        await _cod_order(db_session, f"NOD{i}", "1000.00", awb=f"AWB-NOD{i}")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        s = await _summary(client)
    k = s["kpis"]
    assert k["payment_success"]["count"] == 0
    assert k["payment_initiated"]["count"] == 0
    assert k["payment_pending"]["count"] == 3 == k["delivered_orders"]["count"]
    assert s["data_coverage"]["pending_is_data_limited"] is True


async def test_loaded_remittance_without_bank_data_is_initiated_and_coverage_says_so(db_session):
    user = await _user(db_session, "cov3@example.com")
    order = await _cod_order(db_session, "NB1", "1000.00", awb="AWB-NB1")
    db_session.add(_line(order.id, "AWB-NB1", "CRF-1", "1000"))
    await db_session.commit()
    async with bearer_client(app, get_db, db_session, user.id) as client:
        s = await _summary(client)
    k = s["kpis"]
    # Fully remitted by the courier, but no bank statement: never "success".
    assert (k["payment_success"]["count"], k["payment_initiated"]["count"]) == (0, 1)
    assert k["payment_pending"]["count"] == 0
    cov = s["data_coverage"]
    assert cov["has_remittance_data"] is True and cov["pending_is_data_limited"] is False
    assert cov["has_bank_data"] is False and cov["remittance_lines"] == 1


async def test_bank_confirmed_remittance_is_success_and_coverage_sees_the_bank_data(db_session):
    user = await _user(db_session, "cov4@example.com")
    order = await _cod_order(db_session, "BK1", "1000.00", awb="AWB-BK1")
    db_session.add(_line(order.id, "AWB-BK1", "CRF-BK1", "1000"))
    credit = BankCredit(
        amount=Decimal("1000"),
        credit_date=NOW - timedelta(days=1),
        reference="CRF-BK1",
        dedupe_key=uuid.uuid4().hex,
    )
    db_session.add(credit)
    await db_session.flush()
    db_session.add(
        BankCreditLink(
            bank_credit_id=credit.id,
            module="cod",
            reference="CRF-BK1",
            amount=Decimal("1000"),
            method="auto",
        )
    )
    await db_session.commit()
    async with bearer_client(app, get_db, db_session, user.id) as client:
        s = await _summary(client)
    k = s["kpis"]
    assert (k["payment_success"]["count"], k["payment_initiated"]["count"]) == (1, 0)
    assert s["data_coverage"]["has_bank_data"] is True


async def test_api_synced_remittance_is_initiated_until_a_bank_confirmation(db_session):
    user = await _user(db_session, "cov5@example.com")
    await _cod_order(db_session, "API1", "1000.00", awb="AWB-API1")
    record = {
        "awb_code": "AWB-API1",
        "cod_amount": 1000,
        "collected_amount": 1000,
        "remitted_amount": 1000,
        "utr": "UTR-API1",
        "remittance_date": "2026-11-10",
    }
    legacy = CodReconciliationService(db_session)
    assert (await legacy.sync_remittances(_FakeAdapter([[record]])))["created"] == 1

    async with bearer_client(app, get_db, db_session, user.id) as client:
        before = await _summary(client)
        assert before["kpis"]["payment_initiated"]["count"] == 1
        assert before["kpis"]["payment_success"]["count"] == 0
        assert before["data_coverage"]["api_reported_remittances"] == 1
        assert before["data_coverage"]["has_remittance_data"] is True
        assert before["data_coverage"]["last_api_sync_at"] is not None

        # A person matches it to a real bank credit -> now (and only now) success.
        await legacy.verify_bank_credit(
            "AWB-API1",
            amount=Decimal("1000"),
            reference="BANK-API1",
            credited_on=datetime(2026, 11, 12, tzinfo=UTC),
            reason="Checked against the bank statement",
            user=None,
        )
        after = await _summary(client)
    assert after["kpis"]["payment_success"]["count"] == 1
    assert after["kpis"]["payment_initiated"]["count"] == 0
    assert after["data_coverage"]["manual_bank_verifications"] == 1
    assert after["data_coverage"]["has_bank_data"] is True


async def test_multiple_shipments_on_one_order_count_the_order_and_remittance_once(db_session):
    user = await _user(db_session, "cov6@example.com")
    order = await _cod_order(db_session, "MS1", "1000.00", awb="AWB-NEW")
    # An earlier, superseded shipment for the same order.
    db_session.add(
        Shipment(
            order_id=order.id,
            awb="AWB-OLD",
            current_status=ShipmentStatus.RTO_DELIVERED,
            source_system="manual",
            created_at=NOW - timedelta(days=30),
        )
    )
    # The courier remitted against the OLD AWB; it is not yet matched to an order.
    unmatched = _line(None, "AWB-OLD", "CRF-MS1", "1000")
    db_session.add(unmatched)
    await db_session.commit()

    svc = CodRemittanceReconciliationService(db_session)
    result = await svc.rematch(None)
    assert result["matched"] == 1  # matched through ANY of the order's shipments

    async with bearer_client(app, get_db, db_session, user.id) as client:
        s = await _summary(client)
        records, total = await _records(client)
    k = s["kpis"]
    assert k["delivered_orders"]["count"] == total == len(records) == 1  # order counted once
    assert Decimal(records[0]["remittance_amount"]) == Decimal("1000.00")  # remittance once
    assert k["unmatched"]["count"] == 0
    assert k["payment_initiated"]["count"] == 1 and k["payment_pending"]["count"] == 0


async def test_two_crfs_for_one_order_add_up_without_double_counting(db_session):
    user = await _user(db_session, "cov7@example.com")
    order = await _cod_order(db_session, "CRF2", "1000.00", awb="AWB-CRF2")
    db_session.add_all(
        [_line(order.id, "AWB-CRF2", "CRF-A", "600"), _line(order.id, "AWB-CRF2", "CRF-B", "400")]
    )
    await db_session.commit()
    async with bearer_client(app, get_db, db_session, user.id) as client:
        s = await _summary(client)
        records, total = await _records(client)
    assert total == 1 and s["kpis"]["delivered_orders"]["count"] == 1
    assert Decimal(records[0]["remittance_amount"]) == Decimal("1000.00")
    assert records[0]["line_count"] == 2
    assert s["kpis"]["payment_initiated"]["count"] == 1  # fully remitted, no bank yet


async def test_sync_follows_every_page_and_a_rerun_updates_instead_of_duplicating(db_session):
    pages = [
        [{"awb_code": f"AWB-P{p}{i}", "remitted_amount": 100 + i} for i in range(2)]
        for p in range(3)
    ]
    svc = CodReconciliationService(db_session)
    first = await svc.sync_remittances(_FakeAdapter(pages))
    assert first == {
        "created": 6,
        "updated": 0,
        "skipped": 0,
        "lines_created": 0,
        "lines_updated": 0,
        "lines_matched": 0,
        "lines_unmatched": 0,
    }
    again = await svc.sync_remittances(_FakeAdapter(pages))
    assert again == {
        "created": 0,
        "updated": 6,
        "skipped": 0,
        "lines_created": 0,
        "lines_updated": 0,
        "lines_matched": 0,
        "lines_unmatched": 0,
    }
    assert (await db_session.execute(select(func.count(CodRemittance.id)))).scalar_one() == 6


async def test_unmatched_shiprocket_records_stay_visible_and_are_not_dropped(db_session):
    """A synced/uploaded remittance for an AWB OMS does not know is an exception
    to report, not something to discard."""
    user = await _user(db_session, "cov8@example.com")
    db_session.add(_line(None, "AWB-GHOST", "CRF-GHOST", "250"))
    await db_session.commit()
    async with bearer_client(app, get_db, db_session, user.id) as client:
        s = await _summary(client)
    assert s["kpis"]["unmatched"]["count"] == 1
    assert Decimal(s["kpis"]["unmatched"]["amount"]) == Decimal("250.00")
    assert s["data_coverage"]["has_remittance_data"] is True


async def test_summary_service_returns_coverage_for_an_empty_period(db_session):
    """Direct service call with a date range that excludes everything."""
    svc = CodRemittanceReconciliationService(db_session)
    out = await svc.summary(
        Filters(
            date_from=datetime(2030, 1, 1, tzinfo=UTC), date_to=datetime(2030, 1, 2, tzinfo=UTC)
        )
    )
    assert out["kpis"]["delivered_orders"]["count"] == 0
    assert out["data_coverage"]["pending_is_data_limited"] is True


async def test_adjustment_only_settlement_is_never_payment_success(db_session):
    """`matched` also covers an order settled purely by a manual adjustment. With no
    CRF line and no bank credit that is a write-off, not a payment: it must not count
    as Success, and the three buckets must still partition the delivered orders."""
    from app.models.remittance_reconciliation import ReconciliationAdjustment

    user = await _user(db_session, "cov9@example.com")
    adjusted = await _cod_order(db_session, "ADJ1", "1000.00", awb="AWB-ADJ1")
    await _cod_order(db_session, "PLAIN1", "500.00", awb="AWB-PLAIN1")
    db_session.add(
        ReconciliationAdjustment(
            module="cod", order_id=adjusted.id, amount=Decimal("1000"), reason="agreed write-off"
        )
    )
    await db_session.commit()
    async with bearer_client(app, get_db, db_session, user.id) as client:
        s = await _summary(client)
        k = s["kpis"]
        assert k["reconciled"]["count"] == 1  # the reconciliation status is unchanged
        assert k["payment_success"]["count"] == 0 and k["payment_success"]["amount"] in (
            "0",
            "0.00",
        )
        assert k["payment_initiated"]["count"] == 0
        assert k["payment_pending"]["count"] == 2 == k["delivered_orders"]["count"]
        assert Decimal(k["payment_pending"]["amount"]) == Decimal("1500.00")
        rows, total = await _records(client, kpi="payment_success")
        assert total == 0 and rows == []
        _rows, pending_total = await _records(client, kpi="payment_pending")
        assert pending_total == 2


async def test_pending_orders_never_read_from_shiprocket_are_counted_as_unchecked(db_session):
    """A partial sync must not make unread Pending orders look like confirmed non-payment."""
    from app.services.shiprocket_finance_service import ShiprocketFinanceService

    from tests.test_shiprocket_finance import FakeShiprocket, _shipped

    user = await _user(db_session, "cov10@example.com")
    reported = await _shipped(db_session, "UC-1", "AWB-UC1", None)
    db_session.add(_line(reported.id, "AWB-UC1", "CRF-UC1", "1000"))  # in a CRF report
    await _shipped(db_session, "UC-2", "AWB-UC2", "9002")  # read from Shiprocket: blank
    await _shipped(db_session, "UC-3", "AWB-UC3", None)  # never read, in no report
    await db_session.commit()
    blank = {"data": {"remittance_utr": "", "remittance_date": "", "remittance_status": ""}}
    await ShiprocketFinanceService(db_session).sync_order_finance(
        FakeShiprocket({"9002": blank}), limit=10, user=None
    )
    async with bearer_client(app, get_db, db_session, user.id) as client:
        s = await _summary(client)
    cov = s["data_coverage"]
    assert s["kpis"]["payment_initiated"]["count"] == 1  # the CRF-reported order
    assert cov["has_remittance_data"] is True  # so the all-or-nothing flag stays off...
    assert cov["pending_is_data_limited"] is False
    assert cov["pending_orders"] == 2  # ...yet one of the two Pending orders is unknown:
    assert cov["pending_unchecked_orders"] == 1


async def test_summary_survives_a_database_without_the_charges_table(db_session):
    """Schema state B: `shiprocket_shipment_charges` (migration f2b8d4a6c0e1) is absent.
    /cod/summary must still answer, every count must be unchanged, and the never-checked
    split must be reported as UNKNOWN (never 0)."""
    from sqlalchemy import text

    user = await _user(db_session, "cov11@example.com")
    await _cod_order(db_session, "NT-1", "1000.00", awb="AWB-NT1")
    await _cod_order(db_session, "NT-2", "500.00", awb="AWB-NT2")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        with_table = await _summary(client)  # state A: table present
        assert with_table["data_coverage"]["pending_check_available"] is True
        assert with_table["data_coverage"]["pending_unchecked_orders"] == 2
        await db_session.execute(text("DROP TABLE shiprocket_shipment_charges"))
        await db_session.commit()
        without_table = await _summary(client)  # state B: table absent
    assert without_table["kpis"] == with_table["kpis"]  # Pending is exactly as accurate
    cov = without_table["data_coverage"]
    assert cov["pending_check_available"] is False
    assert cov["pending_unchecked_orders"] is None  # unknown, not zero
    assert cov["pending_orders"] == 2
