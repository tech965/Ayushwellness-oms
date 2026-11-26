"""Manual COD matching (lookup, preview, confirmation, audit), upload
previews / templates / explicit column mapping, and the RBAC matrix for
`reconciliation.read` / `.upload` / `.adjust`. Synthetic data only."""

from __future__ import annotations

import importlib.util
import io
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from app.db.session import get_db
from app.main import app
from app.models.audit_log import AuditLog
from app.models.enums import PaymentType, ShipmentStatus
from app.models.shipment import Shipment
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import bearer_client, make_order, make_role, make_user

pytestmark = pytest.mark.asyncio

NOW = datetime.now(UTC).replace(microsecond=0)
BASE = "/api/v1/remittance-reconciliation"
READ = ["reconciliation.read"]
UPLOAD = ["reconciliation.read", "reconciliation.upload"]
ALL = ["reconciliation.read", "reconciliation.upload", "reconciliation.adjust"]
API_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clear_override():
    yield
    app.dependency_overrides.clear()


async def _user(db: AsyncSession, email: str, perms=ALL):
    role = await make_role(db, name=f"R-{email}", permission_codes=list(perms))
    return await make_user(db, email=email, role=role)


async def _order(
    db, number, amount, *, awb, status=ShipmentStatus.DELIVERED, payment=PaymentType.COD
):
    started = NOW - timedelta(days=6)
    order = await make_order(
        db,
        order_number=number,
        payment_type=payment,
        total_amount=Decimal(amount),
        order_datetime=started,
    )
    db.add(
        Shipment(
            order_id=order.id,
            current_status=status,
            awb=awb,
            source_system="manual",
            actual_delivery_date=started + timedelta(days=2),
        )
    )
    await db.commit()
    return order


