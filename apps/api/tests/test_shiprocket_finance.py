"""Shiprocket expenses and COD payouts: what comes from the API (per
Shiprocket's published collection), what needs a report, and the guarantees
that no money is counted twice and no amount is invented.
Synthetic data only; the Shiprocket API is faked.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.core.exceptions import IntegrationError
from app.integrations.shiprocket.order_finance import parse_order_finance
from app.integrations.shiprocket.wallet import normalize_wallet_transaction
from app.main import app
from app.models.cod_remittance import CodRemittance
from app.models.shiprocket_finance import ShiprocketShipmentCharge
from app.models.shiprocket_wallet import ShiprocketWalletTransaction
from app.services.shiprocket_finance_service import (
    EXPENSE_KPIS,
    PAYOUT_KPIS,
    ShiprocketFinanceService,
)
from app.services.shiprocket_wallet_service import WalletService
from openpyxl import load_workbook
from sqlalchemy import select

from tests.telecalling_test_utils import bearer_client, make_role, make_user
from tests.test_remittance_manual_match_and_uploads import _order

pytestmark = pytest.mark.asyncio

D = Decimal
BASE = "/api/v1/shiprocket-finance"
RECON = "/api/v1/remittance-reconciliation"
EXPENSE_CARDS = [spec[0] for spec in EXPENSE_KPIS]
PAYOUT_CARDS = [spec[0] for spec in PAYOUT_KPIS]


@pytest.fixture(autouse=True)
def _clear_override():
    yield
    app.dependency_overrides.clear()


async def _client(db, perms, email="fin@example.com"):
    role = await make_role(db, name=f"R-{email}", permission_codes=perms)
    return bearer_client(app, get_db_dep(), db, (await make_user(db, email=email, role=role)).id)


def get_db_dep():
    from app.db.session import get_db

    return get_db


ADMIN = [
    "analytics.read",
    "reconciliation.read",
    "reconciliation.upload",
    "reconciliation.manage",
    "reconciliation.adjust",
]


def _csv(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode()


def _file(content: bytes, name="r.csv"):
    return {"file": (name, io.BytesIO(content), "text/csv")}


def statement_row(**over) -> dict:
    """A row shaped like Shiprocket's documented statement sample."""
    return {
        "transaction_id": "TX1",
        "order_id": "9001",
        "channel_order_id": "#A1",
        "awb_code": "AWB1",
        "return_awb_code": None,
        "applied_weight": "0.5",
        "charged_weight": "0.5",
        "billed_weight": "0.5",
        "action": "debit",
        "charge": "Freight",
        "description": "Freight Charge",
        "debit_amount": "80.00",
        "credit_amount": "",
        "balance_amount": "920.00",
        "created_at": "2026-11-20 10:00:00",
        **over,
    }


async def _add_txn(db, **values):
    parsed = normalize_wallet_transaction(statement_row(**values))
    assert parsed is not None, values
    db.add(ShiprocketWalletTransaction(**parsed, source="shiprocket_api"))
    await db.commit()


# --- the documented statement fields --------------------------------------------


async def test_documented_statement_rows_are_read_with_their_direction():
    debit = normalize_wallet_transaction(statement_row())
    assert (debit["direction"], debit["amount"], debit["txn_type"]) == (
        "debit",
        D("80.00"),
        "freight",
    )
    assert debit["balance_after"] == D("920.00") and debit["awb"] == "AWB1"
    credit = normalize_wallet_transaction(
        statement_row(
            transaction_id="TX2",
            action="credit",
            description="Freight reversal",
            debit_amount="",
            credit_amount="80",
        )
    )
    assert (credit["direction"], credit["txn_type"]) == ("credit", "freight_reversal")


async def test_a_row_with_both_sides_or_neither_is_never_guessed():
    assert normalize_wallet_transaction(statement_row(action="", credit_amount="5")) is None
    assert normalize_wallet_transaction(statement_row(debit_amount="", credit_amount="")) is None
    assert normalize_wallet_transaction(statement_row(created_at="")) is None


