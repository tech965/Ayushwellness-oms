"""Money rules of Modules 6 & 7: success-status allowlist, partial bank
credits, no double allocation, ADMIN-only manual verification and the effect
of manual adjustments. Synthetic data only.
"""

from __future__ import annotations

import io
import random
from decimal import Decimal

import pytest
from app.main import app
from app.models.audit_log import AuditLog
from app.models.cod_remittance import CodRemittance
from app.models.remittance_reconciliation import BankCredit, BankCreditLink
from app.services.cashfree_gateway_sync_service import derive_line_status
from app.services.prepaid_remittance_recon_service import prepaid_status
from app.services.remittance_common import (
    SUCCESS_LINE_STATES,
    BankEvidence,
    allocate_to_lines,
    load_ledger,
    prorate,
    verification_of,
)
from openpyxl import load_workbook
from sqlalchemy import select

from tests import test_prepaid_remittance_reconciliation as prepaid_data
from tests.telecalling_test_utils import make_role, make_user
from tests.test_remittance_drilldowns import BASE, _admin, _client, _walk
from tests.test_remittance_manual_match_and_uploads import _dp, _order

pytestmark = pytest.mark.asyncio

D = Decimal
VERIFY = "/api/v1/revenue-reconciliation/remittances"


@pytest.fixture(autouse=True)
def _clear_override():
    yield
    app.dependency_overrides.clear()