def _csv(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode()


def _files(content: bytes, name: str = "r.csv"):
    return {"file": (name, io.BytesIO(content), "text/csv")}


def _dp():
    return {
        "date_from": (NOW - timedelta(days=60)).isoformat(),
        "date_to": (NOW + timedelta(days=1)).isoformat(),
    }


async def _stray_line(client, db, amount="600", ref="RS1"):
    """Upload one remittance line that matches no order yet."""
    content = _csv(
        "AWB,Remittance Reference,Remittance Amount,Remittance Date",
        f"AWB-STRAY-{ref},{ref},{amount},20-11-2026",
    )
    res = await client.post(
        f"{BASE}/cod/uploads", data={"partner": "Delhivery"}, files=_files(content)
    )
    assert res.status_code == 201, res.text
    lines = (
        await client.get(f"{BASE}/cod/unmatched", params={**_dp(), "kind": "remittance_line"})
    ).json()["data"]
    return next(line for line in lines if line["reference"] == ref)


async def _lookup(client, line_id, q):
    res = await client.get(f"{BASE}/cod/lines/{line_id}/lookup", params={"q": q})
    assert res.status_code == 200, res.text
    return res.json()["data"]


async def _match(client, line_id, order_id, **extra):
    return await client.post(
        f"{BASE}/cod/lines/{line_id}/match", json={"order_id": str(order_id), **extra}
    )


# ------------------------------------------------------------- manual match


async def test_lookup_finds_by_exact_order_number_uuid_or_awb_and_never_fuzzy(db_session):
    user = await _user(db_session, "m1@example.com")
    order = await _order(db_session, "M-100", "1000.00", awb="AWB-M100")
    await _order(db_session, "M-1000", "500.00", awb="AWB-M1000")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        line = await _stray_line(client, db_session)
        by_number = await _lookup(client, line["id"], "M-100")
        assert by_number["found"] and by_number["preview"]["order_number"] == "M-100"
        assert (await _lookup(client, line["id"], "#m-100"))["preview"]["order_number"] == "M-100"
        assert (await _lookup(client, line["id"], "awb-m100"))["preview"]["order_number"] == "M-100"
        by_uuid = await _lookup(client, line["id"], str(order.id))
        assert by_uuid["preview"]["order_number"] == "M-100"
        # "M-10" is a prefix of both order numbers but is not an exact id: no result.
        assert (await _lookup(client, line["id"], "M-10"))["found"] is False
        assert (await _lookup(client, line["id"], "nothing"))["found"] is False


async def test_preview_shows_amounts_partial_settlement_and_status(db_session):
    user = await _user(db_session, "m2@example.com")
    await _order(db_session, "M-200", "1000.00", awb="AWB-M200")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        line = await _stray_line(client, db_session, amount="600")
        p = (await _lookup(client, line["id"], "M-200"))["preview"]
    assert Decimal(p["expected_amount"]) == Decimal("1000.00")
    assert Decimal(p["line_amount"]) == Decimal("600.00")
    assert Decimal(p["other_remitted"]) == 0
    assert Decimal(p["projected_total"]) == Decimal("600.00")
    assert Decimal(p["projected_difference"]) == Decimal("-400.00")
    assert p["projected_status"] == "short" and p["needs_acknowledgement"] is True
    assert any("Partial settlement" in w and "400.00" in w for w in p["warnings"])
    assert p["blockers"] == []


async def test_preview_counts_other_lines_and_excess(db_session):
    user = await _user(db_session, "m3@example.com")
    await _order(db_session, "M-300", "1000.00", awb="AWB-M300")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        first = _csv("AWB,Remittance Reference,Remittance Amount", "AWB-M300,RX1,700")
        await client.post(f"{BASE}/cod/uploads", data={"partner": "D"}, files=_files(first))
        line = await _stray_line(client, db_session, amount="500", ref="RX2")
        p = (await _lookup(client, line["id"], "M-300"))["preview"]
    assert Decimal(p["other_remitted"]) == Decimal("700.00")
    assert Decimal(p["projected_total"]) == Decimal("1200.00")
    assert Decimal(p["projected_difference"]) == Decimal("200.00")
    assert p["projected_status"] == "excess"
    assert any("Excess" in w for w in p["warnings"])


async def test_exact_remaining_amount_needs_no_acknowledgement(db_session):
    user = await _user(db_session, "m4@example.com")
    order = await _order(db_session, "M-400", "1000.00", awb="AWB-M400")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        line = await _stray_line(client, db_session, amount="1000", ref="RX4")
        p = (await _lookup(client, line["id"], "M-400"))["preview"]
        assert p["needs_acknowledgement"] is False and Decimal(p["projected_difference"]) == 0
        assert p["projected_status"] == "partially_reconciled"  # amount ok, no bank evidence yet
        res = await _match(client, line["id"], order.id, confirm=True)
        assert res.status_code == 200, res.text


async def test_match_requires_confirmation_and_acknowledgement_of_a_difference(db_session):
    user = await _user(db_session, "m5@example.com")
    order = await _order(db_session, "M-500", "1000.00", awb="AWB-M500")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        line = await _stray_line(client, db_session, amount="600", ref="RX5")
        no_confirm = await _match(client, line["id"], order.id)
        assert no_confirm.status_code == 422 and "Confirm" in no_confirm.text
        no_ack = await _match(client, line["id"], order.id, confirm=True)
        assert no_ack.status_code == 422 and "difference" in no_ack.text.lower()
        # Nothing was saved by the two refused attempts.
        still = (
            await client.get(f"{BASE}/cod/unmatched", params={**_dp(), "kind": "remittance_line"})
        ).json()
        assert any(r["id"] == line["id"] for r in still["data"])
        ok = await _match(client, line["id"], order.id, confirm=True, acknowledge_difference=True)
        assert ok.status_code == 200, ok.text
        records = (await client.get(f"{BASE}/cod/records", params={**_dp(), "q": "M-500"})).json()[
            "data"
        ]
    assert Decimal(records[0]["remittance_amount"]) == Decimal("600.00")
    assert records[0]["status"] == "short"  # a partial settlement is never "matched"


async def test_match_is_blocked_for_prepaid_undelivered_and_duplicate_targets(db_session):
    user = await _user(db_session, "m6@example.com")
    prepaid = await _order(
        db_session, "M-PRE", "1000.00", awb="AWB-PRE", payment=PaymentType.PREPAID
    )
    undelivered = await _order(
        db_session, "M-UND", "1000.00", awb="AWB-UND", status=ShipmentStatus.IN_TRANSIT
    )
    good = await _order(db_session, "M-GOOD", "1000.00", awb="AWB-GOOD")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        line = await _stray_line(client, db_session, amount="1000", ref="RX6")
        pre = (await _lookup(client, line["id"], "M-PRE"))["preview"]
        und = (await _lookup(client, line["id"], "M-UND"))["preview"]
        assert any("COD order" in b for b in pre["blockers"])
        assert any("not been delivered" in b for b in und["blockers"])
        for blocked in (prepaid, undelivered):
            res = await _match(
                client, line["id"], blocked.id, confirm=True, acknowledge_difference=True
            )
            assert res.status_code == 422
        assert (await _match(client, line["id"], good.id, confirm=True)).status_code == 200
        again = (await _lookup(client, line["id"], "M-GOOD"))["preview"]
        assert any("already matched" in b for b in again["blockers"])
        dup = await _match(client, line["id"], good.id, confirm=True, acknowledge_difference=True)
        assert dup.status_code == 422


async def test_manual_match_is_audited_with_amounts_and_refreshes_the_kpis(db_session):
    user = await _user(db_session, "m7@example.com")
    order = await _order(db_session, "M-700", "1000.00", awb="AWB-M700")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        before = (await client.get(f"{BASE}/cod/summary", params=_dp())).json()["data"]
        assert before["kpis"]["pending"]["count"] == 1
        assert before["kpis"]["unmatched"]["count"] == 0
        line = await _stray_line(client, db_session, amount="1000", ref="RX7")
        mid = (await client.get(f"{BASE}/cod/summary", params=_dp())).json()["data"]
        assert mid["kpis"]["unmatched"]["count"] == 1
        await _match(client, line["id"], order.id, confirm=True)
        after = (await client.get(f"{BASE}/cod/summary", params=_dp())).json()["data"]
        reports = (await client.get(f"{BASE}/cod/reports/partners", params=_dp())).json()["data"]
    assert after["kpis"]["pending"]["count"] == 0
    assert after["kpis"]["remittance_received"]["count"] == 1
    assert after["kpis"]["unmatched"]["count"] == 0
    assert sum(p["pending_orders"] for p in reports) == 0
    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "remittance.cod.manual_match")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    entry = rows[0]
    assert entry.user_id == user.id and entry.entity_id == line["id"]
    assert entry.new_value["order_number"] == "M-700"
    assert entry.new_value["line_amount"] == "1000.00"
    assert entry.new_value["expected_amount"] == "1000.00"
    assert entry.new_value["projected_difference"] == "0.00"
    assert entry.new_value["difference_acknowledged"] is False
    assert entry.previous_value["matched_order_id"] is None