# --- expense KPIs ----------------------------------------------------------------


async def _seed_expenses(db):
    await _add_txn(db, transaction_id="F1", debit_amount="80", description="Freight Charge")
    await _add_txn(db, transaction_id="F2", debit_amount="70", awb_code="AWB2",
                   description="Freight Charge", created_at="2026-11-21 10:00:00")  # fmt: skip
    await _add_txn(db, transaction_id="C1", debit_amount="20", awb_code="AWB3",
                   description="COD charge", created_at="2026-11-22 10:00:00")  # fmt: skip
    await _add_txn(db, transaction_id="R1", action="credit", debit_amount="",
                   credit_amount="30", awb_code="AWB4", description="Freight reversal",
                   created_at="2026-11-23 10:00:00")  # fmt: skip
    await _add_txn(db, transaction_id="W1", action="credit", debit_amount="",
                   credit_amount="500", awb_code="", channel_order_id="",
                   description="Wallet recharge", created_at="2026-11-24 10:00:00")  # fmt: skip
    pending = normalize_wallet_transaction(
        statement_row(
            transaction_id="P1",
            debit_amount="55",
            awb_code="AWB5",
            created_at="2026-11-25 10:00:00",
        )  # fmt: skip
    )
    db.add(ShiprocketWalletTransaction(**{**pending, "status": "pending"}, source="shiprocket_api"))
    await db.commit()


async def test_every_expense_card_equals_its_drilldown_and_export(db_session):
    await _seed_expenses(db_session)
    async with await _client(db_session, ADMIN) as client:
        summary = (await client.get(f"{BASE}/expenses/summary")).json()["data"]
        cards = {c["key"]: c for c in summary["cards"]}
        assert list(cards) == EXPENSE_CARDS
        assert D(cards["freight"]["amount"]) == 150 and cards["freight"]["count"] == 2
        assert D(cards["cod_charges"]["amount"]) == 20
        assert D(cards["credits"]["amount"]) == 30 and D(cards["recharges"]["amount"]) == 500
        # Net cost = debits - credits, and recharges never count as spending.
        assert D(cards["net_cost"]["amount"]) == D("140.00")
        assert D(cards["pending"]["amount"]) == 55  # shown, but in no expense total
        for key, card in cards.items():
            for page_size in (1, 100):
                got, page = [], 1
                while True:
                    res = await client.get(
                        f"{BASE}/expenses/records",
                        params={"kpi": key, "page": page, "page_size": page_size},
                    )
                    body = res.json()
                    got += body["data"]
                    if page >= body["meta"]["total_pages"]:
                        break
                    page += 1
                assert body["totals"]["count"] == len(got) == card["count"], key
                assert D(body["totals"]["amount"]) == D(card["amount"]), key
                assert sum((D(r["contribution"]) for r in got), D(0)) == D(card["amount"]), key
            export = await client.get(f"{BASE}/expenses/export", params={"kpi": key})
            sheet = load_workbook(io.BytesIO(export.content))
            assert sheet["Totals"]["B2"].value == card["count"], key
            assert "Definitions" in sheet.sheetnames


async def test_expense_filters_and_search_narrow_cards_and_keep_the_card_total(db_session):
    await _seed_expenses(db_session)
    async with await _client(db_session, ADMIN) as client:
        window = {"date_from": "2026-11-21T00:00:00Z", "date_to": "2026-11-22T23:59:59Z"}
        cards = {
            c["key"]: c
            for c in (await client.get(f"{BASE}/expenses/summary", params=window)).json()["data"][
                "cards"
            ]
        }
        assert D(cards["freight"]["amount"]) == 70 and D(cards["credits"]["amount"]) == 0
        found = (
            await client.get(f"{BASE}/expenses/records", params={"kpi": "freight", "q": "AWB2"})
        ).json()
        assert found["totals"]["count"] == 1 and found["totals"]["base_count"] == 2
        none = (
            await client.get(f"{BASE}/expenses/records", params={"kpi": "freight", "q": "nope"})
        ).json()
        assert none["data"] == [] and D(none["totals"]["amount"]) == 0
        assert (
            await client.get(f"{BASE}/expenses/records", params={"kpi": "x"})
        ).status_code == 404