def _csv(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode()


async def _upload(client, path, name, content, **form):
    res = await client.post(
        f"{BASE}/{path}", data=form, files={"file": (name, io.BytesIO(content), "text/csv")}
    )
    assert res.status_code == 201, res.text
    return res.json()["data"]


async def _cod_report(client, name, *rows):
    return await _upload(
        client,
        "cod/uploads",
        name,
        _csv("AWB,Remittance Reference,Remittance Amount", *rows),
        partner="Delhivery",
    )


async def _bank(client, name, *rows):
    return await _upload(
        client,
        "bank-statements",
        name,
        _csv("Credit Date,Narration,Credit Amount,Reference", *rows),
        source_name="HDFC",
    )


async def _kpis(client, module="cod", params=None):
    res = await client.get(f"{BASE}/{module}/summary", params=params or _dp())
    assert res.status_code == 200, res.text
    return res.json()["data"]["kpis"]


async def _rows(client, kpi="expected", module="cod"):
    rows, totals, _ = await _walk(client, module, kpi, _dp(), page_size=100)
    return {r["order_number"]: r for r in rows}, totals


async def _credits(client, module="cod"):
    res = await client.get(f"{BASE}/{module}/bank-credits", params={**_dp(), "page_size": 100})
    return {c["reference"]: c for c in res.json()["data"]}


async def _assert_never_over_allocated(db):
    ledger = await load_ledger(db)
    for credit in ledger.credits:
        stored = sum(
            (
                D(str(link.amount))
                for link in (
                    await db.execute(
                        select(BankCreditLink).where(
                            BankCreditLink.bank_credit_id == credit.id,
                            BankCreditLink.reversed_at.is_(None),
                        )
                    )
                ).scalars()
            ),
            D(0),
        )
        used = stored + ledger.claimed.get(credit.id, D(0))
        assert used <= D(str(credit.amount)), (credit.reference, used, credit.amount)


def _link(credit, reference, value, **over):
    return {
        "bank_credit_id": credit["id"],
        "reference": reference,
        "bank_reference": "HDFC-UTR-1",
        "amount": value,
        "reason": "Verified on the bank portal",
        **over,
    }


# --- allocation arithmetic -------------------------------------------------------


async def test_prorate_is_exact_and_bounded_for_random_inputs():
    rng = random.Random(7)
    for _ in range(500):
        weights = [D(rng.randint(1, 10**6)) / 100 for _ in range(rng.randint(1, 12))]
        total = sum(weights)
        target = D(rng.randint(0, int(total * 100))) / 100
        shares = prorate(weights, target)
        assert sum(shares) == target
        assert all(0 <= s <= w for s, w in zip(shares, weights, strict=True))


async def test_allocation_never_exceeds_the_credit_or_the_reported_total():
    lines = [("a", "U1", D("600")), ("b", "U1", D("400")), ("c", "U1", D("-100"))]
    for credited, manual in ((D("450"), D("0")), (D("900"), D("300")), (D("5000"), D("5000"))):
        out = allocate_to_lines(lines, BankEvidence({"u1": credited}, {}, {"u1": manual}))
        applied = sum(x.confirmed for x in out.values())
        assert applied == min(credited, D("900"))
        assert sum(x.manual for x in out.values()) <= min(manual, applied)
        assert all(x.covered is (credited >= D("899")) for x in out.values())
    nothing = allocate_to_lines(lines, BankEvidence({}, {}))
    assert all(x.confirmed == 0 and not x.covered for x in nothing.values())


# --- partial credits ------------------------------------------------------------


async def _two_order_batch(db, client):
    await _order(db, "B-1", "600.00", awb="AWB-B1")
    await _order(db, "B-2", "400.00", awb="AWB-B2")
    await _cod_report(client, "r.csv", "AWB-B1,RB,600", "AWB-B2,RB,400")


async def test_partial_credit_is_split_exactly_and_nothing_is_matched(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        await _two_order_batch(db_session, client)
        await _bank(client, "b.csv", "20-11-2026,part,500,RB")
        rows, _ = await _rows(client)
        assert {n: D(r["amount_received"]) for n, r in rows.items()} == {
            "B-1": D("300.00"),
            "B-2": D("200.00"),
        }
        assert {r["bank_status_label"] for r in rows.values()} == {"Partially Confirmed"}
        assert {r["status"] for r in rows.values()} == {"partially_reconciled"}
        k = await _kpis(client)
        position = k["bank_position"]
        assert D(position["expected"]) == 1000 and D(position["amount"]) == 500
        assert D(position["remaining"]) == 500 and D(k["bank_confirmed"]["amount"]) == 500
        assert k["partially_confirmed"]["count"] == 2
        for kpi in ("partially_confirmed", "bank_remaining"):
            _, totals = await _rows(client, kpi)
            assert D(totals["amount"]) == D(k[kpi]["amount"]) == 500
        detail = (await client.get(f"{BASE}/cod/orders/{rows['B-1']['order_id']}/detail")).json()[
            "data"
        ]
        (reference,) = detail["bank_references"]
        assert D(reference["bank_expected"]) == 1000 and D(reference["bank_remaining"]) == 500
        assert reference["bank_status_label"] == "Partially Confirmed"

        # The rest arrives: the automatic link is made up to the full amount.
        await _bank(client, "c.csv", "21-11-2026,rest,500,RB")
        rows, _ = await _rows(client)
        assert {r["status"] for r in rows.values()} == {"matched"}
        assert {r["bank_status"] for r in rows.values()} == {"confirmed"}
        assert (await _kpis(client))["partially_confirmed"]["count"] == 0
        await _assert_never_over_allocated(db_session)


async def test_short_batch_never_matches_even_when_each_share_is_within_tolerance(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        for i in range(10):
            await _order(db_session, f"T-{i}", "100.00", awb=f"AWB-T{i}")
        await _cod_report(client, "r.csv", *(f"AWB-T{i},RT,100" for i in range(10)))
        await _bank(client, "b.csv", "20-11-2026,short by 5,995,RT")  # 0.50 per order
        rows, _ = await _rows(client)
        assert {r["status"] for r in rows.values()} == {"partially_reconciled"}
        assert {r["bank_status"] for r in rows.values()} == {"partially_confirmed"}
        k = await _kpis(client)
    assert D(k["bank_confirmed"]["amount"]) == D("995.00") and k["reconciled"]["count"] == 0


async def test_rounding_shortfall_confirms_only_what_was_credited(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        await _order(db_session, "R-1", "1000.00", awb="AWB-R1")
        await _cod_report(client, "r.csv", "AWB-R1,RR,1000")
        await _bank(client, "b.csv", "20-11-2026,rounding,999.40,RR")
        rows, _ = await _rows(client)
    assert rows["R-1"]["status"] == "matched"
    assert D(rows["R-1"]["amount_received"]) == D("999.40")  # not rounded up to 1000


# --- no duplicate / excess allocation -------------------------------------------


async def test_auto_link_takes_only_what_the_reference_needs(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        await _two_order_batch(db_session, client)
        await _bank(client, "b.csv", "20-11-2026,big,1500,RB")
        k = await _kpis(client)
        assert D(k["bank_confirmed"]["amount"]) == 1000
        assert D(k["unmatched_bank_credits"]["amount"]) == 500  # stays visible
        (credit,) = (await _credits(client)).values()
        assert D(credit["linked_amount"]) == 1000 and D(credit["unlinked_amount"]) == 500
        again = await client.post(f"{BASE}/cod/rematch")
        assert again.json()["data"]["bank_links"] == 0  # idempotent
        extra = await client.post(f"{BASE}/cod/bank-links", json=_link(credit, "RB", "1"))
        assert extra.status_code == 422
        await _assert_never_over_allocated(db_session)


async def test_one_credit_is_never_spent_in_both_modules(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        await _two_order_batch(db_session, client)  # COD reference RB = 1000
        await _upload(
            client,
            "prepaid/uploads",
            "p.csv",
            _csv(
                "OMS Order ID,Net Settlement Amount,Settlement Reference,Status",
                "NOPE,1000,RB,SUCCESS",
            ),
            gateway="Razorpay",
        )
        await _bank(client, "b.csv", "20-11-2026,one,1000,RB")
        await client.post(f"{BASE}/cod/rematch")
        await client.post(f"{BASE}/prepaid/rematch")
        (credit,) = (await _credits(client, "prepaid")).values()
        assert D(credit["unlinked_amount"]) == 0 and credit["link_state"] == "linked"
        more = await client.post(f"{BASE}/prepaid/bank-links", json=_link(credit, "RB", "0.01"))
        assert more.status_code == 422
        await _assert_never_over_allocated(db_session)


# --- manual verification ---------------------------------------------------------


async def _manual_setup(db, client):
    await _two_order_batch(db, client)
    await _bank(client, "b.csv", "20-11-2026,other ref,700,BANKREF-9")
    return (await _credits(client))["BANKREF-9"]


@pytest.mark.parametrize(
    "over",
    [
        {"reason": None},
        {"reason": "  "},
        {"reason": "ab"},
        {"bank_reference": None},
        {"bank_reference": "   "},
        {"amount": None},
        {"amount": "0"},
        {"amount": "-5"},
        {"amount": "700.50"},  # no tolerance above what is free on the credit
        {"amount": "0.001"},
    ],
)
async def test_manual_link_requires_reason_bank_reference_and_a_valid_amount(db_session, over):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        credit = await _manual_setup(db_session, client)
        body = {k: v for k, v in _link(credit, "RB", "700", **over).items() if v is not None}
        res = await client.post(f"{BASE}/cod/bank-links", json=body)
        assert res.status_code == 422, res.text
        assert D((await _kpis(client))["bank_confirmed"]["amount"]) == 0


async def test_manual_link_is_labelled_audited_and_capped(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        credit = await _manual_setup(db_session, client)
        await _bank(client, "c.csv", "21-11-2026,same ref,200,RB")  # statement-matched
        ok = await client.post(f"{BASE}/cod/bank-links", json=_link(credit, "RB", "700"))
        assert ok.status_code == 201, ok.text
        k = await _kpis(client)
        assert D(k["bank_confirmed"]["amount"]) == 900
        assert D(k["bank_confirmed"]["statement_matched"]) == 200
        assert D(k["bank_confirmed"]["manually_verified"]) == 700
        rows, _ = await _rows(client)
        row = rows["B-1"]
        assert D(row["bank_statement_amount"]) + D(row["bank_manual_amount"]) == D(
            row["amount_received"]
        )
        assert row["bank_evidence"] == "Statement-matched + Manually verified"
        detail = (await client.get(f"{BASE}/cod/orders/{row['order_id']}/detail")).json()["data"]
        by_kind = {x["verification"]: x for x in detail["bank_links"]}
        manual = by_kind["manually_verified"]
        assert manual["bank_reference"] == "HDFC-UTR-1"
        assert manual["reason"] == "Verified on the bank portal"
        assert by_kind["statement_matched"]["reason"] is None
        tab = await _credits(client)
        assert D(tab["BANKREF-9"]["manually_verified_amount"]) == 700
        assert D(tab["RB"]["statement_matched_amount"]) == 200
        export = await client.get(f"{BASE}/cod/export", params=_dp())
        sheet = list(load_workbook(io.BytesIO(export.content))["Order-wise"].values)
        for header, total in (("Statement-matched", 200), ("Manually verified", 700)):
            column = sheet[0].index(header)
            assert sum(D(str(r[column])) for r in sheet[1:]) == total, header
        await _assert_never_over_allocated(db_session)
    log = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "remittance.cod.bank_link")
        )
    ).scalar_one()
    assert log.user_id == user.id
    assert log.new_value["reason"] == "Verified on the bank portal"
    assert log.new_value["bank_reference"] == "HDFC-UTR-1" and log.new_value["amount"] == "700.00"
    assert log.new_value["verification"] == "manually_verified"


async def test_manual_link_needs_the_adjust_permission(db_session):
    admin = await _admin(db_session)
    async with _client(db_session, admin) as client:
        credit = await _manual_setup(db_session, client)
    role = await make_role(
        db_session,
        name="NOT-ADMIN",
        permission_codes=["reconciliation.read", "reconciliation.upload", "reconciliation.manage"],
    )
    user = await make_user(db_session, email="ops@example.com", role=role)
    async with _client(db_session, user) as client:
        res = await client.post(f"{BASE}/cod/bank-links", json=_link(credit, "RB", "700"))
    assert res.status_code == 403


async def _shiprocket(db, number="S-1", awb="AWB-S1", remitted="500.00", ref="SR-UTR"):
    await _order(db, number, remitted, awb=awb)
    db.add(CodRemittance(awb=awb, remitted_amount=D(remitted), remittance_reference=ref))
    await db.commit()


def _verify_body(**over):
    return {
        "amount": "500",
        "reference": "SR-UTR",
        "credited_on": "2026-11-20T00:00:00Z",
        "reason": "Seen on the bank statement",
        **over,
    }


async def test_shiprocket_verification_is_admin_only_with_mandatory_evidence(db_session):
    await _shiprocket(db_session)
    ops_role = await make_role(
        db_session,
        name="OPS",
        permission_codes=["reconciliation.read", "reconciliation.manage"],
    )
    ops = await make_user(db_session, email="ops@example.com", role=ops_role)
    async with _client(db_session, ops) as client:
        res = await client.post(f"{VERIFY}/AWB-S1/verify-bank-credit", json=_verify_body())
        assert res.status_code == 403
    admin = await _admin(db_session)
    async with _client(db_session, admin) as client:
        for field in ("amount", "reference", "reason"):
            body = _verify_body()
            del body[field]
            res = await client.post(f"{VERIFY}/AWB-S1/verify-bank-credit", json=body)
            assert res.status_code == 422, field
        for bad in ({"reason": "   "}, {"reference": "  "}, {"amount": "500.01"}):
            res = await client.post(f"{VERIFY}/AWB-S1/verify-bank-credit", json=_verify_body(**bad))
            assert res.status_code == 422, bad
        ok = await client.post(
            f"{VERIFY}/AWB-S1/verify-bank-credit", json=_verify_body(amount="300")
        )
        assert ok.status_code == 200, ok.text
        rows, _ = await _rows(client)
        row = rows["S-1"]
        assert D(row["amount_received"]) == 300 and D(row["bank_remaining"]) == 200
        assert D(row["bank_manual_amount"]) == 300 and D(row["bank_statement_amount"]) == 0
        assert row["bank_status_label"] == "Partially Confirmed"
        assert row["status"] == "partially_reconciled"
        detail = (await client.get(f"{BASE}/cod/orders/{row['order_id']}/detail")).json()["data"]
        (evidence,) = detail["bank_links"]
        assert evidence["verification_label"] == "Manually verified"
        assert evidence["reason"] == "Seen on the bank statement"
        assert evidence["bank_reference"] == "SR-UTR"
    log = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "remittance.cod.bank_verification")
        )
    ).scalar_one()
    assert log.user_id == admin.id and log.new_value["amount"] == "300"
    assert log.new_value["reason"] == "Seen on the bank statement"


async def test_default_operations_role_cannot_verify_bank_credits():
    from scripts.seed import ROLE_PERMISSIONS

    holders = {
        role
        for role, perms in ROLE_PERMISSIONS.items()
        if perms == "*" or "reconciliation.adjust" in perms
    }
    assert holders == {"ADMIN"}


async def test_statement_and_manual_verification_never_count_the_same_money_twice(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        await _shiprocket(db_session)  # manually verified 500 under SR-UTR
        await _order(db_session, "U-1", "500.00", awb="AWB-U1")
        ok = await client.post(f"{VERIFY}/AWB-S1/verify-bank-credit", json=_verify_body())
        assert ok.status_code == 200, ok.text
        # An uploaded report uses the same UTR, then ONE statement credit of 500 arrives.
        await _cod_report(client, "r.csv", "AWB-U1,SR-UTR,500")
        await _bank(client, "b.csv", "20-11-2026,single,500,SR-UTR")
        await client.post(f"{BASE}/cod/rematch")
        k = await _kpis(client)
        assert D(k["bank_confirmed"]["amount"]) == 500  # not 1000
        assert D(k["bank_remaining"]["amount"]) == 500
        await _assert_never_over_allocated(db_session)


async def test_verification_cannot_exceed_a_statement_credit(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        await _shiprocket(db_session, "S-1", "AWB-S1")
        await _shiprocket(db_session, "S-2", "AWB-S2")
        await _bank(client, "b.csv", "20-11-2026,single,600,SR-UTR")
        first = await client.post(f"{VERIFY}/AWB-S1/verify-bank-credit", json=_verify_body())
        assert first.status_code == 200, first.text
        over = await client.post(f"{VERIFY}/AWB-S2/verify-bank-credit", json=_verify_body())
        assert over.status_code == 422 and "100.00" in over.text
        rest = await client.post(
            f"{VERIFY}/AWB-S2/verify-bank-credit", json=_verify_body(amount="100")
        )
        assert rest.status_code == 200, rest.text
        # Correcting the first verification does not count its own old amount against it.
        redo = await client.post(f"{VERIFY}/AWB-S1/verify-bank-credit", json=_verify_body())
        assert redo.status_code == 200, redo.text
        k = await _kpis(client)
        assert D(k["bank_confirmed"]["amount"]) == 600
        assert k["unmatched_bank_credits"]["count"] == 0
        await _assert_never_over_allocated(db_session)


async def test_earlier_verifications_larger_than_a_later_statement_are_capped(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        for number in (1, 2):
            await _shiprocket(db_session, f"S-{number}", f"AWB-S{number}")
            res = await client.post(
                f"{VERIFY}/AWB-S{number}/verify-bank-credit", json=_verify_body()
            )
            assert res.status_code == 200, res.text
        assert D((await _kpis(client))["bank_confirmed"]["amount"]) == 1000  # no statement yet
        await _bank(client, "b.csv", "20-11-2026,only,600,SR-UTR")
        k = await _kpis(client)
        assert D(k["bank_confirmed"]["amount"]) == 600  # the statement is the limit
        await _assert_never_over_allocated(db_session)


# --- success-status allowlist ---------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["SUCCESS", "Successful", " settled ", "PROCESSED", "Completed", "success"],
)
async def test_recognised_success_statuses(raw):
    assert verification_of(raw) == "successful"


@pytest.mark.parametrize(
    "raw",
    [None, "", "  ", "paid", "ok", "done", "yes", "succes", "not successful", "settlement"],
)
async def test_blank_unknown_and_unfamiliar_statuses_are_unverified(raw):
    assert verification_of(raw) == "unverified"


async def test_the_documented_list_is_the_enforced_list():
    from pathlib import Path

    doc = (Path(__file__).resolve().parents[3] / "docs" / "integrations" / "cashfree.md").read_text(
        encoding="utf-8"
    )
    table = doc.split("### Accepted successful settlement statuses")[1].split("###")[0]
    listed = {line.split("`")[1] for line in table.splitlines() if line.startswith("| `")}
    assert listed == set(SUCCESS_LINE_STATES)


async def test_sync_keeps_blank_and_unfamiliar_statuses_unverified():
    assert verification_of(derive_line_status("SUCCESS", "SUCCESS", True)) == "successful"
    assert verification_of(derive_line_status(None, "SUCCESS", True)) == "unverified"
    assert verification_of(derive_line_status("WEIRD", "SUCCESS", True)) == "unverified"
    assert verification_of(derive_line_status("SUCCESS", "SOMETHING_NEW", True)) == "unverified"
    assert derive_line_status("SUCCESS", "FAILED", True) == "failed"


async def test_unverified_lines_count_nothing_until_a_corrected_report_confirms_them(db_session):
    user = await _admin(db_session)
    header = "OMS Order ID,Gross Amount,Net Settlement Amount,Settlement Reference,Status"
    async with _client(db_session, user) as client:
        await prepaid_data._seed(db_session)
        first = await _upload(
            client,
            "prepaid/uploads",
            "a.csv",
            _csv(header, "P1,1000,1000,V1,", "P2,1000,1000,V2,Paid", "P3,1000,1000,V3,FAILED"),
            gateway="PayU",
        )
        assert (first["unverified_rows"], first["unsuccessful_rows"]) == (2, 1)
        await _bank(client, "b.csv", "20-11-2026,x,1000,V1")
        params = prepaid_data._dp()
        k = await _kpis(client, "prepaid", params)
        assert D(k["reported_settlement"]["amount"]) == 0
        assert D(k["bank_settlement"]["amount"]) == 0  # an unverified line earns no bank link
        assert D(k["unmatched_bank_credits"]["amount"]) == 1000
        assert k["unverified"]["count"] == 2 and D(k["unverified"]["amount"]) == 2000
        rows, totals, _ = await _walk(client, "prepaid", "unverified", params)
        assert D(totals["amount"]) == 2000
        assert {r["unverified_statuses"] for r in rows} == {"blank", "paid"}
        assert all(r["status"] == "pending" for r in rows)
        detail = (await client.get(f"{BASE}/prepaid/orders/{rows[0]['order_id']}/detail")).json()[
            "data"
        ]
        assert [x["verification_label"] for x in detail["lines"]] == ["Unverified"]
        assert detail["lines"][0]["counted"] is False

        # Corrected report: same rows, recognised statuses. Only the status changes.
        fixed = await _upload(
            client,
            "prepaid/uploads",
            "b.csv",
            _csv(
                header,
                "P1,1000,1000,V1,SUCCESS",
                "P2,1000,1000,V2,Settled",
                "P3,1000,1000,V3,SUCCESS",
            ),
            gateway="PayU",
        )
        assert fixed["accepted"] == 0 and fixed["statuses_updated"] == 2
        assert fixed["bank_credits_linked"] == 1
        k = await _kpis(client, "prepaid", params)
        assert D(k["reported_settlement"]["amount"]) == 2000  # the FAILED line stays failed
        assert D(k["bank_settlement"]["amount"]) == 1000
        assert k["unverified"]["count"] == 0
        # A later file can never downgrade or re-decide a recognised status.
        again = await _upload(
            client,
            "prepaid/uploads",
            "c.csv",
            _csv(header, "P1,1000,1000,V1,FAILED"),
            gateway="PayU",
        )
        assert again["statuses_updated"] == 0
        k = await _kpis(client, "prepaid", params)
        assert D(k["reported_settlement"]["amount"]) == 2000


async def test_other_gateways_are_included_with_a_gateway_breakdown(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        await prepaid_data._order(db_session, "RZ-1", txn="RZ-T", provider="razorpay")
        await prepaid_data._setup(db_session, client)
        await _upload(
            client,
            "prepaid/uploads",
            "rz.csv",
            _csv(
                "Gateway Transaction ID,Net Settlement Amount,Settlement Reference,Status",
                "RZ-T,990,RZU,SUCCESS",
            ),
            gateway="Razorpay",
        )
        res = await client.get(f"{BASE}/prepaid/summary", params=prepaid_data._dp())
    data = res.json()["data"]
    by_gateway = {g["gateway"]: D(g["reported_settlement"]) for g in data["gateways"]}
    assert by_gateway["Razorpay"] == 990
    assert sum(by_gateway.values()) == D(data["kpis"]["reported_settlement"]["amount"])


# --- manual adjustments -----------------------------------------------------------


@pytest.mark.parametrize("module", ["cod", "prepaid"])
async def test_adjustments_are_separate_and_explained(db_session, module):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        if module == "cod":
            await _order(db_session, "A-1", "1000.00", awb="AWB-A1")
            await _cod_report(client, "r.csv", "AWB-A1,RA,900")
            params, number, kpi = _dp(), "A-1", "expected"
            amount = "100.00"
        else:
            await prepaid_data._setup(db_session, client)
            params, number, kpi = prepaid_data._dp(), "P3", "orders"
            amount = "76.40"
        before = await _kpis(client, module, params)
        rows, _, _ = await _walk(client, module, kpi, params, page_size=100)
        order = next(r for r in rows if r["order_number"] == number)
        assert order["status"] == "short" and order["adjustment_effect"] == "No manual adjustment."
        res = await client.post(
            f"{BASE}/{module}/adjustments",
            json={"order_id": order["order_id"], "amount": amount, "reason": "Fee waiver"},
        )
        assert res.status_code == 201, res.text
        after = await _kpis(client, module, params)
        rows, _, _ = await _walk(client, module, kpi, params, page_size=100)
        order = next(r for r in rows if r["order_number"] == number)
        export = await client.get(f"{BASE}/{module}/export", params=params)
    assert D(after["adjusted"]["amount"]) == D(amount) and after["adjusted"]["count"] == 1
    assert D(order["difference_before_adjustments"]) == -D(amount)
    assert D(order["difference"]) == 0 and D(order["adjustment_amount"]) == D(amount)
    effect = order["adjustment_effect"]
    assert f"+Rs {D(amount):,.2f}" in effect and f"-Rs {D(amount):,.2f}" in effect
    assert "status changes from Short to Partially Reconciled" in effect
    # Adjustments are never bank money.
    assert after["bank_position"]["amount"] == before["bank_position"]["amount"]
    wb = load_workbook(io.BytesIO(export.content))
    sheet = list(wb["Order-wise"].values)
    assert effect in {r[sheet[0].index("Adjustment effect")] for r in sheet[1:]}
    summary = {r[0]: r[1] for r in wb["Bank & adjustments"].values}
    assert D(str(summary["Manual adjustments"])) == D(amount)
    if module == "prepaid":
        difference = after["settlement_difference"]
        # Headline unchanged by the adjustment; its effect is reported beside it.
        assert difference["amount"] == before["settlement_difference"]["amount"]
        assert D(difference["adjustments"]) == D(amount)
        assert D(difference["after_adjustments"]) == D(difference["amount"]) - D(amount)


async def test_adjustment_on_an_unpaid_order_says_it_has_no_effect(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        await prepaid_data._setup(db_session, client)
        params = prepaid_data._dp()
        rows, _, _ = await _walk(client, "prepaid", "orders", params, page_size=100)
        unpaid = next(r for r in rows if r["order_number"] == "P9")
        await client.post(
            f"{BASE}/prepaid/adjustments",
            json={"order_id": unpaid["order_id"], "amount": "10", "reason": "test only"},
        )
        rows, _, _ = await _walk(client, "prepaid", "adjusted", params)
    assert "has no effect" in rows[0]["adjustment_effect"] and D(rows[0]["difference"]) == 0


async def test_bank_totals_never_exceed_all_credits(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        await prepaid_data._setup(db_session, client)
        k = await _kpis(client, "prepaid", prepaid_data._dp())
    credits = sum(
        D(str(c.amount)) for c in (await db_session.execute(select(BankCredit))).scalars()
    )
    assert D(k["bank_settlement"]["amount"]) + D(k["unmatched_bank_credits"]["amount"]) <= credits
    await _assert_never_over_allocated(db_session)


@pytest.mark.parametrize(
    ("settled", "adjustment", "bank", "covered", "status"),
    [
        ("900", "76.40", "900", True, "matched"),  # bank covers what the gateway reported
        ("900", "76.40", "899.50", True, "matched"),  # rounding only
        ("900", "76.40", "500", False, "partially_reconciled"),
        ("900", "76.40", "976.40", False, "partially_reconciled"),  # reference not covered
        ("900", "76.40", "0", True, "partially_reconciled"),
        ("0", "976.40", "0", True, "partially_reconciled"),  # adjustment alone: no evidence
        ("900", "10", "900", True, "short"),  # adjustment too small
        ("900", "200", "900", True, "excess"),
        ("1100", "-123.60", "976.40", True, "partially_reconciled"),  # bank below reported
        ("1100", "-123.60", "1100", True, "matched"),
    ],
)
async def test_prepaid_matched_needs_the_bank_to_cover_the_reported_settlement_only(
    settled, adjustment, bank, covered, status
):
    assert (
        prepaid_status(
            captured=True,
            failed=False,
            gross=D("1000"),
            refunds=D("0"),
            has_lines=D(settled) != 0,
            expected_net=D("976.40"),
            settled=D(settled),
            adjustment=D(adjustment),
            bank_confirmed=D(bank),
            fully_covered=covered,
        )
        == status
    )


async def test_positive_adjustment_with_full_bank_cover_is_matched_end_to_end(db_session):
    user = await _admin(db_session)
    async with _client(db_session, user) as client:
        await prepaid_data._setup(db_session, client)
        params = prepaid_data._dp()
        # P3: reported 900 against 976.40 expected; the bank credits exactly the 900.
        await _upload(
            client,
            "bank-statements",
            "p3.csv",
            _csv("Credit Date,Narration,Credit Amount,Reference", "25-11-2026,p3,900,U3"),
            source_name="HDFC",
        )
        before = await _kpis(client, "prepaid", params)
        rows, _, _ = await _walk(client, "prepaid", "orders", params, page_size=100)
        p3 = next(r for r in rows if r["order_number"] == "P3")
        assert p3["status"] == "short" and p3["bank_status"] == "partially_confirmed"
        res = await client.post(
            f"{BASE}/prepaid/adjustments",
            json={"order_id": p3["order_id"], "amount": "76.40", "reason": "Fee waiver"},
        )
        assert res.status_code == 201, res.text
        after = await _kpis(client, "prepaid", params)
        rows, _, _ = await _walk(client, "prepaid", "reconciled", params, page_size=100)
    p3 = next(r for r in rows if r["order_number"] == "P3")
    assert p3["status"] == "matched" and D(p3["adjustment_amount"]) == D("76.40")
    assert "status changes from Short to Matched" in p3["adjustment_effect"]
    # Still separate: the adjustment is not bank money; the headline is unchanged.
    assert D(p3["bank_confirmed_settlement"]) == 900
    assert D(p3["bank_remaining"]) == D("76.40")
    assert after["settlement_difference"]["amount"] == before["settlement_difference"]["amount"]
    assert after["bank_settlement"]["amount"] == before["bank_settlement"]["amount"]
    assert after["reconciled"]["count"] == before["reconciled"]["count"] + 1