async def test_moving_a_matched_line_to_another_order_records_the_previous_match(db_session):
    user = await _user(db_session, "m8@example.com")
    first = await _order(db_session, "M-801", "1000.00", awb="AWB-801")
    second = await _order(db_session, "M-802", "1000.00", awb="AWB-802")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        line = await _stray_line(client, db_session, amount="1000", ref="RX8")
        await _match(client, line["id"], first.id, confirm=True)
        p = (await _lookup(client, line["id"], "M-802"))["preview"]
        assert any("another order" in w for w in p["warnings"])
        await _match(client, line["id"], second.id, confirm=True)
    entries = (
        (
            await db_session.execute(
                select(AuditLog)
                .where(AuditLog.action == "remittance.cod.manual_match")
                .order_by(AuditLog.created_at)
            )
        )
        .scalars()
        .all()
    )
    assert entries[-1].previous_value["matched_order_id"] == str(first.id)
    assert entries[-1].new_value["matched_order_id"] == str(second.id)


async def test_read_only_and_upload_only_users_cannot_match_or_look_up(db_session):
    reader = await _user(db_session, "m9r@example.com", perms=READ)
    uploader = await _user(db_session, "m9u@example.com", perms=UPLOAD)
    order = await _order(db_session, "M-900", "1000.00", awb="AWB-900")
    admin = await _user(db_session, "m9a@example.com")
    async with bearer_client(app, get_db, db_session, admin.id) as client:
        line = await _stray_line(client, db_session, ref="RX9")
    zero = "00000000-0000-0000-0000-000000000001"
    for who in (reader, uploader):
        async with bearer_client(app, get_db, db_session, who.id) as client:
            assert (
                await client.get(f"{BASE}/cod/lines/{zero}/lookup", params={"q": "x"})
            ).status_code == 403
            res = await _match(
                client, line["id"], order.id, confirm=True, acknowledge_difference=True
            )
            assert res.status_code == 403
            assert (await client.post(f"{BASE}/cod/lines/{line['id']}/unmatch")).status_code == 403


# ---------------------------------------------------- uploads: preview/map


async def test_upload_preview_reports_mapping_and_problems_without_importing(db_session):
    user = await _user(db_session, "u1@example.com")
    good = _csv("AWB,Remittance Amount,Extra", "A1,100,x")
    bad = _csv("Order ID,Amt", "1,100")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        ok = (await client.post(f"{BASE}/cod/uploads/preview", files=_files(good))).json()["data"]
        no = (await client.post(f"{BASE}/cod/uploads/preview", files=_files(bad))).json()["data"]
        listed = (await client.get(f"{BASE}/cod/uploads")).json()["data"]
        stray = (
            await client.get(f"{BASE}/cod/unmatched", params={**_dp(), "kind": "remittance_line"})
        ).json()
    assert ok["can_import"] is True
    assert ok["suggested_mapping"] == {"awb": "AWB", "remittance_amount": "Remittance Amount"}
    assert ok["ignored"] == ["Extra"] and ok["sample_rows"] == [["A1", "100", "x"]]
    assert no["can_import"] is False and no["problems"] and no["headers"] == ["Order ID", "Amt"]
    assert ok["required_groups"] == [["remittance_amount"], ["awb", "order_ref"]]
    assert listed == [] and stray["data"] == []  # preview imported nothing