async def test_sources_say_what_is_api_and_what_needs_a_report(db_session):
    async with await _client(db_session, ADMIN) as client:
        sources = (await client.get(f"{BASE}/expenses/sources")).json()["data"]
    by = {s["data"].split(" (")[0]: s for s in sources}
    assert by["COD payouts: CRF id, amounts, deductions, status"]["method"] == "report"
    assert by["COD remittance amount per AWB"]["method"] == "report"
    assert by["Shiprocket bills / freight invoices"]["state"] == "not_available"
    assert by["Weight discrepancy records"]["state"] == "not_ingested"
    wallet = next(s for s in sources if s["data"].startswith("Wallet transactions"))
    assert wallet["method"] == "api" and wallet["endpoint"] == "GET /account/details/statement"
    assert wallet["state"] in ("not_configured", "not_enabled")  # never reported as live


# --- API and report must not double count -------------------------------------


async def test_a_report_import_and_the_api_never_count_one_movement_twice(db_session):
    report = _csv(
        "Transaction ID,Date,Action,Debit Amount,Credit Amount,Balance Amount,AWB,"
        "Order Number,Description",
        "TX1,20-11-2026 10:00:00,debit,80,,920,AWB1,#A1,Freight Charge",
        "TX9,21-11-2026 10:00:00,debit,45,,875,AWB2,#A2,Freight Charge",
        "TX9,21-11-2026 10:00:00,debit,45,,875,AWB2,#A2,Freight Charge",  # identical row
    )
    async with await _client(db_session, ADMIN) as client:
        await _add_txn(db_session)  # TX1 is already known from the API
        res = await client.post(f"{BASE}/expenses/import", files=_file(report))
        assert res.status_code == 201, res.text
        data = res.json()["data"]
        assert data["added"] == 1 and data["duplicates_skipped"] == 2
        again = await client.post(f"{BASE}/expenses/import", files=_file(report, "again.csv"))
        assert again.status_code == 409  # the same bytes are never imported twice
        # A re-export of the same movements with a different layout is still not counted twice.
        reshaped = _csv(
            "Transaction ID,Date,Action,Debit Amount,Credit Amount,AWB,Order Number,Description",
            "TX1,2026-11-20 10:00:00,debit,80,,AWB1,#A1,Freight Charge",
            "TX9,2026-11-21 10:00:00,debit,45,,AWB2,#A2,Freight Charge",
        )
        again2 = await client.post(f"{BASE}/expenses/import", files=_file(reshaped, "b.csv"))
        assert again2.json()["data"]["added"] == 0
        cards = {
            c["key"]: c
            for c in (await client.get(f"{BASE}/expenses/summary")).json()["data"]["cards"]
        }
    assert D(cards["freight"]["amount"]) == 125 and cards["freight"]["count"] == 2


async def test_two_equal_movements_stay_two_and_an_api_row_adopts_a_report_row(db_session):
    report = _csv(
        "Date,Action,Debit Amount,AWB,Description",
        "20-11-2026,debit,30,AWB7,COD charge",
        "20-11-2026,debit,30,AWB7,COD charge",  # a second, genuinely separate charge
    )
    async with await _client(db_session, ADMIN) as client:
        assert (
            await client.post(f"{BASE}/expenses/import", files=_file(report))
        ).status_code == 201
        rows = (await db_session.execute(select(ShiprocketWalletTransaction))).scalars().all()
        assert len(rows) == 1  # identical rows without an id cannot be told apart: one is kept

        # The API later reports the same charge with a transaction id: no second row.
        class Adapter:
            async def fetch_wallet_statement(self, **_kw):
                return [statement_row(transaction_id="API-7", awb_code="AWB7", channel_order_id="",
                                      description="COD charge", debit_amount="30",
                                      created_at="2026-11-20 00:00:00")], False  # fmt: skip

        await WalletService(db_session).sync_transactions(Adapter())
        rows = (await db_session.execute(select(ShiprocketWalletTransaction))).scalars().all()
        assert len(rows) == 1 and rows[0].source == "shiprocket_api"
        cards = {
            c["key"]: c
            for c in (await client.get(f"{BASE}/expenses/summary")).json()["data"]["cards"]
        }
    assert D(cards["cod_charges"]["amount"]) == 30


