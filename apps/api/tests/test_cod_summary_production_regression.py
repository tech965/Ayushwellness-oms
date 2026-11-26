"""Regression tests for the production 500 on
GET /api/v1/remittance-reconciliation/cod/summary.

The browser sent exactly this range (an IST month expressed in UTC with a
literal `Z` suffix and milliseconds). These tests pin the application-level
behaviour for that range with empty data and with awkward-but-valid data
(nullable relationships, null dates, null references), so a 500 seen in
production can be attributed to the environment (schema, config) rather than
to these code paths. Synthetic data only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.db.session import get_db
from app.main import app
from app.models.cod_remittance import CodRemittance
from app.models.enums import PaymentType, ShipmentStatus
from app.models.remittance_reconciliation import (
    BankCredit,
    BankCreditLink,
    CodRemittanceLine,
)
from app.models.shipment import Shipment
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import bearer_client, make_order, make_role, make_user

pytestmark = pytest.mark.asyncio

URL = "/api/v1/remittance-reconciliation/cod/summary"
# Exactly as observed in the failing production request.
PROD_RANGE = {
    "date_from": "2026-10-27T18:30:00.000Z",
    "date_to": "2026-11-26T18:29:59.999Z",
}
IN_RANGE = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
OUT_OF_RANGE = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clear_override():
    yield
    app.dependency_overrides.clear()


async def _user(db: AsyncSession, email: str, perms=("reconciliation.read",)):
    role = await make_role(db, name=f"R-{email}", permission_codes=list(perms))
    return await make_user(db, email=email, role=role)


async def _get(client, **extra):
    return await client.get(URL, params={**PROD_RANGE, **extra})


# ----------------------------------------------------------- empty data


async def test_prod_date_range_with_empty_database_returns_200(db_session):
    user = await _user(db_session, "empty@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        r = await _get(client)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["kpis"]["delivered_orders"]["count"] == 0
    assert data["partners"] == []
    assert data["denominators"]["delivered_orders"] == 0


async def test_no_date_params_with_empty_database_returns_200(db_session):
    user = await _user(db_session, "nodates@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        r = await client.get(URL)
    assert r.status_code == 200, r.text


# ------------------------------------------- awkward but valid data shapes


async def test_prod_date_range_with_nullable_relationships_and_null_dates(db_session):
    """A delivered COD order with no customer, no AWB, no courier and no tracking
    events, plus remittance lines / bank credits that have null dates and refs."""
    user = await _user(db_session, "nulls@example.com")

    # Delivered COD order: no customer, shipment without AWB/courier/events.
    order = await make_order(
        db_session,
        order_number="NULLS-1",
        payment_type=PaymentType.COD,
        total_amount=Decimal("750.00"),
        order_datetime=IN_RANGE,
    )
    db_session.add(
        Shipment(
            order_id=order.id,
            current_status=ShipmentStatus.DELIVERED,
            awb=None,
            source_system="manual",
            actual_delivery_date=None,
        )
    )
    # Delivered COD order with a Shiprocket API row whose amounts are all NULL.
    order2 = await make_order(
        db_session,
        order_number="NULLS-2",
        payment_type=PaymentType.COD,
        total_amount=Decimal("500.00"),
        order_datetime=IN_RANGE,
    )
    db_session.add(
        Shipment(
            order_id=order2.id,
            current_status=ShipmentStatus.DELIVERED,
            awb="AWB-NULLS-2",
            source_system="manual",
        )
    )
    db_session.add(CodRemittance(awb="AWB-NULLS-2"))
    # Stray remittance line: null date, null reference, no matched order.
    db_session.add(
        CodRemittanceLine(
            partner_name="  ",
            awb=None,
            order_ref=None,
            remittance_reference=None,
            remittance_amount=Decimal("100.00"),
            remittance_date=None,
            dedupe_key=uuid.uuid4().hex,
        )
    )
    # Bank credits: null date + null reference; one outside the range; one linked.
    undated = BankCredit(amount=Decimal("200.00"), dedupe_key=uuid.uuid4().hex)
    old = BankCredit(
        amount=Decimal("300.00"),
        credit_date=OUT_OF_RANGE,
        reference="OLD-REF",
        dedupe_key=uuid.uuid4().hex,
    )
    linked = BankCredit(
        amount=Decimal("400.00"),
        credit_date=IN_RANGE,
        reference="LINKED-REF",
        dedupe_key=uuid.uuid4().hex,
    )
    db_session.add_all([undated, old, linked])
    await db_session.flush()
    db_session.add(
        BankCreditLink(
            bank_credit_id=linked.id,
            module="cod",
            reference="LINKED-REF",
            amount=Decimal("400.00"),
            method="auto",
        )
    )
    await db_session.flush()

    async with bearer_client(app, get_db, db_session, user.id) as client:
        r = await _get(client)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["kpis"]["delivered_orders"]["count"] == 2
    assert Decimal(data["kpis"]["expected_collection"]["amount"]) == Decimal("1250.00")
    assert data["kpis"]["pending"]["count"] == 2  # nothing was reported as remitted
    assert data["kpis"]["unmatched"]["count"] == 1  # the stray line (null date is kept)
    # The undated credit is always shown; the out-of-range one is hidden by the date range;
    # the fully linked one has nothing left to report.
    assert data["kpis"]["unmatched_bank_credits"]["count"] == 1
    assert Decimal(data["kpis"]["unmatched_bank_credits"]["amount"]) == Decimal("200.00")
    assert data["partners"][0]["partner"] in {"Unknown", ""} or data["partners"]


async def test_order_outside_prod_range_is_excluded_not_an_error(db_session):
    user = await _user(db_session, "outside@example.com")
    order = await make_order(
        db_session,
        order_number="OLD-1",
        payment_type=PaymentType.COD,
        total_amount=Decimal("100.00"),
        order_datetime=OUT_OF_RANGE,
    )
    db_session.add(
        Shipment(
            order_id=order.id,
            current_status=ShipmentStatus.DELIVERED,
            awb="AWB-OLD-1",
            source_system="manual",
        )
    )
    await db_session.flush()
    async with bearer_client(app, get_db, db_session, user.id) as client:
        r = await _get(client)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["kpis"]["delivered_orders"]["count"] == 0


# ------------------------------------------------------------ date parsing


@pytest.mark.parametrize(
    "date_from,date_to",
    [
        ("2026-10-27T18:30:00.000Z", "2026-11-26T18:29:59.999Z"),  # exact production value
        ("2026-10-27T18:30:00Z", "2026-11-26T18:29:59Z"),
        ("2026-10-27T18:30:00+00:00", "2026-11-26T18:29:59.999000+00:00"),
        ("2026-10-28T00:00:00+05:30", "2026-11-26T23:59:59+05:30"),
        ("2026-10-27T18:30:00", "2026-11-26T18:29:59"),  # naive input must not 500 either
        ("2026-10-28", "2026-11-26"),  # date only
    ],
)
async def test_date_serialisations_never_cause_a_500(db_session, date_from, date_to):
    user = await _user(db_session, f"fmt-{abs(hash(date_from))}@example.com")
    order = await make_order(
        db_session,
        order_number="FMT-1",
        payment_type=PaymentType.COD,
        total_amount=Decimal("100.00"),
        order_datetime=IN_RANGE,
    )
    db_session.add(
        Shipment(
            order_id=order.id,
            current_status=ShipmentStatus.DELIVERED,
            awb="AWB-FMT-1",
            source_system="manual",
        )
    )
    db_session.add(
        BankCredit(
            amount=Decimal("50.00"),
            credit_date=IN_RANGE,
            reference="X",
            dedupe_key=uuid.uuid4().hex,
        )
    )
    await db_session.flush()
    async with bearer_client(app, get_db, db_session, user.id) as client:
        r = await client.get(URL, params={"date_from": date_from, "date_to": date_to})
    assert r.status_code == 200, r.text


# ------------------------------------------------------------- error cases


async def test_invalid_date_is_a_422_not_a_500(db_session):
    user = await _user(db_session, "baddate@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        r = await client.get(URL, params={"date_from": "not-a-date"})
    assert r.status_code == 422, r.text


async def test_unknown_status_filter_is_a_422(db_session):
    user = await _user(db_session, "badstatus@example.com")
    async with bearer_client(app, get_db, db_session, user.id) as client:
        r = await _get(client, status="bogus")
    assert r.status_code == 422, r.text


async def test_missing_permission_is_a_403(db_session):
    user = await _user(db_session, "noperm@example.com", perms=())
    async with bearer_client(app, get_db, db_session, user.id) as client:
        r = await _get(client)
    assert r.status_code == 403, r.text


async def test_prepaid_summary_accepts_naive_dates_with_unlinked_credit(db_session):
    """Same defect class on the sibling tab: naive dates vs aware bank credits."""
    user = await _user(db_session, "prepaid-naive@example.com")
    db_session.add(
        BankCredit(
            amount=Decimal("50.00"),
            credit_date=IN_RANGE,
            reference="P-X",
            dedupe_key=uuid.uuid4().hex,
        )
    )
    await db_session.flush()
    async with bearer_client(app, get_db, db_session, user.id) as client:
        r = await client.get(
            "/api/v1/remittance-reconciliation/prepaid/summary",
            params={"date_from": "2026-10-28", "date_to": "2026-11-26"},
        )
    assert r.status_code == 200, r.text