async def test_upload_with_explicit_column_map_and_result_shows_the_mapping(db_session):
    user = await _user(db_session, "u2@example.com")
    order = await _order(db_session, "U-100", "800.00", awb="AWB-U100")
    content = _csv("Order ID,Paid,Notes", "U-100,800,hello")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        refused = await client.post(
            f"{BASE}/cod/uploads", data={"partner": "P"}, files=_files(content)
        )
        assert refused.status_code == 422
        assert "Columns found in the file: Order ID, Paid, Notes" in refused.text
        mapping = {"order_ref": "Order ID", "remittance_amount": "Paid"}
        res = await client.post(
            f"{BASE}/cod/uploads",
            data={"partner": "P", "column_map": json.dumps(mapping)},
            files=_files(content),
        )
        assert res.status_code == 201, res.text
        data = res.json()["data"]
        bad_json = await client.post(
            f"{BASE}/cod/uploads",
            data={"partner": "P", "column_map": "{oops"},
            files=_files(_csv("AWB,Remittance Amount", "A,1"), "other.csv"),
        )
        assert bad_json.status_code == 422
        recs = (await client.get(f"{BASE}/cod/records", params={**_dp(), "q": "U-100"})).json()[
            "data"
        ]
    assert data["column_mapping"] == mapping and data["ignored_columns"] == ["Notes"]
    assert data["accepted"] == 1 and data["auto_matched_to_orders"] == 1
    assert Decimal(recs[0]["remittance_amount"]) == Decimal("800.00")
    assert order.id is not None


async def test_refused_upload_stores_nothing_and_identical_file_stays_blocked(db_session):
    user = await _user(db_session, "u3@example.com")
    bad = _csv("Foo,Bar", "1,2")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        assert (
            await client.post(f"{BASE}/cod/uploads", data={"partner": "P"}, files=_files(bad))
        ).status_code == 422
        assert (await client.get(f"{BASE}/cod/uploads")).json()["data"] == []
        good = _csv("AWB,Remittance Amount", "A1,100")
        assert (
            await client.post(f"{BASE}/cod/uploads", data={"partner": "P"}, files=_files(good))
        ).status_code == 201
        assert (
            await client.post(f"{BASE}/cod/uploads", data={"partner": "P"}, files=_files(good))
        ).status_code == 409
        files = (await client.get(f"{BASE}/cod/uploads")).json()["data"]
    assert len(files) == 1


async def test_prepaid_and_bank_previews_and_mapping(db_session):
    user = await _user(db_session, "u4@example.com")
    bank = _csv("Txn Date,Description,Deposit,Chq/Ref No", "25-11-2026,NEFT,1000,R1")
    pre = _csv("cf_payment_id,payment_amount,amount_settled,settlement_utr", "111,1000,976.4,U1")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        bp = (await client.post(f"{BASE}/bank-statements/preview", files=_files(bank))).json()[
            "data"
        ]
        pp = (await client.post(f"{BASE}/prepaid/uploads/preview", files=_files(pre))).json()[
            "data"
        ]
        assert bp["can_import"] is False and "Credit amount" in " ".join(bp["problems"])
        assert (
            pp["can_import"] is True and pp["suggested_mapping"]["net_amount"] == "amount_settled"
        )
        mapping = {"amount": "Deposit", "credit_date": "Txn Date", "reference": "Chq/Ref No"}
        res = await client.post(
            f"{BASE}/bank-statements",
            data={"source_name": "HDFC", "column_map": json.dumps(mapping)},
            files=_files(bank),
        )
        assert res.status_code == 201, res.text
        assert res.json()["data"]["column_mapping"]["amount"] == "Deposit"
        ok = await client.post(
            f"{BASE}/prepaid/uploads", data={"gateway": "Cashfree"}, files=_files(pre)
        )
        assert ok.status_code == 201 and ok.json()["data"]["accepted"] == 1


