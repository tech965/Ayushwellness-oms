"""Every reconciliation KPI card must open a drilldown whose records add up to
the card -- under no filter, status / partner / date filters and empty
results -- with pagination, search, export, order detail and RBAC.
Synthetic data only.
"""

from __future__ import annotations

import io
from decimal import Decimal

import pytest
from app.db.session import get_db
from app.main import app
from app.models.remittance_reconciliation import GatewaySettlementLine
from openpyxl import load_workbook

from tests import test_cod_remittance_reconciliation as cod_data
from tests import test_prepaid_remittance_reconciliation as prepaid_data
from tests.telecalling_test_utils import bearer_client, make_role, make_user

pytestmark = pytest.mark.asyncio

BASE = "/api/v1/remittance-reconciliation"
D = Decimal

# card -> (drill selector, summary KPI key). The amount shown on each card.
COD_CARDS = {
    "Total COD to be Settled": ("expected", "expected_collection"),
    "Total COD Settled": ("received", "remittance_received"),
    "Pending Settlement": ("pending", "pending"),
    "Short Settlement": ("short", "short"),
    "Excess Settlement": ("excess", "excess"),
    "Unmatched Remittances": ("unmatched", "unmatched"),
    "Reconciled Orders": ("reconciled", "reconciled"),
    "Total COD Delivered Orders": ("delivered", "delivered_orders"),
    "COD Bank Confirmed": ("bank_confirmed", "bank_confirmed"),
    "Unmatched Bank Credits": ("unmatched_bank_credits", "unmatched_bank_credits"),
}
PREPAID_CARDS = {
    "Total Prepaid Collected": ("gross", "gross_collection"),
    "Total Settlement Received": ("reported_settlement", "reported_settlement"),
    "Prepaid Bank Confirmed": ("bank_settlement", "bank_settlement"),
    "Unmatched Bank Credits": ("unmatched_bank_credits", "unmatched_bank_credits"),
    "Settlement Pending": ("pending", "pending"),
    "Gateway Charges": ("charges", "gateway_charges"),
    "GST": ("gst", "gst"),
    "Refunds": ("refunds", "refunds"),
    "Settlement Difference": ("difference", "settlement_difference"),
    "Unmatched Transactions": ("unmatched", "unmatched"),
    "Reconciled Transactions": ("reconciled", "reconciled"),
    "Total Prepaid Orders": ("orders", "total_prepaid_orders"),
    "Expected Net Settlement": ("expected_net", "expected_net"),
}


@pytest.fixture(autouse=True)
def _clear_override():
    yield
    app.dependency_overrides.clear()


async def _admin(db, email="drill@example.com"):
    role = await make_role(
        db,
        name=f"R-{email}",
        permission_codes=["reconciliation.read", "reconciliation.upload", "reconciliation.adjust"],
    )
    return await make_user(db, email=email, role=role)


def _client(db, user):
    return bearer_client(app, get_db, db, user.id)


# Clickable figures outside the 25 cards (bank confirmation / adjustments).
FIGURES = {
    "cod": {
        "Remaining amount": ("bank_remaining", "bank_remaining"),
        "Partially Confirmed": ("partially_confirmed", "partially_confirmed"),
        "Manual adjustments": ("adjusted", "adjusted"),
    },
}
FIGURES["prepaid"] = {**FIGURES["cod"], "Unverified lines": ("unverified", "unverified")}

UNMATCHED_CARDS = ("unmatched", "unmatched_bank_credits")


def _unmatched_kind(module: str, kpi: str) -> str:
    if kpi == "unmatched_bank_credits":
        return "bank_credit"
    return "remittance_line" if module == "cod" else "gateway_line"


def _path(module: str, kpi: str) -> tuple[str, dict]:
    if kpi in UNMATCHED_CARDS:
        return f"{BASE}/{module}/unmatched", {"kind": _unmatched_kind(module, kpi)}
    # "all_unmatched" is the mixed listing (the Unmatched report tab), not a card.
    if kpi == "all_unmatched":
        return f"{BASE}/{module}/unmatched", {}
    return f"{BASE}/{module}/records", {"kpi": kpi}