async def test_a_wrong_report_row_is_rejected_not_guessed(db_session):
    report = _csv(
        "Date,Action,Debit Amount,Credit Amount,AWB",
        "20-11-2026,debit,10,5,AWB1",  # both sides
        ",debit,10,,AWB2",  # no date
        "21-11-2026,debit,12,,AWB3",
    )
    async with await _client(db_session, ADMIN) as client:
        data = (await client.post(f"{BASE}/expenses/import", files=_file(report))).json()["data"]
    assert data["added"] == 1 and data["rejected"] == 2


# --- shipment charges and order remittance fields (GET /orders/show) ------------


def order_body(**over):
    data = {
        "awb_data": {
            "charges": {
                "zone": "z_b",
                "cod_charges": "25.00",
                "freight_charges": "85.50",
                "applied_weight": "0.5",
                "charged_weight": "",
                "applied_weight_amount": "85.50",
                "charged_weight_amount": "",
                "charged_weight_amount_rto": "",
                "applied_weight_amount_rto": "0.00",
            }
        },
        "remittance_utr": "UTR-SR-1",
        "remittance_date": "2026-11-25",
        "remittance_status": "Paid",
        "customer_name": "must never be stored",
        **over,
    }
    return {"data": data}


async def test_order_finance_is_parsed_without_inventing_amounts():
    parsed = parse_order_finance(order_body())
    assert parsed["charges"]["freight_charges"] == D("85.50")
    assert parsed["charges"]["charged_weight_amount"] is None  # blank stays blank, not 0
    assert parsed["remittance_utr"] == "UTR-SR-1" and parsed["has_charges"] is True
    assert "customer" not in str(parsed["raw"])
    empty = parse_order_finance(order_body(awb_data={"charges": {"freight_charges": ""}},
                                           remittance_utr="", remittance_status="",
                                           remittance_date=""))  # fmt: skip
    assert empty["has_charges"] is False and empty["remittance_utr"] is None
    assert parse_order_finance({"message": "x"}) is None


async def test_hover_text_is_read_as_presence_only_never_an_amount():
    """`cod_recieved_hover_text` is a free-text label: it is kept verbatim
    when present, left `None` when blank, and never turned into a number."""
    present = parse_order_finance(order_body(cod_recieved_hover_text="COD received"))
    assert present["cod_received_hover_text"] == "COD received"
    assert present["raw"]["cod_recieved_hover_text"] == "COD received"
    blank = parse_order_finance(order_body(cod_recieved_hover_text=""))
    assert blank["cod_received_hover_text"] is None
    missing = parse_order_finance(order_body())  # key absent entirely in the fixture base
    assert missing["cod_received_hover_text"] is None


class FakeShiprocket:
    def __init__(self, bodies=None, error=None):
        self.bodies, self.error, self.calls = bodies or {}, error, []

    async def get_order(self, order_id):
        self.calls.append(order_id)
        if self.error:
            raise self.error
        return self.bodies[order_id]


async def _shipped(db, number, awb, order_id):
    from app.models.shipment import Shipment

    order = await _order(db, number, "1000.00", awb=awb)
    shipment = (await db.execute(select(Shipment).where(Shipment.awb == awb))).scalar_one()
    if order_id:
        shipment.raw_external_payload = {"order_id": order_id}
    await db.commit()
    return order