async def test_templates_download_as_csv_with_the_oms_headers(db_session):
    user = await _user(db_session, "u5@example.com", perms=UPLOAD)
    async with bearer_client(app, get_db, db_session, user.id) as client:
        cod = await client.get(f"{BASE}/cod/uploads/template")
        pre = await client.get(f"{BASE}/prepaid/uploads/template")
        bank = await client.get(f"{BASE}/bank-statements/template")
    assert cod.status_code == 200 and "text/csv" in cod.headers["content-type"]
    assert (
        cod.text.strip()
        == "AWB,OMS Order ID,Remittance Reference,Remittance Amount,COD Amount,Remittance Date"
    )
    assert pre.text.startswith(
        "Gateway Transaction ID,OMS Order ID,Settlement Reference,Gross Amount"
    )
    assert bank.text.strip() == "Credit Date,Reference,Credit Amount,Narration"


async def test_preview_template_and_upload_require_the_upload_permission(db_session):
    reader = await _user(db_session, "u6@example.com", perms=READ)
    async with bearer_client(app, get_db, db_session, reader.id) as client:
        for method, path in (
            ("post", "cod/uploads/preview"),
            ("post", "prepaid/uploads/preview"),
            ("post", "bank-statements/preview"),
            ("get", "cod/uploads/template"),
            ("get", "prepaid/uploads/template"),
            ("get", "bank-statements/template"),
        ):
            kwargs = {"files": _files(b"AWB,Remittance Amount\nA,1\n")} if method == "post" else {}
            res = await getattr(client, method)(f"{BASE}/{path}", **kwargs)
            assert res.status_code == 403, path


# ------------------------------------------------------------- RBAC matrix


def _seed_module():
    spec = importlib.util.spec_from_file_location(
        "seed_for_rbac_test", API_ROOT / "scripts" / "seed.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_only_admin_holds_the_new_write_permissions_by_default():
    seed = _seed_module()
    assert (
        "reconciliation.upload" in seed.PERMISSIONS and "reconciliation.adjust" in seed.PERMISSIONS
    )
    roles = seed.ROLE_PERMISSIONS
    assert roles["ADMIN"] == "*"
    for name, perms in roles.items():
        if name == "ADMIN":
            continue
        assert "reconciliation.upload" not in perms, name
        assert "reconciliation.adjust" not in perms, name


def test_read_is_limited_to_the_roles_that_already_had_it():
    seed = _seed_module()
    holders = {
        name
        for name, perms in seed.ROLE_PERMISSIONS.items()
        if perms == "*" or "reconciliation.read" in perms
    }
    assert holders == {"ADMIN", "OPERATIONS", "MANAGEMENT"}
    for name in (
        "FULFILLMENT",
        "CUSTOMER_SUPPORT",
        "MARKETING",
        "TELECALLER",
        "TEAM_LEADER",
        "SHIPMENT_STAFF",
    ):
        assert "reconciliation.read" not in seed.ROLE_PERMISSIONS[name], name


def test_migration_grants_the_new_permissions_to_admin_only():
    spec = importlib.util.spec_from_file_location(
        "mig", API_ROOT / "alembic" / "versions" / "c9e1a5d3f7b2_remittance_reconciliation.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.GRANTED_ROLES == ("ADMIN",)


async def test_every_write_endpoint_rejects_a_user_without_the_right_permission(db_session):
    """Enumerates the OpenAPI routes: every non-GET remittance endpoint needs
    upload or adjust; every GET needs read."""
    reader = await _user(db_session, "rb1@example.com", perms=READ)
    nobody = await _user(db_session, "rb2@example.com", perms=["orders.read"])
    zero = "00000000-0000-0000-0000-000000000001"
    schema = app.openapi()["paths"]
    checked = 0
    async with bearer_client(app, get_db, db_session, reader.id) as client:
        for path, ops in schema.items():
            if "/remittance-reconciliation/" not in path:
                continue
            url = (
                path.replace("{line_id}", zero)
                .replace("{link_id}", zero)
                .replace("{adjustment_id}", zero)
                .replace("{file_id}", zero)
            )
            for method in ops:
                if method == "get":
                    continue
                res = await client.request(method.upper(), url, json={})
                assert res.status_code == 403, f"{method} {path} -> {res.status_code}"
                checked += 1
    assert checked >= 18
    async with bearer_client(app, get_db, db_session, nobody.id) as client:
        for path, ops in schema.items():
            if "/remittance-reconciliation/" in path and "get" in ops:
                url = path.replace("{file_id}", zero).replace("{line_id}", zero)
                res = await client.get(url, params={"q": "x"})
                assert res.status_code == 403, path
