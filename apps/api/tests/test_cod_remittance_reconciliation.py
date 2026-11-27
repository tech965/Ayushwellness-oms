"""Module 6 -- COD Remittance Reconciliation: matching, partial settlements,
bank evidence, duplicates, drilldown consistency, percentages, audit, RBAC
and exports. Synthetic data only."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.db.session import get_db
from app.main import app
from app.models.audit_log import AuditLog
from app.models.cod_remittance import CodRemittance
from app.models.enums import PaymentType, ShipmentStatus
from app.models.shipment import Shipment, ShipmentEvent
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import bearer_client, make_order, make_role, make_user

pytestmark = pytest.mark.asyncio

NOW = datetime.now(UTC).replace(microsecond=0)
BASE = "/api/v1/remittance-reconciliation"
READ = ["reconciliation.read"]
ALL = ["reconciliation.read", "reconciliation.upload", "reconciliation.adjust"]


@pytest.fixture(autouse=True)
def _clear_override():
    yield
    app.dependency_overrides.clear()


async def _user(db: AsyncSession, email: str, perms=ALL):
    role = await make_role(db, name=f"R-{email}", permission_codes=list(perms))
    return await make_user(db, email=email, role=role)


async def _cod_order(
    db,
    number,
    amount,
    *,
    awb,
    status=ShipmentStatus.DELIVERED,
    partner="Delhivery",
    days_ago=10,
    payment=PaymentType.COD,
):
    started = NOW - timedelta(days=days_ago)
    order = await make_order(
        db,
        order_number=number,
        payment_type=payment,
        total_amount=Decimal(amount),
        order_datetime=started,
    )
    shipment = Shipment(
        order_id=order.id,
        current_status=status,
        awb=awb,
        source_system="manual",
        pickup_date=started + timedelta(hours=4),
        actual_delivery_date=started + timedelta(days=2),
    )
    db.add(shipment)
    await db.flush()
    db.add(
        ShipmentEvent(
            shipment_id=shipment.id,
            status="IN TRANSIT",
            event_timestamp=NOW,
            courier_name=partner,
            source="shiprocket_webhook",
        )
    )
    await db.commit()
    return order


def _csv(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode()


async def _seed(db: AsyncSession):
    """6 delivered COD orders of Rs 1000 each + 2 that must be excluded."""
    for i in range(1, 7):
        await _cod_order(db, f"C{i}", "1000.00", awb=f"AWB-C{i}")
    await _cod_order(db, "PRE", "1000.00", awb="AWB-PRE", payment=PaymentType.PREPAID)
    await _cod_order(db, "TRN", "1000.00", awb="AWB-TRN", status=ShipmentStatus.IN_TRANSIT)


REPORT = _csv(
    "AWB,OMS Order ID,Remittance Reference,Remittance Amount,Remittance Date",
    "AWB-C1,,R1,1000,20-11-2026",
    "AWB-C2,,R4,1000,20-11-2026",
    "AWB-C3,,R2,600,21-11-2026",
    "AWB-C4,,R3,1200,21-11-2026",
    ",C6,R5,1000,22-11-2026",
    "AWB-UNKNOWN,,R9,500,22-11-2026",
)
BANK = _csv(
    "Credit Date,Narration,Credit Amount,Reference",
    "23-11-2026,NEFT Delhivery,1000,R1",
    "23-11-2026,Misc deposit,777,X-1",
)


async def _upload(client, path, name, content, **form):
    return await client.post(
        f"{BASE}/{path}", data=form, files={"file": (name, io.BytesIO(content), "text/csv")}
    )


async def _setup_full(db_session, client):
    await _seed(db_session)
    r = await _upload(client, "cod/uploads", "rem.csv", REPORT, partner="Delhivery")
    assert r.status_code == 201, r.text
    b = await _upload(client, "bank-statements", "bank.csv", BANK, source_name="HDFC")
    assert b.status_code == 201, b.text
    return r.json()["data"], b.json()["data"]


def _date_params():
    return {
        "date_from": (NOW - timedelta(days=60)).isoformat(),
        "date_to": (NOW + timedelta(days=1)).isoformat(),
    }


async def _summary(client, **extra):
    r = await client.get(f"{BASE}/cod/summary", params={**_date_params(), **extra})
    assert r.status_code == 200, r.text
    return r.json()["data"]


async def _records(client, **extra):
    out, page = [], 1
    while True:
        r = await client.get(
            f"{BASE}/cod/records",
            params={**_date_params(), "page": page, "page_size": 200, **extra},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        out += body["data"]
        if page >= body["meta"]["total_pages"]:
            return out, body["meta"]["total_items"]
        page += 1


async def test_upload_matches_by_awb_and_order_ref_and_reports_unmatched(db_session):
    user = await _user(db_session, "u1@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        up, bank = await _setup_full(db_session, client)
    assert up["rows_read"] == 6 and up["accepted"] == 6
    assert up["auto_matched_to_orders"] == 5  # 4 by AWB + 1 by order ref
    assert up["unmatched"] == 1
    assert bank["accepted"] == 2
    assert bank["auto_linked_cod"] == 1  # only R1 has a remittance line


async def test_summary_amounts_counts_and_statuses(db_session):
    user = await _user(db_session, "u2@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup_full(db_session, client)
        s = await _summary(client)
    k = s["kpis"]
    assert k["delivered_orders"]["count"] == 6  # prepaid + in-transit orders excluded
    assert Decimal(k["expected_collection"]["amount"]) == Decimal("6000.00")
    assert k["remittance_received"]["count"] == 5
    assert Decimal(k["remittance_received"]["amount"]) == Decimal("4800.00")
    assert k["pending"]["count"] == 1 and Decimal(k["pending"]["amount"]) == Decimal("1000.00")
    assert k["short"]["count"] == 1 and Decimal(k["short"]["amount"]) == Decimal("400.00")
    assert k["excess"]["count"] == 1 and Decimal(k["excess"]["amount"]) == Decimal("200.00")
    # Only C1 has bank evidence; C2 and C6 agree with the expected amount but are not
    # bank-confirmed, so they are NOT fully reconciled.
    assert k["reconciled"]["count"] == 1
    assert k["partially_reconciled"]["count"] == 2
    assert k["bank_confirmed"]["count"] == 1
    assert Decimal(k["bank_confirmed"]["amount"]) == Decimal("1000.00")
    # The stray remittance line and the unrelated bank credit both stay visible, as
    # separate figures: courier-reported money is never added to bank money.
    assert k["unmatched"]["count"] == 1 and k["unmatched_bank_credits"]["count"] == 1
    assert Decimal(k["unmatched"]["amount"]) + Decimal(
        k["unmatched_bank_credits"]["amount"]
    ) == Decimal("1277.00")


async def test_payment_status_kpis_group_statuses_and_total_cod_orders_is_wider(db_session):
    """The 5-KPI payment-status summary: `payment_success`/`initiated`/
    `pending` must partition the SAME 6 delivered COD orders as
    `status_breakdown`, and `total_cod_orders` must additionally include the
    non-delivered (in-transit) COD order `_seed()` adds, which none of the
    delivered-only figures ever count."""
    user = await _user(db_session, "u2b@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup_full(db_session, client)
        s = await _summary(client)
    k = s["kpis"]
    # matched=[C1]; partially_reconciled=[C2,C6]; short=[C3]; excess=[C4]; pending=[C5].
    assert k["payment_success"]["count"] == 1
    assert Decimal(k["payment_success"]["amount"]) == Decimal("1000.00")
    assert k["payment_initiated"]["count"] == 4  # 2 partially_reconciled + short + excess
    assert Decimal(k["payment_initiated"]["amount"]) == Decimal("4000.00")
    assert k["payment_pending"]["count"] == 1
    assert Decimal(k["payment_pending"]["amount"]) == Decimal("1000.00")
    success_initiated_pending = (
        k["payment_success"]["count"]
        + k["payment_initiated"]["count"]
        + k["payment_pending"]["count"]
    )
    assert success_initiated_pending == k["delivered_orders"]["count"] == 6
    # total_cod_orders = the 6 delivered COD orders + the 1 in-transit COD order
    # ("TRN") -- wider than every delivered-only figure, including this module's
    # own "COD Delivered Orders" card.
    assert s["denominators"]["total_cod_orders"] == 7
    assert Decimal(s["denominators"]["total_cod_amount"]) == Decimal("7000.00")
    assert k["total_cod_orders"]["count"] == 7


async def test_payment_status_drilldowns_match_their_counts_with_no_overlap(db_session):
    user = await _user(db_session, "u2c@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup_full(db_session, client)
        s = await _summary(client)
        k = s["kpis"]
        seen: set[str] = set()
        for kpi in ("payment_success", "payment_initiated", "payment_pending"):
            rows, total = await _records(client, kpi=kpi)
            assert total == k[kpi]["count"] == len(rows), kpi
            ids = {r["order_id"] for r in rows}
            assert not (ids & seen), f"{kpi} overlaps a previous bucket"
            seen |= ids
        assert len(seen) == k["delivered_orders"]["count"]


async def test_partial_remittance_is_short_never_matched(db_session):
    user = await _user(db_session, "u3@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup_full(db_session, client)
        rows, _ = await _records(client, q="C3")
    row = rows[0]
    assert row["status"] == "short"
    assert Decimal(row["remittance_amount"]) == Decimal("600.00")
    assert Decimal(row["difference"]) == Decimal("-400.00")


async def test_nothing_is_assumed_remitted(db_session):
    """A delivered COD order with no remittance is pending, not reconciled."""
    user = await _user(db_session, "u4@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup_full(db_session, client)
        rows, _ = await _records(client, q="C5")
    assert rows[0]["status"] == "pending"
    assert Decimal(rows[0]["remittance_amount"]) == 0
    assert rows[0]["source"] is None


async def test_matched_requires_bank_evidence_and_partial_bank_credit_is_not_enough(db_session):
    user = await _user(db_session, "u5@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup_full(db_session, client)
        c1, _ = await _records(client, q="C1")
        c2, _ = await _records(client, q="C2")
        assert c1[0]["status"] == "matched" and Decimal(c1[0]["amount_received"]) == Decimal(
            "1000.00"
        )
        assert c2[0]["status"] == "partially_reconciled"
        # A bank credit covering only 400 of R4's 1000 is still not enough.
        partial = _csv("Credit Date,Narration,Credit Amount,Reference", "24-11-2026,part,400,R4")
        res = await _upload(client, "bank-statements", "p.csv", partial, source_name="HDFC")
        assert res.status_code == 201
        c2, _ = await _records(client, q="C2")
        assert c2[0]["status"] == "partially_reconciled"
        # The remaining 600 completes it.
        rest = _csv("Credit Date,Narration,Credit Amount,Reference", "25-11-2026,rest,600,R4")
        await _upload(client, "bank-statements", "q.csv", rest, source_name="HDFC")
        c2, _ = await _records(client, q="C2")
        assert c2[0]["status"] == "matched"
        assert c2[0]["bank_credit_date"] is not None


async def test_duplicate_file_and_overlapping_lines_do_not_duplicate_entries(db_session):
    user = await _user(db_session, "u6@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup_full(db_session, client)
        again = await _upload(client, "cod/uploads", "rem.csv", REPORT, partner="Delhivery")
        assert again.status_code == 409
        # An overlapping report (one repeated line + one new line) imports only the new one.
        overlap = _csv(
            "AWB,Remittance ID,Remittance Amount,Remittance Date",
            "AWB-C3,R2,600,21-11-2026",
            "AWB-C5,R6,1000,23-11-2026",
        )
        res = await _upload(client, "cod/uploads", "o.csv", overlap, partner="Delhivery")
        assert res.status_code == 201
        data = res.json()["data"]
        assert data["accepted"] == 1 and data["duplicates_skipped"] == 1
        c3, _ = await _records(client, q="C3")
        assert Decimal(c3[0]["remittance_amount"]) == Decimal("600.00")  # not 1200
        # Identical bank statement bytes cannot be imported twice either.
        assert (
            await _upload(client, "bank-statements", "bank.csv", BANK, source_name="x")
        ).status_code == 409


async def test_two_partial_remittances_for_one_order_add_up(db_session):
    user = await _user(db_session, "u7@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup_full(db_session, client)
        extra = _csv(
            "AWB,Remittance ID,Remittance Amount,Remittance Date", "AWB-C3,R7,400,23-11-2026"
        )
        await _upload(client, "cod/uploads", "e.csv", extra, partner="Delhivery")
        c3, _ = await _records(client, q="C3")
    assert Decimal(c3[0]["remittance_amount"]) == Decimal("1000.00")
    assert c3[0]["line_count"] == 2
    assert c3[0]["status"] == "partially_reconciled"  # amount complete, bank evidence missing


async def test_shiprocket_api_fallback_is_not_double_counted(db_session):
    user = await _user(db_session, "u8@example.com")
    await _cod_order(db_session, "API1", "1000.00", awb="AWB-API1")
    await _cod_order(db_session, "API2", "1000.00", awb="AWB-API2")
    db_session.add_all(
        [
            CodRemittance(
                awb="AWB-API1", remitted_amount=Decimal("1000.00"), remittance_reference="SR1"
            ),
            CodRemittance(
                awb="AWB-API2", remitted_amount=Decimal("1000.00"), remittance_reference="SR2"
            ),
        ]
    )
    await db_session.commit()
    async with bearer_client(app, get_db, db_session, user.id) as client:
        r1, _ = await _records(client, q="API1")
        assert r1[0]["source"] == "shiprocket_api" and Decimal(r1[0]["remittance_amount"]) == 1000
        # Upload a report that also covers API2: the upload wins, the API value is not added.
        rep = _csv("AWB,Remittance ID,Remittance Amount", "AWB-API2,U2,1000")
        await _upload(client, "cod/uploads", "a.csv", rep, partner="Delhivery")
        r2, _ = await _records(client, q="API2")
    assert r2[0]["source"] == "upload" and Decimal(r2[0]["remittance_amount"]) == 1000


async def test_every_kpi_drilldown_equals_its_count_and_statuses_partition(db_session):
    user = await _user(db_session, "u9@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup_full(db_session, client)
        s = await _summary(client)
        k = s["kpis"]
        expectations = {
            "delivered": k["delivered_orders"]["count"],
            "expected": k["expected_collection"]["count"],
            "received": k["remittance_received"]["count"],
            "bank_confirmed": k["bank_confirmed"]["count"],
            "pending": k["pending"]["count"],
            "short": k["short"]["count"],
            "excess": k["excess"]["count"],
            "reconciled": k["reconciled"]["count"],
            "partial": k["partially_reconciled"]["count"],
        }
        for kpi, count in expectations.items():
            rows, total = await _records(client, kpi=kpi)
            assert total == count == len(rows), kpi
            assert len({r["order_id"] for r in rows}) == len(rows), f"duplicate in {kpi}"
        # Status breakdown partitions the dataset: every order is in exactly one status.
        assert sum(b["count"] for b in s["status_breakdown"]) == k["delivered_orders"]["count"]
        # Each unmatched drilldown == its own KPI.
        for kind, key in (
            ("remittance_line", "unmatched"),
            ("bank_credit", "unmatched_bank_credits"),
        ):
            un = await client.get(f"{BASE}/cod/unmatched", params={**_date_params(), "kind": kind})
            assert un.json()["meta"]["total_items"] == k[key]["count"], key
            assert Decimal(un.json()["totals"]["amount"]) == Decimal(k[key]["amount"]), key


async def test_percentages_use_the_same_dataset_and_show_the_denominator(db_session):
    user = await _user(db_session, "u10@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup_full(db_session, client)
        s = await _summary(client)
    by_key = {p["key"]: p for p in s["percentages"]}
    comp = by_key["completion"]
    assert (
        comp["kind"] == "count"
        and Decimal(comp["numerator"]) == 1
        and Decimal(comp["denominator"]) == 6
    )
    assert comp["value"] == pytest.approx(100 / 6)
    assert comp["denominator_label"] == "delivered COD orders"
    assert by_key["completion_value"]["kind"] == "value"
    assert by_key["pending"]["value"] == pytest.approx(100 / 6)
    assert by_key["pending_value"]["value"] == pytest.approx(100 / 6)  # 1000 of 6000
    assert by_key["short_value"]["value"] == pytest.approx(400 / 6000 * 100)
    # 5 lines matched to delivered COD orders vs 1 stray line in view.
    assert by_key["matched_tx"]["value"] == pytest.approx(5 / 6 * 100)
    assert by_key["unmatched_tx"]["value"] == pytest.approx(1 / 6 * 100)
    for item in s["percentages"]:
        assert item["drill"] is not None


async def test_zero_denominators_give_none_not_zero_or_nan(db_session):
    user = await _user(db_session, "u11@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        s = await _summary(client)  # empty database
    assert s["kpis"]["delivered_orders"]["count"] == 0
    for item in s["percentages"]:
        assert item["value"] is None, item["key"]
    assert all(b["percent_of_orders"] is None for b in s["status_breakdown"])


async def test_filters_apply_consistently_to_cards_and_drilldowns(db_session):
    user = await _user(db_session, "u12@example.com")
    await _cod_order(db_session, "OLD", "500.00", awb="AWB-OLD", days_ago=100, partner="Ekart")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup_full(db_session, client)
        # Date range excludes the 100-day-old order.
        s = await _summary(client)
        assert s["kpis"]["delivered_orders"]["count"] == 6
        # Partner filter narrows both the summary and the list.
        s2 = await _summary(
            client, partner="Ekart", date_from=(NOW - timedelta(days=200)).isoformat()
        )
        rows, total = await _records(
            client, partner="Ekart", date_from=(NOW - timedelta(days=200)).isoformat()
        )
        assert s2["kpis"]["delivered_orders"]["count"] == total == 1
        assert rows[0]["partner"] == "Ekart"
        # Status filter + search + pagination.
        short, n = await _records(client, status="short")
        assert n == 1 and short[0]["order_number"] == "C3"
        page1 = await client.get(
            f"{BASE}/cod/records", params={**_date_params(), "page_size": 2, "page": 1}
        )
        page2 = await client.get(
            f"{BASE}/cod/records", params={**_date_params(), "page_size": 2, "page": 2}
        )
        ids1 = {r["order_id"] for r in page1.json()["data"]}
        ids2 = {r["order_id"] for r in page2.json()["data"]}
        assert len(ids1) == 2 and not ids1 & ids2
        by_awb, _ = await _records(client, q="awb-c4")
        assert [r["order_number"] for r in by_awb] == ["C4"]
        by_ref, _ = await _records(client, q="r2")
        assert [r["order_number"] for r in by_ref] == ["C3"]


async def test_reports_partners_dates_ageing_and_bank_credits(db_session):
    user = await _user(db_session, "u13@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup_full(db_session, client)
        partners = (await client.get(f"{BASE}/cod/reports/partners", params=_date_params())).json()[
            "data"
        ]
        dates = (
            await client.get(f"{BASE}/cod/reports/settlement-dates", params=_date_params())
        ).json()["data"]
        ageing = (await client.get(f"{BASE}/cod/reports/ageing", params=_date_params())).json()[
            "data"
        ]
        credits = (await client.get(f"{BASE}/cod/bank-credits", params=_date_params())).json()
    assert partners[0]["partner"] == "Delhivery" and partners[0]["delivered_orders"] == 6
    assert partners[0]["matched_orders"] == 1 and partners[0]["unmatched_lines"] == 1
    assert sum(d["lines"] for d in dates) == 6  # 5 matched + 1 unmatched line
    assert sum(d["unmatched_lines"] for d in dates) == 1
    # Delivered ~8 days ago: pending C5 and short C3 sit in the 8-15 day bucket.
    bucket = next(b for b in ageing if b["bucket"] == "8-15 days")
    assert bucket["pending_orders"] == 1 and bucket["short_orders"] == 1
    states = {c["reference"]: c["link_state"] for c in credits["data"]}
    assert states["R1"] == "linked" and states["X-1"] == "unlinked"


async def test_manual_adjustment_is_reasoned_audited_and_reversible(db_session):
    user = await _user(db_session, "u14@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup_full(db_session, client)
        c3, _ = await _records(client, q="C3")
        order_id = c3[0]["order_id"]
        no_reason = await client.post(
            f"{BASE}/cod/adjustments", json={"order_id": order_id, "amount": "400", "reason": " "}
        )
        assert no_reason.status_code == 422
        res = await client.post(
            f"{BASE}/cod/adjustments",
            json={"order_id": order_id, "amount": "400", "reason": "Agreed write-off with partner"},
        )
        assert res.status_code == 201, res.text
        adj_id = res.json()["data"]["id"]
        after, _ = await _records(client, q="C3")
        # The adjustment closes the short difference but is NOT bank evidence.
        assert Decimal(after[0]["difference"]) == 0
        assert Decimal(after[0]["amount_received"]) == 0
        assert after[0]["status"] == "partially_reconciled"
        assert (await client.post(f"{BASE}/cod/adjustments/{adj_id}/reverse")).status_code == 200
        assert (await client.post(f"{BASE}/cod/adjustments/{adj_id}/reverse")).status_code == 422
        back, _ = await _records(client, q="C3")
        assert back[0]["status"] == "short"
    actions = (await db_session.execute(select(AuditLog.action))).scalars().all()
    assert (
        "remittance.cod.adjustment" in actions and "remittance.cod.adjustment_reversed" in actions
    )
    assert "remittance.cod.upload" in actions and "remittance.bank.upload" in actions


async def test_manual_match_unmatch_and_bank_link_are_audited(db_session):
    user = await _user(db_session, "u15@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup_full(db_session, client)
        lines = (
            await client.get(
                f"{BASE}/cod/unmatched", params={**_date_params(), "kind": "remittance_line"}
            )
        ).json()["data"]
        stray = lines[0]
        c5, _ = await _records(client, q="C5")
        m = await client.post(
            f"{BASE}/cod/lines/{stray['id']}/match",
            json={"order_id": c5[0]["order_id"], "confirm": True, "acknowledge_difference": True},
        )
        assert m.status_code == 200
        c5, _ = await _records(client, q="C5")
        assert (
            Decimal(c5[0]["remittance_amount"]) == Decimal("500.00") and c5[0]["status"] == "short"
        )
        assert (await client.post(f"{BASE}/cod/lines/{stray['id']}/unmatch")).status_code == 200
        c5, _ = await _records(client, q="C5")
        assert c5[0]["status"] == "pending"
        # Manual bank link: the unrelated 777 credit cannot pay a reference that does not exist...
        credits = (await client.get(f"{BASE}/cod/bank-credits", params=_date_params())).json()[
            "data"
        ]
        x1 = next(c for c in credits if c["reference"] == "X-1")
        evidence = {"bank_reference": "X-1", "reason": "Checked against the statement"}
        bad = await client.post(
            f"{BASE}/cod/bank-links",
            json={"bank_credit_id": x1["id"], "reference": "NOPE", "amount": "1", **evidence},
        )
        assert bad.status_code == 422
        # ...but can be linked to a real remittance reference, up to its free amount.
        too_much = await client.post(
            f"{BASE}/cod/bank-links",
            json={"bank_credit_id": x1["id"], "reference": "R4", "amount": "5000", **evidence},
        )
        assert too_much.status_code == 422
        ok = await client.post(
            f"{BASE}/cod/bank-links",
            json={"bank_credit_id": x1["id"], "reference": "R4", "amount": "700", **evidence},
        )
        assert ok.status_code == 201, ok.text
        link_id = ok.json()["data"]["id"]
        dup = await client.post(
            f"{BASE}/cod/bank-links",
            json={"bank_credit_id": x1["id"], "reference": "R4", "amount": "10", **evidence},
        )
        assert dup.status_code == 422
        assert (await client.post(f"{BASE}/cod/bank-links/{link_id}/reverse")).status_code == 200
    actions = set((await db_session.execute(select(AuditLog.action))).scalars().all())
    assert {
        "remittance.cod.manual_match",
        "remittance.cod.unmatch",
        "remittance.cod.bank_link",
        "remittance.cod.bank_unlink",
    } <= actions


async def test_bad_files_are_rejected_with_clear_errors(db_session):
    user = await _user(db_session, "u16@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        wrong_type = await _upload(client, "cod/uploads", "x.txt", b"hello", partner="P")
        assert wrong_type.status_code == 422
        no_cols = await _upload(client, "cod/uploads", "x.csv", _csv("Foo,Bar", "1,2"), partner="P")
        assert no_cols.status_code == 422
        empty = await _upload(client, "cod/uploads", "x.csv", b"", partner="P")
        assert empty.status_code == 422
        blank_partner = await _upload(client, "cod/uploads", "x.csv", REPORT, partner=" ")
        assert blank_partner.status_code == 422
        mixed = _csv("AWB,Remittance Amount", "A1,100", "A2,oops", ",50")
        ok = await _upload(client, "cod/uploads", "m.csv", mixed, partner="P")
        assert ok.status_code == 201
        data = ok.json()["data"]
        assert data["accepted"] == 1 and data["rejected"] == 2
        assert {e["row"] for e in data["row_errors"]} == {3, 4}


async def test_original_file_is_preserved_byte_for_byte(db_session):
    user = await _user(db_session, "u17@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        up, _ = await _setup_full(db_session, client)
        got = await client.get(f"{BASE}/files/{up['file_id']}/download")
        listed = (await client.get(f"{BASE}/cod/uploads")).json()["data"]
    assert got.status_code == 200 and got.content == REPORT
    assert {f["module"] for f in listed} == {"cod", "bank"}


async def test_rbac_read_only_cannot_upload_adjust_or_match(db_session):
    reader = await _user(db_session, "reader@example.com", perms=READ)
    nobody = await _user(db_session, "nobody@example.com", perms=["orders.read"])
    async with bearer_client(app, get_db, db_session, reader.id) as client:
        assert (await client.get(f"{BASE}/cod/summary", params=_date_params())).status_code == 200
        assert (
            await _upload(client, "cod/uploads", "r.csv", REPORT, partner="P")
        ).status_code == 403
        assert (await _upload(client, "bank-statements", "b.csv", BANK)).status_code == 403
        assert (await client.post(f"{BASE}/cod/rematch")).status_code == 403
        body = {
            "order_id": "00000000-0000-0000-0000-000000000001",
            "amount": "1",
            "reason": "testing",
        }
        assert (await client.post(f"{BASE}/cod/adjustments", json=body)).status_code == 403
        assert (
            await client.post(
                f"{BASE}/cod/lines/00000000-0000-0000-0000-000000000001/match",
                json={"order_id": body["order_id"]},
            )
        ).status_code == 403
    async with bearer_client(app, get_db, db_session, nobody.id) as client:
        for path in ("cod/summary", "cod/records", "cod/unmatched", "cod/export", "sources"):
            assert (await client.get(f"{BASE}/{path}")).status_code == 403, path


async def test_excel_export_has_every_report_sheet_and_matches_the_screen(db_session):
    user = await _user(db_session, "u18@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup_full(db_session, client)
        res = await client.get(f"{BASE}/cod/export", params=_date_params())
    assert res.status_code == 200
    assert "spreadsheetml" in res.headers["content-type"]
    wb = load_workbook(io.BytesIO(res.content))
    assert wb.sheetnames == [
        "Order-wise",
        "Partner-wise",
        "Settlement-date-wise",
        "Pending ageing",
        "Unmatched",
        "Bank & adjustments",
        "Definitions",
    ]
    rows = list(wb["Order-wise"].iter_rows(values_only=True))
    assert rows[0][0] == "Order ID" and len(rows) - 1 == 6
    statuses = {r[rows[0].index("Status")] for r in rows[1:]}
    assert {"Matched", "Short", "Excess", "Pending", "Partially Reconciled"} == statuses
    assert len(list(wb["Unmatched"].iter_rows(values_only=True))) - 1 == 2


async def test_sources_report_configuration_status_without_secrets(db_session):
    user = await _user(db_session, "u19@example.com", perms=READ)
    async with bearer_client(app, get_db, db_session, user.id) as client:
        res = await client.get(f"{BASE}/sources")
    data = res.json()["data"]
    assert {s["key"] for s in data["cod"]} == {"shiprocket", "other"}
    assert {s["key"] for s in data["prepaid"]} == {"cashfree", "other"}
    other = next(s for s in data["cod"] if s["key"] == "other")
    assert other["api_supported"] is False
    assert "password" not in res.text.lower() and "secret" not in res.text.lower()


async def test_ageing_and_settlement_date_rows_drill_to_their_orders(db_session):
    user = await _user(db_session, "u20@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup_full(db_session, client)
        ageing = (await client.get(f"{BASE}/cod/reports/ageing", params=_date_params())).json()[
            "data"
        ]
        for bucket in ageing:
            expected = (
                bucket["pending_orders"] + bucket["short_orders"] + bucket["awaiting_bank_orders"]
            )
            rows, total = await _records(client, age_bucket=bucket["bucket"])
            assert total == expected == len(rows), bucket["bucket"]
        dates = (
            await client.get(f"{BASE}/cod/reports/settlement-dates", params=_date_params())
        ).json()["data"]
        for row in dates:
            if row["date"] == "No date":
                continue
            rows, _ = await _records(client, settled_on=row["date"])
            matched_lines = row["lines"] - row["unmatched_lines"]
            assert sum(r["line_count"] for r in rows) >= len(rows) > 0 or matched_lines == 0
            assert len(rows) <= matched_lines


async def test_status_filter_narrows_cards_percentages_and_drilldown_alike(db_session):
    user = await _user(db_session, "u21@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup_full(db_session, client)
        s = await _summary(client, status="short")
        rows, total = await _records(client, status="short")
    assert s["kpis"]["delivered_orders"]["count"] == total == 1
    assert s["kpis"]["short"]["count"] == 1 and s["kpis"]["pending"]["count"] == 0
    assert sum(b["count"] for b in s["status_breakdown"]) == 1
    completion = next(p for p in s["percentages"] if p["key"] == "completion")
    assert Decimal(completion["denominator"]) == 1  # the denominator is the filtered set


async def test_partner_filter_never_hides_unlinked_bank_credits(db_session):
    user = await _user(db_session, "u22@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        await _setup_full(db_session, client)
        s = await _summary(client, partner="Delhivery")
        un = await client.get(
            f"{BASE}/cod/unmatched", params={**_date_params(), "partner": "Delhivery"}
        )
    assert s["kpis"]["unmatched_bank_credits"]["count"] == 1
    assert {r["kind"] for r in un.json()["data"]} == {"remittance_line", "bank_credit"}
    assert un.json()["meta"]["total_items"] == (
        s["kpis"]["unmatched"]["count"] + s["kpis"]["unmatched_bank_credits"]["count"]
    )