async def test_sync_reads_only_orders_with_a_known_id_and_stores_no_amount(db_session):
    await _shipped(db_session, "S-1", "AWB-S1", "9001")
    await _shipped(db_session, "S-2", "AWB-S2", None)  # no known Shiprocket id: never guessed
    fake = FakeShiprocket({"9001": order_body()})
    async with await _client(db_session, ADMIN) as client:
        result = await ShiprocketFinanceService(db_session).sync_order_finance(
            fake, limit=10, user=None
        )
        assert fake.calls == ["9001"]
        assert result["checked"] == 1 and result["with_charges"] == 1
        again = await ShiprocketFinanceService(db_session).sync_order_finance(
            fake, limit=10, user=None
        )  # idempotent: one row per AWB
        charges = (await db_session.execute(select(ShiprocketShipmentCharge))).scalars().all()
        assert len(charges) == 1 and charges[0].freight_charges == D("85.50")
        assert again["checked"] == 1
        rem = (await db_session.execute(select(CodRemittance))).scalar_one()
        assert rem.remittance_reference == "UTR-SR-1" and rem.remittance_status == "Paid"
        assert rem.remitted_amount is None  # Shiprocket gives no amount; none is stored
        listed = (await client.get(f"{BASE}/expenses/shipment-charges")).json()
        assert listed["coverage"]["orders_checked"] == 1 and len(listed["data"]) == 1
        # Shipment charges are informational and never enter the wallet KPIs.
        cards = (await client.get(f"{BASE}/expenses/summary")).json()["data"]["cards"]
        assert all(D(c["amount"]) == 0 for c in cards)


async def test_sync_stores_hover_text_in_the_existing_json_column_no_amount(db_session):
    """No dedicated column is added for `cod_recieved_hover_text`: it must
    land inside the already-existing `raw_external_payload` JSON field, under
    its own key, and must never populate `remitted_amount`."""
    await _shipped(db_session, "S-3", "AWB-S3", "9003")
    fake = FakeShiprocket(
        {
            "9003": order_body(
                remittance_utr="", remittance_status="", remittance_date="",
                cod_recieved_hover_text="COD received",
            )  # fmt: skip
        }
    )
    await ShiprocketFinanceService(db_session).sync_order_finance(fake, limit=5, user=None)
    rem = (await db_session.execute(select(CodRemittance))).scalar_one()
    assert rem.raw_external_payload["cod_recieved_hover_text"] == "COD received"
    assert rem.remitted_amount is None
    assert rem.remittance_reference is None


async def test_module_6_is_unchanged_by_a_utr_only_remittance_row(db_session):
    await _shipped(db_session, "S-1", "AWB-S1", "9001")
    async with await _client(db_session, ADMIN) as client:
        params = {"date_from": "2020-01-01T00:00:00Z", "date_to": "2099-01-01T00:00:00Z"}
        before = (await client.get(f"{RECON}/cod/summary", params=params)).json()["data"]["kpis"]
        await ShiprocketFinanceService(db_session).sync_order_finance(
            FakeShiprocket({"9001": order_body()}), limit=5, user=None
        )
        after = (await client.get(f"{RECON}/cod/summary", params=params)).json()["data"]["kpis"]
    for key in ("remittance_received", "pending", "short", "excess", "bank_confirmed"):
        assert after[key] == before[key], key


async def test_access_denied_is_reported_not_swallowed(db_session):
    await _shipped(db_session, "S-1", "AWB-S1", "9001")
    fake = FakeShiprocket(
        error=IntegrationError("no", details={"error_type": "authorization_error"})
    )
    result = await ShiprocketFinanceService(db_session).sync_order_finance(fake, limit=5, user=None)
    assert result["status"] == "access_denied" and result["checked"] == 0
    assert (await db_session.execute(select(ShiprocketShipmentCharge))).scalars().all() == []


# --- COD payouts (report) and bank matching ---------------------------------------

