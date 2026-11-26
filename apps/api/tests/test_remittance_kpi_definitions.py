"""Reported settlement (courier / gateway) and bank-confirmed money are two
different figures. These tests pin the definitions for COD and prepaid.
Synthetic data only.
"""

from __future__ import annotations

import io
import uuid
from decimal import Decimal

import pytest
from app.main import app
from app.models.remittance_reconciliation import GatewaySettlementLine
from app.services.prepaid_remittance_recon_service import FAILED_LINE_STATES, prepaid_status
from app.services.remittance_common import DEFINITIONS, BankEvidence, cod_status
from openpyxl import load_workbook

from tests import test_cod_remittance_reconciliation as cod_data
from tests import test_prepaid_remittance_reconciliation as prepaid_data
from tests.test_remittance_drilldowns import BASE, _admin, _client, _walk

pytestmark = pytest.mark.asyncio

D = Decimal


@pytest.fixture(autouse=True)
def _clear_override():
    yield
    app.dependency_overrides.clear()


async def _kpis(client, module: str, params: dict) -> dict:
    res = await client.get(f"{BASE}/{module}/summary", params=params)
    assert res.status_code == 200, res.text
    return res.json()["data"]["kpis"]


# --- never bank-confirmed without a bank credit ----------------------------------


@pytest.mark.parametrize("batch", ["0.01", "0.80", "1.00", "0", "-50", "1000"])
async def test_no_bank_credit_never_covers_any_batch(batch):
    assert BankEvidence({}, {}).covers("U1", D(batch)) is False


async def test_tolerance_is_rounding_only_and_never_creates_a_match():
    assert cod_status(D("1"), D("1"), D("0"), D("0")) == "partially_reconciled"
    assert cod_status(D("1"), D("1"), D("0"), D("1")) == "matched"
    common = {
        "captured": True,
        "failed": False,
        "gross": D("2"),
        "refunds": D("0"),
        "has_lines": True,
        "expected_net": D("0.90"),
        "settled": D("0.90"),
    }
    assert prepaid_status(**common, bank_confirmed=D("0")) == "partially_reconciled"
    assert prepaid_status(**common, bank_confirmed=D("0.90")) == "matched"
    evidence = BankEvidence({"u1": D("999.40")}, {})
    assert evidence.covers("U1", D("1000.00"))  # within rounding
    assert not evidence.covers("U1", D("1001.00"))


