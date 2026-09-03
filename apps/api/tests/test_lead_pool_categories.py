"""`/team/leads` (the widened, category-aware Admin/Manager Lead Pool) and
`/team/summary`'s per-category counts. Directly exercises the spec's core
safety requirement: COD Unfulfilled, COD Fulfilled, and Prepaid are never
mixed together.
"""

from __future__ import annotations

import pytest
from app.db.session import get_db
from app.main import app
from app.models.enums import FulfillmentStatus, PaymentType
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import (
    bearer_client,
    make_customer,
    make_order,
    make_role,
    make_user,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clear_get_db_override():
    yield
    app.dependency_overrides.clear()


async def _setup_leader(db_session: AsyncSession):
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    return await make_user(db_session, email="leader2@example.com", role=team_leader_role)


async def _seed_orders(db_session: AsyncSession):
    customer = await make_customer(db_session)
    cod_unfulfilled = await make_order(
        db_session,
        order_number="AWL-COD-UNFUL",
        customer=customer,
        payment_type=PaymentType.COD,
        fulfillment_status=FulfillmentStatus.UNFULFILLED,
    )
    cod_fulfilled = await make_order(
        db_session,
        order_number="AWL-COD-FUL",
        customer=customer,
        payment_type=PaymentType.COD,
        fulfillment_status=FulfillmentStatus.FULFILLED,
    )
    prepaid = await make_order(
        db_session,
        order_number="AWL-PREPAID",
        customer=customer,
        payment_type=PaymentType.PREPAID,
        fulfillment_status=FulfillmentStatus.UNFULFILLED,
    )
    other = await make_order(
        db_session,
        order_number="AWL-OTHER",
        customer=customer,
        payment_type=PaymentType.OTHER,
        fulfillment_status=FulfillmentStatus.UNFULFILLED,
    )
    return cod_unfulfilled, cod_fulfilled, prepaid, other


async def test_cod_unfulfilled_filter_excludes_cod_fulfilled_and_prepaid(
    db_session: AsyncSession,
) -> None:
    leader = await _setup_leader(db_session)
    cod_unfulfilled, cod_fulfilled, prepaid, _other = await _seed_orders(db_session)

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        response = await client.get("/api/v1/team/leads", params={"category": "cod_unfulfilled"})
        assert response.status_code == 200
        order_ids = {row["order_id"] for row in response.json()["data"]}

        assert str(cod_unfulfilled.id) in order_ids
        assert str(cod_fulfilled.id) not in order_ids
        assert str(prepaid.id) not in order_ids
        for row in response.json()["data"]:
            assert row["lead_category"] == "cod_unfulfilled"
            assert row["priority"] == "high"


async def test_cod_fulfilled_filter_is_disjoint_from_cod_unfulfilled(
    db_session: AsyncSession,
) -> None:
    leader = await _setup_leader(db_session)
    cod_unfulfilled, cod_fulfilled, prepaid, _other = await _seed_orders(db_session)

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        response = await client.get("/api/v1/team/leads", params={"category": "cod_fulfilled"})
        order_ids = {row["order_id"] for row in response.json()["data"]}

        assert str(cod_fulfilled.id) in order_ids
        assert str(cod_unfulfilled.id) not in order_ids
        assert str(prepaid.id) not in order_ids


async def test_prepaid_filter_never_includes_cod_orders(db_session: AsyncSession) -> None:
    leader = await _setup_leader(db_session)
    cod_unfulfilled, cod_fulfilled, prepaid, _other = await _seed_orders(db_session)

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        response = await client.get("/api/v1/team/leads", params={"category": "prepaid"})
        order_ids = {row["order_id"] for row in response.json()["data"]}

        assert str(prepaid.id) in order_ids
        assert str(cod_unfulfilled.id) not in order_ids
        assert str(cod_fulfilled.id) not in order_ids


async def test_other_payment_type_never_appears_in_any_category_pool(
    db_session: AsyncSession,
) -> None:
    leader = await _setup_leader(db_session)
    _cod_unfulfilled, _cod_fulfilled, _prepaid, other = await _seed_orders(db_session)

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        response = await client.get("/api/v1/team/leads")
        order_ids = {row["order_id"] for row in response.json()["data"]}
        assert str(other.id) not in order_ids


async def test_team_summary_category_counts_are_real_and_disjoint(db_session: AsyncSession) -> None:
    leader = await _setup_leader(db_session)
    await _seed_orders(db_session)

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        response = await client.get("/api/v1/team/summary")
        assert response.status_code == 200
        summary = response.json()["data"]

        assert summary["cod_unfulfilled"] == 1
        assert summary["cod_fulfilled"] == 1
        assert summary["prepaid"] == 1
        assert summary["total_leads"] == 3  # excludes the PaymentType.OTHER order
        assert summary["unassigned_leads"] == 3