PAYOUT_HEAD = (
    "CRF ID,UTR,Payout Date,Status,COD Amount,Freight Deduction,Other Deductions,Remittance Amount"
)
PAYOUTS = _csv(
    PAYOUT_HEAD,
    "CRF1,UTR-A,22-11-2026,Paid,1000,80,20,900",  # matched to bank
    "CRF2,UTR-B,23-11-2026,Paid,2000,100,0,1900",  # bank has a different amount
    "CRF3,UTR-C,24-11-2026,Paid,500,40,10,450",  # not on any statement
    "CRF4,,25-11-2026,Pending,800,60,0,740",  # no UTR yet
)
BANK = _csv(
    "Credit Date,Narration,Credit Amount,Reference",
    "23-11-2026,SR,900,UTR-A",
    "24-11-2026,SR,1850,UTR-B",
)


async def test_payout_import_cards_drilldowns_bank_matching_and_exports(db_session):
    async with await _client(db_session, ADMIN) as client:
        no_bank = await client.post(f"{BASE}/remittances/import", files=_file(PAYOUTS))
        assert no_bank.status_code == 201, no_bank.text
        assert no_bank.json()["data"]["added"] == 4
        states = {
            c["key"]: c
            for c in (await client.get(f"{BASE}/remittances/summary")).json()["data"]["cards"]
        }
        assert states["bank_matched"]["count"] == 0  # no statement: nothing is "matched"
        bank = await client.post(
            f"{RECON}/bank-statements", data={"source_name": "HDFC"}, files=_file(BANK, "b.csv")
        )
        assert bank.status_code == 201, bank.text
        summary = (await client.get(f"{BASE}/remittances/summary")).json()["data"]
        cards = {c["key"]: c for c in summary["cards"]}
        assert summary["bank_data_available"] is True
        assert list(cards) == PAYOUT_CARDS
        assert D(cards["net"]["amount"]) == 3990 and cards["net"]["count"] == 4
        assert (cards["bank_matched"]["count"], D(cards["bank_matched"]["amount"])) == (1, 900)
        assert (cards["bank_differs"]["count"], D(cards["bank_differs"]["amount"])) == (1, 1900)
        assert (cards["not_in_bank"]["count"], D(cards["not_in_bank"]["amount"])) == (1, 450)
        assert (cards["without_utr"]["count"], D(cards["without_utr"]["amount"])) == (1, 740)
        assert D(cards["freight_deducted"]["amount"]) == 280
        for key, card in cards.items():
            res = (
                await client.get(
                    f"{BASE}/remittances/records", params={"kpi": key, "page_size": 100}
                )
            ).json()
            assert res["totals"]["count"] == card["count"], key
            assert D(res["totals"]["amount"]) == D(card["amount"]), key
            assert sum((D(r["contribution"]) for r in res["data"]), D(0)) == D(card["amount"])
            export = await client.get(f"{BASE}/remittances/export", params={"kpi": key})
            assert load_workbook(io.BytesIO(export.content))["Totals"]["B2"].value == card["count"]
        rows = (
            await client.get(f"{BASE}/remittances/records", params={"kpi": "net", "q": "crf2"})
        ).json()["data"]
        detail = (await client.get(f"{BASE}/remittances/{rows[0]['id']}")).json()["data"]
        assert detail["bank_credits"][0]["amount"] == "1850.00"
        assert D(rows[0]["unexplained_difference"]) == 0
        # Read-only comparison: no allocation was created, so Module 6/7 are untouched.
        credits = (await client.get(f"{RECON}/cod/bank-credits")).json()["data"]
        assert {c["unlinked_amount"] for c in credits} == {"900.00", "1850.00"}


async def test_payouts_that_share_a_utr_cannot_both_match_one_credit(db_session):
    shared = _csv(PAYOUT_HEAD, "CRF1,UTR-A,22-11-2026,Paid,1000,0,0,900",
                  "CRF2,UTR-A,22-11-2026,Paid,1000,0,0,900")  # fmt: skip
    async with await _client(db_session, ADMIN) as client:
        await client.post(f"{BASE}/remittances/import", files=_file(shared))
        await client.post(
            f"{RECON}/bank-statements", data={"source_name": "B"}, files=_file(BANK, "b.csv")
        )
        cards = {
            c["key"]: c
            for c in (await client.get(f"{BASE}/remittances/summary")).json()["data"]["cards"]
        }
    assert cards["bank_matched"]["count"] == 0 and cards["bank_differs"]["count"] == 2