async def _walk(client, module: str, kpi: str, params: dict, page_size: int = 2):
    """All pages (small page size, so pagination is really exercised)."""
    url, extra = _path(module, kpi)
    rows, page, totals = [], 1, None
    while True:
        res = await client.get(
            url, params={**params, **extra, "page": page, "page_size": page_size}
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert totals is None or body["totals"] == totals  # same total on every page
        totals = body["totals"]
        rows += body["data"]
        assert len(body["data"]) <= page_size
        if page >= body["meta"]["total_pages"]:
            return rows, totals, body["meta"]["total_items"]
        page += 1


async def _check_every_card(client, module: str, cards: dict, params: dict) -> dict:
    res = await client.get(f"{BASE}/{module}/summary", params=params)
    assert res.status_code == 200, res.text
    kpis = res.json()["data"]["kpis"]
    seen = {}
    for label, (kpi, key) in {**cards, **FIGURES[module]}.items():
        rows, totals, total_items = await _walk(client, module, kpi, params)
        card = kpis[key]
        assert total_items == len(rows) == totals["count"] == card["count"], (label, params)
        assert D(totals["amount"]) == D(card["amount"]), (label, params, totals, card)
        assert sum((D(r["contribution"]) for r in rows), D(0)) == D(card["amount"]), label
        assert D(totals["base_amount"]) == D(card["amount"]), label
        assert len({(r.get("order_id") or r.get("id")) for r in rows}) == len(rows), label
        seen[label] = len(rows)
    return seen


# --- every card, with and without filters --------------------------------------


async def test_every_cod_card_reconciles_with_its_drilldown(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        await cod_data._setup_full(db_session, client)
        base = cod_data._date_params()
        seen = await _check_every_card(client, "cod", COD_CARDS, base)
        assert seen["Total COD to be Settled"] > 0 and seen["Unmatched Remittances"] > 0
        for extra in (
            {"status": "short"},
            {"status": "matched"},
            {"status": "pending"},
            {"partner": "Delhivery"},
        ):
            await _check_every_card(client, "cod", COD_CARDS, {**base, **extra})
        # A window with no data: every card opens an empty drilldown totalling zero.
        empty = {"date_from": "2001-01-01T00:00:00Z", "date_to": "2001-01-02T00:00:00Z"}
        res = await client.get(f"{BASE}/cod/summary", params=empty)
        assert res.json()["data"]["kpis"]["expected_collection"]["count"] == 0
        for label, (kpi, _key) in COD_CARDS.items():
            rows, totals, _ = await _walk(client, "cod", kpi, empty)
            if kpi not in UNMATCHED_CARDS:  # undated unmatched evidence is never hidden
                assert rows == [] and D(totals["amount"]) == 0, label


async def test_every_prepaid_card_reconciles_with_its_drilldown(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        await prepaid_data._setup(db_session, client)
        base = prepaid_data._dp()
        seen = await _check_every_card(client, "prepaid", PREPAID_CARDS, base)
        assert seen["Gateway Charges"] > 0 and seen["Settlement Difference"] > 0
        for extra in (
            {"status": "pending"},
            {"status": "matched"},
            {"status": "short"},
            {"status": "refunded"},
            {"status": "failed"},
            {"gateway": "Cashfree"},
        ):
            await _check_every_card(client, "prepaid", PREPAID_CARDS, {**base, **extra})
        empty = {"date_from": "2001-01-01T00:00:00Z", "date_to": "2001-01-02T00:00:00Z"}
        for label, (kpi, _key) in PREPAID_CARDS.items():
            rows, totals, _ = await _walk(client, "prepaid", kpi, empty)
            if kpi not in UNMATCHED_CARDS:
                assert rows == [] and D(totals["amount"]) == 0, label


async def test_negative_charge_adjustments_still_reconcile(db_session):
    """A reversal line (negative charge) must appear in the Charges drilldown,
    otherwise the listed records would not add up to the card."""
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        await prepaid_data._setup(db_session, client)
        line = GatewaySettlementLine(
            gateway="Cashfree",
            order_ref="P5",
            gateway_charges=D("-5.00"),
            gst_amount=D("-0.90"),
            net_amount=D("5.90"),
            line_status="success",
            dedupe_key="neg-1",
            settlement_reference="UNEG",
        )
        rows, _, _ = await _walk(client, "prepaid", "orders", prepaid_data._dp())
        line.matched_order_id = __import__("uuid").UUID(
            next(r for r in rows if r["order_number"].lstrip("#") == "P5")["order_id"]
        )
        db_session.add(line)
        await db_session.commit()
        await _check_every_card(client, "prepaid", PREPAID_CARDS, prepaid_data._dp())
        charged, _, _ = await _walk(client, "prepaid", "charges", prepaid_data._dp())
        assert any(D(r["contribution"]) < 0 for r in charged)


# --- fields, search ---------------------------------------------------------------


async def test_cod_rows_carry_the_required_fields(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        await cod_data._setup_full(db_session, client)
        rows, _, _ = await _walk(client, "cod", "expected", cod_data._date_params())
    for field in (
        "order_number",
        "customer_name",
        "order_datetime",
        "payment_status",
        "delivery_status",
        "order_amount",
        "awb",
        "partner",
        "expected_amount",
        "remittance_amount",
        "remittance_date",
        "difference",
    ):
        assert all(field in r for r in rows), field
    assert all(r["payment_status"] and r["delivery_status"] == "delivered" for r in rows)


async def test_prepaid_rows_expose_cashfree_ids_only_where_available(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        await prepaid_data._setup(db_session, client)
        before, _, _ = await _walk(client, "prepaid", "orders", prepaid_data._dp())
        p2 = next(r for r in before if r["order_number"].lstrip("#") == "P2")
        # Uploaded file lines have no settlement id: shown as missing, not guessed.
        assert p2["settlement_id"] is None and p2["bank_reference"] == "U2"
        assert p2["payment_status"] and p2["delivery_status"]
        db_session.add(
            GatewaySettlementLine(
                gateway="Cashfree",
                source="api",
                gateway_transaction_id="CF-PAY-9",
                cf_settlement_id="CF-SET-9",
                settlement_reference="CF-UTR-9",
                gross_amount=D("300"),
                net_amount=D("290"),
                gateway_charges=D("8"),
                gst_amount=D("2"),
                line_status="success",
                dedupe_key="api-unmatched",
            )
        )
        await db_session.commit()
        rows, totals, _ = await _walk(client, "prepaid", "all_unmatched", prepaid_data._dp())
        api = next(r for r in rows if r["settlement_id"] == "CF-SET-9")
        assert api["cashfree_payment_id"] == "CF-PAY-9" and api["reference"] == "CF-UTR-9"
        assert D(api["gross_amount"]) == 300 and D(api["gateway_charges"]) == 8
        assert D(api["gst_amount"]) == 2 and D(api["amount"]) == 290
        assert sum(D(p["amount"]) for p in totals["breakdown"]) == D(totals["amount"])
        assert sum(p["count"] for p in totals["breakdown"]) == totals["count"]
        found, ftotals, _ = await _walk(
            client, "prepaid", "all_unmatched", {**prepaid_data._dp(), "q": "CF-SET-9"}
        )
        assert len(found) == 1 and ftotals["count"] == 1
        assert ftotals["base_count"] == totals["count"]


async def test_search_keeps_the_card_total_alongside_the_filtered_total(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        await cod_data._setup_full(db_session, client)
        params = cod_data._date_params()
        everything, all_totals, _ = await _walk(client, "cod", "expected", params)
        one = everything[0]["order_number"]
        rows, totals, _ = await _walk(client, "cod", "expected", {**params, "q": one})
        assert rows and totals["count"] == len(rows) < all_totals["count"]
        assert D(totals["amount"]) == sum(D(r["contribution"]) for r in rows)
        assert D(totals["base_amount"]) == D(all_totals["amount"])
        assert totals["base_count"] == all_totals["count"]
        none, nt, _ = await _walk(client, "cod", "expected", {**params, "q": "no-such-thing"})
        assert none == [] and D(nt["amount"]) == 0 and nt["base_count"] == all_totals["count"]


# --- Excel export of the drilldown ----------------------------------------------------


def _sheet(content: bytes):
    wb = load_workbook(io.BytesIO(content))
    records = list(wb["Records"].iter_rows(values_only=True))
    totals = {r[0]: r[1] for r in wb["Totals"].iter_rows(values_only=True)}
    return records, totals


@pytest.mark.parametrize("module", ["cod", "prepaid"])
async def test_drilldown_export_matches_every_card(db_session, module):
    user = await _admin(db_session)
    data = cod_data if module == "cod" else prepaid_data
    cards = COD_CARDS if module == "cod" else PREPAID_CARDS
    async with _client(db_session, user) as client:
        await (data._setup_full if module == "cod" else data._setup)(db_session, client)
        params = data._date_params() if module == "cod" else data._dp()
        for label, (kpi, _key) in cards.items():
            _rows, totals, count = await _walk(client, module, kpi, params, page_size=100)
            query = (
                {"view": "unmatched", "kind": _unmatched_kind(module, kpi)}
                if kpi in UNMATCHED_CARDS
                else {"kpi": kpi}
            )
            res = await client.get(f"{BASE}/{module}/drilldown/export", params={**params, **query})
            assert res.status_code == 200, res.text
            records, summary = _sheet(res.content)
            assert len(records) - 1 == count, label
            assert summary["Records exported"] == count, label
            assert D(str(summary["Total of exported records"])) == D(totals["amount"]), label
            if kpi in UNMATCHED_CARDS:
                if module == "prepaid":
                    column = records[0].index("Counts toward total")
                else:
                    column = records[0].index("Amount")
                assert sum(D(str(r[column])) for r in records[1:]) == D(totals["amount"]), label
            else:
                column = records[0].index(f"Counts toward card: {totals['measure']}")
                exported = sum(D(str(r[column])) for r in records[1:])
                assert exported == D(totals["amount"]), label


async def test_export_follows_search_and_status_and_never_writes_formulas(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        await cod_data._setup_full(db_session, client)
        params = cod_data._date_params()
        rows, _, _ = await _walk(client, "cod", "expected", params)
        from app.models.order import Order
        from sqlalchemy import select

        order = (
            await db_session.execute(
                select(Order).where(Order.order_number == rows[0]["order_number"])
            )
        ).scalar_one()
        order.order_number = '=HYPERLINK("http://x","y")'
        await db_session.commit()
        res = await client.get(
            f"{BASE}/cod/drilldown/export", params={**params, "kpi": "expected", "q": "HYPERLINK"}
        )
        sheet = load_workbook(io.BytesIO(res.content))["Records"]
        assert sheet.max_row == 2
        assert sheet["A2"].data_type == "s" and sheet["A2"].value.startswith("=")
        filtered = await client.get(
            f"{BASE}/cod/drilldown/export",
            params={**params, "kpi": "expected", "status": "short"},
        )
        _records, summary = _sheet(filtered.content)
        expected, _, _ = await _walk(client, "cod", "expected", {**params, "status": "short"})
        assert summary["Records exported"] == len(expected)


# --- order detail ------------------------------------------------------------------


async def test_cod_order_detail_matches_the_record_and_lists_evidence(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        await cod_data._setup_full(db_session, client)
        rows, _, _ = await _walk(client, "cod", "reconciled", cod_data._date_params())
        row = rows[0]
        res = await client.get(f"{BASE}/cod/orders/{row['order_id']}/detail")
        assert res.status_code == 200, res.text
        data = res.json()["data"]
    record = data["record"]
    for key in ("order_number", "status", "expected_amount", "remittance_amount", "difference"):
        assert str(record[key]) == str(row[key]), key
    assert data["lines"] and data["bank_links"]
    assert sum(D(str(line["amount"])) for line in data["lines"]) == D(str(row["remittance_amount"]))


async def test_prepaid_order_detail_matches_the_record_and_lists_evidence(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        await prepaid_data._setup(db_session, client)
        rows, _, _ = await _walk(client, "prepaid", "orders", prepaid_data._dp())
        for row in rows:
            res = await client.get(f"{BASE}/prepaid/orders/{row['order_id']}/detail")
            assert res.status_code == 200, res.text
            data = res.json()["data"]
            record = data["record"]
            for key in ("status", "gross_amount", "actual_settlement", "difference"):
                assert str(record[key]) == str(row[key]), (row["order_number"], key)
            counted = [ln for ln in data["lines"] if ln["counted"]]
            assert sum(D(str(ln["amount"])) for ln in counted) == D(str(row["actual_settlement"]))
        assert "raw_row" not in res.text and "raw_payload" not in res.text


async def test_order_detail_404_for_an_order_outside_the_module(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        await prepaid_data._setup(db_session, client)
        rows, _, _ = await _walk(client, "prepaid", "orders", prepaid_data._dp())
        res = await client.get(f"{BASE}/cod/orders/{rows[0]['order_id']}/detail")
    assert res.status_code == 404


# --- RBAC -------------------------------------------------------------------------


@pytest.mark.parametrize("module", ["cod", "prepaid"])
async def test_read_only_users_can_view_but_not_change(db_session, module):
    admin = await _admin(db_session)
    data = cod_data if module == "cod" else prepaid_data
    async with _client(db_session, admin) as client:
        await (data._setup_full if module == "cod" else data._setup)(db_session, client)
        params = data._date_params() if module == "cod" else data._dp()
        rows, _, _ = await _walk(client, module, "unmatched", params)
    order_kpi = "expected" if module == "cod" else "orders"
    role = await make_role(db_session, name="RO", permission_codes=["reconciliation.read"])
    reader = await make_user(db_session, email="ro@example.com", role=role)
    async with _client(db_session, reader) as client:
        found, _, _ = await _walk(client, module, order_kpi, params)
        order_id = found[0]["order_id"]
        assert (await client.get(f"{BASE}/{module}/orders/{order_id}/detail")).status_code == 200
        export = await client.get(
            f"{BASE}/{module}/drilldown/export", params={**params, "kpi": order_kpi}
        )
        assert export.status_code == 200
        blocked = [
            await client.post(f"{BASE}/{module}/rematch"),
            await client.post(
                f"{BASE}/{module}/adjustments",
                json={"order_id": order_id, "amount": "1.00", "reason": "x"},
            ),
            await client.post(
                f"{BASE}/{module}/lines/{rows[0]['id']}/match",
                json={"order_id": order_id, "confirm": True},
            ),
            await client.post(
                f"{BASE}/{module}/uploads",
                data={"partner" if module == "cod" else "gateway": "X"},
                files={"file": ("a.csv", io.BytesIO(b"a\n1\n"), "text/csv")},
            ),
        ]
    assert [r.status_code for r in blocked] == [403, 403, 403, 403]
    outsider_role = await make_role(db_session, name="NONE", permission_codes=["orders.read"])
    outsider = await make_user(db_session, email="none@example.com", role=outsider_role)
    async with _client(db_session, outsider) as client:
        for url in (
            f"{BASE}/{module}/records",
            f"{BASE}/{module}/unmatched",
            f"{BASE}/{module}/drilldown/export",
            f"{BASE}/{module}/orders/{order_id}/detail",
        ):
            assert (await client.get(url, params=params)).status_code == 403, url