async def test_successful_gateway_settlement_without_bank_credit_is_not_confirmed(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        # Settlement report only: no bank statement at all.
        await prepaid_data._seed(db_session)
        up = await prepaid_data._upload(
            client, "prepaid/uploads", "s.csv", prepaid_data.SETTLEMENT, gateway="Cashfree"
        )
        assert up.status_code == 201, up.text
        k = await _kpis(client, "prepaid", prepaid_data._dp())
        assert D(k["reported_settlement"]["amount"]) == D("4529.20")
        assert D(k["bank_settlement"]["amount"]) == 0 and k["bank_settlement"]["count"] == 0
        assert k["reconciled"]["count"] == 0
        # Difference and pending depend on what the gateway reports, not on the bank.
        assert D(k["settlement_difference"]["amount"]) == D("976.40")
        before = (k["pending"], k["settlement_difference"], k["reported_settlement"])
        bank = await prepaid_data._upload(
            client, "bank-statements", "b.csv", prepaid_data.BANK, source_name="HDFC"
        )
        assert bank.status_code == 201, bank.text
        after = await _kpis(client, "prepaid", prepaid_data._dp())
    assert (
        after["pending"],
        after["settlement_difference"],
        after["reported_settlement"],
    ) == before
    assert D(after["bank_settlement"]["amount"]) == D("976.40")  # matched by UTR U1
    assert after["reconciled"]["count"] == 1


async def test_expected_net_minus_received_is_the_settlement_difference(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        await prepaid_data._setup(db_session, client)
        for extra in ({}, {"status": "short"}, {"status": "pending"}, {"gateway": "Cashfree"}):
            params = {**prepaid_data._dp(), **extra}
            k = await _kpis(client, "prepaid", params)
            assert D(k["expected_net"]["amount"]) - D(k["reported_settlement"]["amount"]) == D(
                k["settlement_difference"]["amount"]
            ), extra
            rows, totals, _ = await _walk(client, "prepaid", "difference", params)
            assert sum((D(r["settlement_gap"]) for r in rows), D(0)) == D(totals["amount"])


async def test_unsuccessful_lines_never_count_as_received_or_unmatched_money(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        await prepaid_data._setup(db_session, client)
        params = prepaid_data._dp()
        before = await _kpis(client, "prepaid", params)
        orders, _, _ = await _walk(client, "prepaid", "orders", params, page_size=100)
        p5 = next(r for r in orders if r["order_number"].lstrip("#") == "P5")
        for index, state in enumerate(sorted(FAILED_LINE_STATES)):
            for matched in (uuid.UUID(p5["order_id"]), None):
                db_session.add(
                    GatewaySettlementLine(
                        gateway="Cashfree",
                        net_amount=D("1000.00"),
                        gateway_charges=D("0"),
                        gst_amount=D("0"),
                        line_status=state,
                        matched_order_id=matched,
                        settlement_reference="X",  # a bank credit with this ref exists
                        dedupe_key=f"bad-{index}-{matched is None}",
                    )
                )
        await db_session.commit()
        after = await _kpis(client, "prepaid", params)
        for key in ("reported_settlement", "bank_settlement", "pending", "settlement_difference"):
            assert after[key] == before[key], key
        # Still visible as unmatched transactions, but they add no money.
        extra = len(FAILED_LINE_STATES)
        assert after["unmatched"]["count"] == before["unmatched"]["count"] + extra
        assert after["unmatched"]["amount"] == before["unmatched"]["amount"]
        rows, totals, _ = await _walk(client, "prepaid", "unmatched", params, page_size=100)
        bad = [r for r in rows if "Not a successful settlement" in r["reason"]]
        assert len(bad) == extra and all(D(r["contribution"]) == 0 for r in bad)
        assert D(totals["amount"]) == D(after["unmatched"]["amount"])
        dates = (
            await client.get(f"{BASE}/prepaid/reports/settlement-dates", params=params)
        ).json()["data"]
    assert sum((D(r["net_amount"]) for r in dates), D(0)) == D(
        after["reported_settlement"]["amount"]
    ) + D(after["unmatched"]["amount"])


# --- partial bank credit ---------------------------------------------------------


@pytest.mark.parametrize("module", ["cod", "prepaid"])
async def test_partial_bank_credit_counts_the_actual_amount_everywhere(db_session, module):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        if module == "cod":
            await cod_data._setup_full(db_session, client)
            params = cod_data._date_params()
            confirmed_key = "bank_confirmed"
        else:
            await prepaid_data._setup(db_session, client)
            params = prepaid_data._dp()
            confirmed_key = "bank_settlement"
        before = await _kpis(client, module, params)

        def report_total(rows):
            return sum((D(r["bank_confirmed_amount"]) for r in rows), D(0))

        url = f"{BASE}/{module}/reports/settlement-dates"
        report_before = report_total((await client.get(url, params=params)).json()["data"])
        assert report_before == D(before[confirmed_key]["amount"])
        # Shrink every bank link to Rs 1: each reference now has only Rs 1 credited.
        from app.models.remittance_reconciliation import BankCreditLink
        from sqlalchemy import select, update

        links = len((await db_session.execute(select(BankCreditLink.id))).all())
        await db_session.execute(update(BankCreditLink).values(amount=D("1.00")))
        await db_session.commit()
        after = await _kpis(client, module, params)
        report_after = report_total((await client.get(url, params=params)).json()["data"])
    assert links >= 1
    assert D(after[confirmed_key]["amount"]) == report_after == D(links)
    assert after["reconciled"]["count"] < before["reconciled"]["count"]
    assert after["partially_confirmed"]["count"] >= links
    reported_key = "remittance_received" if module == "cod" else "reported_settlement"
    assert after[reported_key] == before[reported_key]  # reported money is unaffected


# --- Excel exports --------------------------------------------------------------


@pytest.mark.parametrize("module", ["cod", "prepaid"])
async def test_exports_label_reported_and_bank_confirmed_separately(db_session, module):
    user = await _admin(db_session)
    data = cod_data if module == "cod" else prepaid_data
    reported, confirmed = (
        ("COD settled (courier-reported)", "COD bank confirmed")
        if module == "cod"
        else ("Settlement received (gateway-reported)", "Prepaid bank confirmed")
    )
    async with _client(db_session, user) as client:
        await (data._setup_full if module == "cod" else data._setup)(db_session, client)
        params = data._date_params() if module == "cod" else data._dp()
        k = await _kpis(client, module, params)
        full = await client.get(f"{BASE}/{module}/export", params=params)
        drill = await client.get(
            f"{BASE}/{module}/drilldown/export",
            params={**params, "kpi": "expected" if module == "cod" else "orders"},
        )
    reported_key = "remittance_received" if module == "cod" else "reported_settlement"
    confirmed_key = "bank_confirmed" if module == "cod" else "bank_settlement"
    for content, sheet in ((full.content, "Order-wise"), (drill.content, "Records")):
        wb = load_workbook(io.BytesIO(content))
        rows = list(wb[sheet].iter_rows(values_only=True))
        for header, key in ((reported, reported_key), (confirmed, confirmed_key)):
            column = rows[0].index(header)
            assert sum(D(str(r[column])) for r in rows[1:]) == D(k[key]["amount"]), header
        terms = {r[0]: r[1] for r in wb["Definitions"].iter_rows(values_only=True)}
        for term, meaning in DEFINITIONS[module]:
            assert terms[term] == meaning
    wb = load_workbook(io.BytesIO(full.content))
    if module == "prepaid":
        for title in ("Order-wise", "Gateway-wise"):
            headers = next(wb[title].iter_rows(values_only=True))
            assert reported in headers and confirmed in headers, title