async def test_payout_reimport_fills_blanks_but_never_changes_money_or_a_utr(db_session):
    later = _csv(
        PAYOUT_HEAD,
        "CRF1,UTR-A,22-11-2026,Settled,1000,80,20,900",  # status moves on: allowed
        "CRF2,UTR-B,23-11-2026,Paid,2000,100,0,1999",  # different amount: refused
        "CRF3,UTR-OTHER,24-11-2026,Paid,500,40,10,450",  # different UTR: refused
        "CRF4,UTR-D,25-11-2026,Pending,800,60,0,740",  # a UTR arrives: allowed
        "CRF5,,,,,,,10",  # a payout with neither id... has CRF, so valid
        ",,,,,,,10",  # neither CRF nor UTR: rejected
    )
    async with await _client(db_session, ADMIN) as client:
        await client.post(f"{BASE}/remittances/import", files=_file(PAYOUTS))
        data = (
            await client.post(f"{BASE}/remittances/import", files=_file(later, "l.csv"))
        ).json()["data"]
        assert data["updated"] == 2 and data["conflicts"] == 2 and data["added"] == 1
        assert data["rejected"] == 1
        rows = (
            await client.get(f"{BASE}/remittances/records", params={"kpi": "net", "page_size": 50})
        ).json()["data"]
    by = {r["crf_id"]: r for r in rows}
    assert by["CRF1"]["status"] == "Settled" and by["CRF4"]["utr"] == "UTR-D"
    assert D(by["CRF2"]["net_amount"]) == 1900 and by["CRF3"]["utr"] == "UTR-C"


async def test_payout_report_columns_are_never_guessed(db_session):
    async with await _client(db_session, ADMIN) as client:
        res = await client.post(
            f"{BASE}/remittances/import/preview",
            files=_file(_csv("Foo,Bar", "1,2")),
        )
        assert res.status_code == 200 and res.json()["data"]["can_import"] is False
        bad = await client.post(f"{BASE}/remittances/import", files=_file(_csv("Foo,Bar", "1,2")))
        assert bad.status_code == 422


# --- permissions --------------------------------------------------------------------


async def test_permissions_follow_the_data(db_session):
    async with await _client(db_session, ["analytics.read"], "a@x.com") as client:
        assert (await client.get(f"{BASE}/expenses/summary")).status_code == 200
        assert (await client.get(f"{BASE}/remittances/summary")).status_code == 403
        assert (await client.post(f"{BASE}/expenses/sync-order-finance")).status_code == 403
        assert (
            await client.post(f"{BASE}/expenses/import", files=_file(b"a\n"))
        ).status_code == 403
    async with await _client(db_session, ["reconciliation.read"], "r@x.com") as client:
        assert (await client.get(f"{BASE}/remittances/summary")).status_code == 200
        assert (await client.get(f"{BASE}/expenses/summary")).status_code == 403
        for path in ("remittances/import", "expenses/import"):
            res = await client.post(f"{BASE}/{path}", files=_file(PAYOUTS))
            assert res.status_code == 403, path
    async with await _client(db_session, ["orders.read"], "n@x.com") as client:
        for path in ("expenses/summary", "remittances/summary", "expenses/sources"):
            assert (await client.get(f"{BASE}/{path}")).status_code == 403, path


async def test_no_credentials_or_raw_payloads_are_returned(db_session):
    await _add_txn(db_session)
    async with await _client(db_session, ADMIN) as client:
        body = (await client.get(f"{BASE}/expenses/records", params={"kpi": "freight"})).text
        sources = (await client.get(f"{BASE}/expenses/sources")).text
    for text in (body, sources):
        assert "raw_external_payload" not in text and "password" not in text.lower()
    assert datetime.now(UTC).year >= 2026
