"""Module 7 -- Prepaid Remittance Reconciliation: gross, charges, GST,
refunds, expected net, bank evidence, failed/pending/refunded, unmatched,
duplicates, drilldown consistency, percentages, RBAC and exports.
Synthetic data only."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.db.session import get_db
from app.main import app
from app.models.audit_log import AuditLog
from app.models.enums import PaymentStatus, PaymentType, RefundStatus
from app.models.payment import Payment
from app.models.refund import Refund
from openpyxl import load_workbook
from sqlalchemy import select

from tests.telecalling_test_utils import bearer_client, make_order, make_role, make_user

pytestmark = pytest.mark.asyncio

NOW = datetime.now(UTC).replace(microsecond=0)
BASE = "/api/v1/remittance-reconciliation"
ALL = ["reconciliation.read", "reconciliation.upload", "reconciliation.adjust"]


@pytest.fixture(autouse=True)
def _clear_override():
    yield
    app.dependency_overrides.clear()


async def _user(db, email, perms=ALL):
    role = await make_role(db, name=f"R-{email}", permission_codes=list(perms))
    return await make_user(db, email=email, role=role)


async def _order(
    db,
    number,
    *,
    status=PaymentStatus.PAID,
    txn=None,
    amount="1000.00",
    provider="cashfree",
    days_ago=5,
    payment_type=PaymentType.PREPAID,
    refund=None,
):
    started = NOW - timedelta(days=days_ago)
    order = await make_order(
        db,
        order_number=number,
        payment_type=payment_type,
        total_amount=Decimal(amount),
        order_datetime=started,
    )
    db.add(
        Payment(
            order_id=order.id,
            payment_type=payment_type,
            status=status,
            amount=Decimal(amount),
            provider=provider,
            external_transaction_id=txn,
            paid_at=started + timedelta(minutes=5) if status == PaymentStatus.PAID else None,
            source_system="manual",
        )
    )
    if refund is not None:
        db.add(
            Refund(
                order_id=order.id,
                amount=Decimal(refund),
                status=RefundStatus.COMPLETED,
                source_system="manual",
            )
        )
    await db.commit()
    return order


def _csv(*lines):
    return ("\n".join(lines) + "\n").encode()


async def _seed(db):
    await _order(db, "P1", txn="T1")
    await _order(db, "P2", txn="T2")
    await _order(db, "P3", txn="T3")
    await _order(db, "P4", txn="T4")
    await _order(db, "P5", txn="T5")  # paid, never settled
    await _order(db, "P6", status=PaymentStatus.FAILED, txn="T6")
    await _order(db, "P7", txn="T7", refund="1000.00")  # fully refunded
    await _order(db, "P8", txn="T8", refund="400.00")  # partly refunded
    await _order(db, "P9", status=PaymentStatus.PENDING, txn="T9")  # not captured
    await _order(db, "COD1", payment_type=PaymentType.COD, txn="TC")


SETTLEMENT = _csv(
    "Gateway Transaction ID,OMS Order ID,Gross Amount,Gateway Charges,GST Amount,Refund Amount,"
    "Net Settlement Amount,Settlement Reference,Settlement Date,Status",
    "T1,,1000,20,3.60,0,976.40,U1,22-11-2026,SUCCESS",  # matches by transaction id, equals expected
    ",P2,1000,20,3.60,0,976.40,U2,22-11-2026,SUCCESS",  # matches by order id, no bank credit
    "T3,,1000,20,3.60,0,900,U3,22-11-2026,SUCCESS",  # short
    "T4,,1000,0,0,0,1100,U4,22-11-2026,SUCCESS",  # excess
    "T8,,1000,20,3.60,400,576.40,U5,23-11-2026,SUCCESS",  # partial refund, equals expected
    "T-UNKNOWN,,300,0,0,0,300,U9,23-11-2026,SUCCESS",  # no such order -> unmatched
)
BANK = _csv(
    "Credit Date,Narration,Credit Amount,Reference",
    "24-11-2026,Cashfree,976.40,U1",
    "24-11-2026,Other,555,X",
)


async def _upload(client, path, name, content, **form):
    return await client.post(
        f"{BASE}/{path}", data=form, files={"file": (name, io.BytesIO(content), "text/csv")}
    )


async def _setup(db, client):
    await _seed(db)
    up = await _upload(client, "prepaid/uploads", "s.csv", SETTLEMENT, gateway="Cashfree")
    assert up.status_code == 201, up.text
    bank = await _upload(client, "bank-statements", "b.csv", BANK, source_name="HDFC")
    assert bank.status_code == 201, bank.text
    return up.json()["data"], bank.json()["data"]


def _dp():
    return {
        "date_from": (NOW - timedelta(days=60)).isoformat(),
        "date_to": (NOW + timedelta(days=1)).isoformat(),
    }


async def _summary(client, **extra):
    r = await client.get(f"{BASE}/prepaid/summary", params={**_dp(), **extra})
    assert r.status_code == 200, r.text
    return r.json()["data"]


async def _records(client, **extra):
    out, page = [], 1
    while True:
        r = await client.get(
            f"{BASE}/prepaid/records", params={**_dp(), "page": page, "page_size": 200, **extra}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        out += body["data"]
        if page >= body["meta"]["total_pages"]:
            return out, body["meta"]["total_items"]
        page += 1


def D(x):
    return Decimal(str(x))


async def test_upload_matches_by_transaction_id_and_order_id_and_keeps_unmatched(db_session):
    user = await _user(db_session, "p1@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        up, bank = await _setup(db_session, client)
    assert up["rows_read"] == 6 and up["accepted"] == 6
    assert up["auto_matched_to_orders"] == 5 and up["unmatched"] == 1
    assert bank["accepted"] == 2 and bank["auto_linked_prepaid"] == 1


async def test_statuses_follow_evidence_not_payment_status(db_session):
    user = await _user(db_session, "p2@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup(db_session, client)
        rows, _ = await _records(client)
    st = {r["order_number"]: r["status"] for r in rows}
    assert st == {
        "P1": "matched",  # equals expected AND bank credit linked
        "P2": "partially_reconciled",  # equals expected, no bank evidence
        "P3": "short",
        "P4": "excess",
        "P5": "pending",  # paid but never settled: NOT assumed settled
        "P6": "failed",
        "P7": "refunded",
        "P8": "partially_reconciled",
        "P9": "pending",  # payment not captured
    }
    assert "COD1" not in st


async def test_amounts_charges_gst_refund_expected_net_and_difference(db_session):
    user = await _user(db_session, "p3@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup(db_session, client)
        rows, _ = await _records(client)
    by = {r["order_number"]: r for r in rows}
    p1 = by["P1"]
    assert D(p1["gross_amount"]) == D("1000.00")
    assert D(p1["gateway_charges"]) == D("20.00") and D(p1["gst_amount"]) == D("3.60")
    assert D(p1["expected_net"]) == D("976.40") and D(p1["actual_settlement"]) == D("976.40")
    assert D(p1["bank_confirmed_settlement"]) == D("976.40") and D(p1["difference"]) == 0
    assert p1["bank_reference"] == "U1" and p1["bank_credit_date"] is not None
    assert p1["gateway"] == "Cashfree"
    assert D(by["P3"]["difference"]) == D("-76.40")
    assert D(by["P4"]["difference"]) == D("100.00")
    p8 = by["P8"]
    assert D(p8["refund_amount"]) == D("400.00") and D(p8["expected_net"]) == D("576.40")
    assert D(by["P7"]["refund_amount"]) == D("1000.00")
    assert D(by["P5"]["expected_net"]) == D("1000.00")  # no charges are guessed
    assert D(by["P6"]["gross_amount"]) == 0 and D(by["P9"]["gross_amount"]) == 0


async def test_summary_kpis(db_session):
    user = await _user(db_session, "p4@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup(db_session, client)
        s = await _summary(client)
    k = s["kpis"]
    assert k["total_prepaid_orders"]["count"] == 9
    assert k["gross_collection"]["count"] == 7 and D(k["gross_collection"]["amount"]) == D(
        "7000.00"
    )
    assert k["gateway_charges"]["count"] == 4 and D(k["gateway_charges"]["amount"]) == D("80.00")
    assert D(k["gst"]["amount"]) == D("14.40")
    assert k["refunds"]["count"] == 2 and D(k["refunds"]["amount"]) == D("1400.00")
    assert D(k["expected_net"]["amount"]) == D("5505.60")
    assert D(k["reported_settlement"]["amount"]) == D("4529.20")
    assert k["bank_settlement"]["count"] == 1 and D(k["bank_settlement"]["amount"]) == D("976.40")
    # Expected net - settlement received (gateway-reported): P3, P4 and P5.
    assert D(k["settlement_difference"]["amount"]) == D("976.40")
    assert k["settlement_difference"]["count"] == 3
    assert k["unmatched"]["count"] == 1 and D(k["unmatched"]["amount"]) == D("300.00")
    assert k["unmatched_bank_credits"]["count"] == 1
    assert D(k["unmatched_bank_credits"]["amount"]) == D("555.00")
    assert k["reconciled"]["count"] == 1
    assert k["pending"]["count"] == 1  # P5 (P9 was never captured, so it is not "paid")
    assert k["failed"]["count"] == 1 and k["refunded"]["count"] == 1
    assert k["short"]["count"] == 1 and k["excess"]["count"] == 1


async def test_every_kpi_drilldown_equals_its_count(db_session):
    user = await _user(db_session, "p5@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup(db_session, client)
        s = await _summary(client)
        k = s["kpis"]
        expectations = {
            "orders": k["total_prepaid_orders"]["count"],
            "gross": k["gross_collection"]["count"],
            "charges": k["gateway_charges"]["count"],
            "gst": k["gst"]["count"],
            "refunds": k["refunds"]["count"],
            "expected_net": k["expected_net"]["count"],
            "reported_settlement": k["reported_settlement"]["count"],
            "bank_settlement": k["bank_settlement"]["count"],
            "difference": k["settlement_difference"]["count"],
            "reconciled": k["reconciled"]["count"],
            "short": k["short"]["count"],
            "excess": k["excess"]["count"],
            "failed": k["failed"]["count"],
            "refunded": k["refunded"]["count"],
        }
        for kpi, count in expectations.items():
            rows, total = await _records(client, kpi=kpi)
            assert total == count == len(rows), kpi
            assert len({r["order_id"] for r in rows}) == len(rows)
        # The "Settlement Pending" drilldown lists exactly what its card counts
        # (paid, nothing settled); the status filter still lists unpaid ones too.
        pend, total = await _records(client, kpi="pending")
        assert total == k["pending"]["count"] == 1
        by_filter, filtered = await _records(client, status="pending")
        assert filtered == 2  # P5 + the unpaid P9
        assert sum(b["count"] for b in s["status_breakdown"]) == k["total_prepaid_orders"]["count"]
        for kind, key in (
            ("gateway_line", "unmatched"),
            ("bank_credit", "unmatched_bank_credits"),
        ):
            un = await client.get(f"{BASE}/prepaid/unmatched", params={**_dp(), "kind": kind})
            assert un.json()["meta"]["total_items"] == k[key]["count"], key


async def test_percentages_label_numerator_denominator_and_kind(db_session):
    user = await _user(db_session, "p6@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup(db_session, client)
        s = await _summary(client)
    p = {i["key"]: i for i in s["percentages"]}
    assert p["completion"]["kind"] == "count"
    assert D(p["completion"]["numerator"]) == 1 and D(p["completion"]["denominator"]) == 7
    assert p["completion"]["value"] == pytest.approx(100 / 7)
    assert p["pending"]["value"] == pytest.approx(100 / 7)
    assert p["completion_value"]["kind"] == "value"
    assert p["charges"]["value"] == pytest.approx(80 / 7000 * 100)
    assert p["refunds"]["value"] == pytest.approx(1400 / 7000 * 100)
    assert p["matched_tx"]["value"] == pytest.approx(5 / 6 * 100)
    assert p["unmatched_tx"]["value"] == pytest.approx(1 / 6 * 100)
    assert p["difference"]["value"] == pytest.approx(976.4 / 5505.6 * 100)
    assert "paid" in p["completion"]["denominator_label"]


async def test_zero_denominators_are_none(db_session):
    user = await _user(db_session, "p7@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        s = await _summary(client)
    assert s["kpis"]["total_prepaid_orders"]["count"] == 0
    assert all(i["value"] is None for i in s["percentages"])


async def test_duplicate_file_and_lines_are_not_imported_twice(db_session):
    user = await _user(db_session, "p8@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup(db_session, client)
        assert (
            await _upload(client, "prepaid/uploads", "s.csv", SETTLEMENT, gateway="Cashfree")
        ).status_code == 409
        overlap = _csv(
            "Gateway Transaction ID,Gross Amount,Net Settlement Amount,Settlement Reference,Status",
            "T3,1000,900,U3,SUCCESS",  # identical to a line already imported -> duplicate
            "T5,1000,980,U6,SUCCESS",  # new line
        )
        res = await _upload(client, "prepaid/uploads", "o.csv", overlap, gateway="Cashfree")
        assert res.status_code == 201
        data = res.json()["data"]
        assert data["accepted"] == 1 and data["duplicates_skipped"] == 1
        rows, _ = await _records(client, q="P5")
        assert D(rows[0]["actual_settlement"]) == D("980.00")
        again = _csv(
            "Gateway Transaction ID,Gross Amount,Net Settlement Amount,Settlement Reference,Status",
            "T5,1000,980,U6,SUCCESS",
        )
        res = await _upload(client, "prepaid/uploads", "o2.csv", again, gateway="Cashfree")
        assert res.json()["data"]["duplicates_skipped"] == 1 and res.json()["data"]["accepted"] == 0


async def test_bank_credit_partial_then_full_and_manual_link(db_session):
    user = await _user(db_session, "p9@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup(db_session, client)
        part = _csv("Credit Date,Narration,Credit Amount,Reference", "25-11-2026,part,400,U2")
        await _upload(client, "bank-statements", "p.csv", part, source_name="HDFC")
        rows, _ = await _records(client, q="P2")
        assert rows[0]["status"] == "partially_reconciled"
        assert D(rows[0]["bank_confirmed_settlement"]) == D("400.00")  # the actual credit
        assert D(rows[0]["bank_remaining"]) == D("576.40")
        assert rows[0]["bank_status"] == "partially_confirmed"
        rest = _csv("Credit Date,Narration,Credit Amount,Reference", "26-11-2026,rest,576.40,U2")
        await _upload(client, "bank-statements", "q.csv", rest, source_name="HDFC")
        rows, _ = await _records(client, q="P2")
        assert rows[0]["status"] == "matched"
        # Manual: the unrelated 555 credit can be linked to settlement U3 (partial -> still short).
        credits = (await client.get(f"{BASE}/prepaid/bank-credits", params=_dp())).json()["data"]
        x = next(c for c in credits if c["reference"] == "X")
        evidence = {"bank_reference": "X", "reason": "Checked against the statement"}
        ok = await client.post(
            f"{BASE}/prepaid/bank-links",
            json={"bank_credit_id": x["id"], "reference": "U3", "amount": "555", **evidence},
        )
        assert ok.status_code == 201, ok.text
        assert (
            await client.post(
                f"{BASE}/prepaid/bank-links",
                json={"bank_credit_id": x["id"], "reference": "NOPE", "amount": "1", **evidence},
            )
        ).status_code == 422


async def test_adjustment_requires_reason_and_is_audited(db_session):
    user = await _user(db_session, "p10@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup(db_session, client)
        rows, _ = await _records(client, q="P3")
        oid = rows[0]["order_id"]
        assert (
            await client.post(
                f"{BASE}/prepaid/adjustments",
                json={"order_id": oid, "amount": "76.40", "reason": "  "},
            )
        ).status_code == 422
        res = await client.post(
            f"{BASE}/prepaid/adjustments",
            json={"order_id": oid, "amount": "76.40", "reason": "Gateway fee waiver"},
        )
        assert res.status_code == 201
        after, _ = await _records(client, q="P3")
        assert D(after[0]["difference"]) == 0
        assert (
            await client.post(f"{BASE}/prepaid/adjustments/{res.json()['data']['id']}/reverse")
        ).status_code == 200
    actions = set((await db_session.execute(select(AuditLog.action))).scalars().all())
    assert {
        "remittance.prepaid.adjustment",
        "remittance.prepaid.adjustment_reversed",
        "remittance.prepaid.upload",
    } <= actions


async def test_manual_match_only_to_prepaid_orders(db_session):
    user = await _user(db_session, "p11@example.com")
    cod = await _order(db_session, "COD2", payment_type=PaymentType.COD)
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup(db_session, client)
        stray = (
            await client.get(f"{BASE}/prepaid/unmatched", params={**_dp(), "kind": "gateway_line"})
        ).json()["data"][0]
        assert (
            await client.post(
                f"{BASE}/prepaid/lines/{stray['id']}/match", json={"order_id": str(cod.id)}
            )
        ).status_code == 422
        p5, _ = await _records(client, q="P5")
        ok = await client.post(
            f"{BASE}/prepaid/lines/{stray['id']}/match", json={"order_id": p5[0]["order_id"]}
        )
        assert ok.status_code == 200
        p5, _ = await _records(client, q="P5")
        assert D(p5[0]["actual_settlement"]) == D("300.00") and p5[0]["status"] == "short"
        assert (await client.post(f"{BASE}/prepaid/lines/{stray['id']}/unmatch")).status_code == 200


async def test_gateway_and_date_filters_are_consistent(db_session):
    user = await _user(db_session, "p12@example.com")
    await _order(db_session, "RZ1", txn="RZT", provider="razorpay")
    await _order(db_session, "OLD", txn="OT", days_ago=100)
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup(db_session, client)
        all_rows, total = await _records(client)
        assert total == 10 and "OLD" not in {r["order_number"] for r in all_rows}
        cf, n_cf = await _records(client, gateway="cashfree")
        rz, n_rz = await _records(client, gateway="Razorpay")
        assert n_rz == 1 and rz[0]["gateway"] == "Razorpay"
        assert n_cf == 9  # P1..P9
        s = await _summary(client, gateway="Razorpay")
        assert s["kpis"]["total_prepaid_orders"]["count"] == 1
        gws = (await client.get(f"{BASE}/prepaid/reports/gateways", params=_dp())).json()["data"]
        assert {g["gateway"] for g in gws} >= {"Cashfree", "Razorpay"}
        wide = await _summary(client, date_from=(NOW - timedelta(days=200)).isoformat())
        assert wide["kpis"]["total_prepaid_orders"]["count"] == 11
        status_rows, st_total = await _records(client, status="failed")
        assert st_total == 1 and status_rows[0]["order_number"] == "P6"
        page1 = await client.get(
            f"{BASE}/prepaid/records", params={**_dp(), "page_size": 3, "page": 1}
        )
        page2 = await client.get(
            f"{BASE}/prepaid/records", params={**_dp(), "page_size": 3, "page": 2}
        )
        assert not {r["order_id"] for r in page1.json()["data"]} & {
            r["order_id"] for r in page2.json()["data"]
        }


async def test_reports_and_cashfree_lots(db_session):
    from app.models.cashfree_settlement import CashfreeSettlement

    user = await _user(db_session, "p13@example.com")
    db_session.add(
        CashfreeSettlement(
            cf_settlement_id="S1",
            status="SUCCESS",
            settlement_utr="U1",
            settlement_processed_on=NOW - timedelta(days=1),
            payment_amount=Decimal("1000"),
            amount_settled=Decimal("976.40"),
        )
    )
    await db_session.commit()
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup(db_session, client)
        dates = (await client.get(f"{BASE}/prepaid/reports/settlement-dates", params=_dp())).json()[
            "data"
        ]
        ageing = (await client.get(f"{BASE}/prepaid/reports/ageing", params=_dp())).json()["data"]
        lots = (await client.get(f"{BASE}/prepaid/settlement-lots", params=_dp())).json()
    assert sum(d["lines"] for d in dates) == 6 and sum(d["unmatched_lines"] for d in dates) == 1
    bucket = next(b for b in ageing if b["bucket"] == "0-7 days")
    assert bucket["pending_orders"] == 1 and bucket["awaiting_bank_orders"] == 2  # P2, P8
    assert lots["data"][0]["utr"] == "U1" and lots["data"][0]["bank_state"] == "linked"


async def test_rbac_and_excel_export(db_session):
    reader = await _user(db_session, "pr@example.com", perms=["reconciliation.read"])
    nobody = await _user(db_session, "pn@example.com", perms=["orders.read"])
    async with bearer_client(app, get_db, db_session, reader.id) as client:
        assert (
            await _upload(client, "prepaid/uploads", "s.csv", SETTLEMENT, gateway="Cashfree")
        ).status_code == 403
        assert (await client.post(f"{BASE}/prepaid/rematch")).status_code == 403
        assert (await client.get(f"{BASE}/prepaid/summary", params=_dp())).status_code == 200
    async with bearer_client(app, get_db, db_session, nobody.id) as client:
        for path in ("prepaid/summary", "prepaid/records", "prepaid/export", "prepaid/unmatched"):
            assert (await client.get(f"{BASE}/{path}")).status_code == 403, path
    admin = await _user(db_session, "pa@example.com")
    async with bearer_client(app, get_db, db_session, admin.id) as client:
        await _setup(db_session, client)
        res = await client.get(f"{BASE}/prepaid/export", params=_dp())
    wb = load_workbook(io.BytesIO(res.content))
    assert wb.sheetnames == [
        "Order-wise",
        "Gateway-wise",
        "Settlement-date-wise",
        "Settlement ageing",
        "Failed transactions",
        "Refunded transactions",
        "Unmatched",
        "Bank & adjustments",
        "Definitions",
    ]
    rows = list(wb["Order-wise"].iter_rows(values_only=True))
    assert len(rows) - 1 == 9 and rows[0][5] == "Gross amount"
    assert len(list(wb["Failed transactions"].iter_rows(values_only=True))) - 1 == 1
    assert len(list(wb["Unmatched"].iter_rows(values_only=True))) - 1 == 2


async def test_ageing_and_settlement_date_rows_drill_to_their_orders(db_session):
    user = await _user(db_session, "p14@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup(db_session, client)
        ageing = (await client.get(f"{BASE}/prepaid/reports/ageing", params=_dp())).json()["data"]
        for bucket in ageing:
            expected = bucket["pending_orders"] + bucket["awaiting_bank_orders"]
            rows, total = await _records(client, age_bucket=bucket["bucket"])
            assert total == expected == len(rows), bucket["bucket"]
        dates = (await client.get(f"{BASE}/prepaid/reports/settlement-dates", params=_dp())).json()[
            "data"
        ]
        for row in dates:
            rows, _ = await _records(client, settled_on=row["date"])
            assert len(rows) <= row["lines"] - row["unmatched_lines"]
            assert (len(rows) > 0) == (row["lines"] - row["unmatched_lines"] > 0)


async def test_status_filter_narrows_cards_percentages_and_drilldown_alike(db_session):
    user = await _user(db_session, "p15@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup(db_session, client)
        s = await _summary(client, status="short")
        rows, total = await _records(client, status="short")
    assert s["kpis"]["total_prepaid_orders"]["count"] == total == 1
    assert s["kpis"]["short"]["count"] == 1 and s["kpis"]["reconciled"]["count"] == 0
    assert sum(b["count"] for b in s["status_breakdown"]) == 1


async def test_gateway_filter_never_hides_unlinked_bank_credits(db_session):
    user = await _user(db_session, "p16@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup(db_session, client)
        s = await _summary(client, gateway="Cashfree")
        un = await client.get(f"{BASE}/prepaid/unmatched", params={**_dp(), "gateway": "Cashfree"})
    assert s["kpis"]["unmatched_bank_credits"]["count"] == 1
    assert s["kpis"]["unmatched"]["count"] == 1
    assert un.json()["meta"]["total_items"] == 2  # the listing still shows both
