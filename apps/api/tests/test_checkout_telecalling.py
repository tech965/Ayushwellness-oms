"""Abandoned-checkout assignment/calling/follow-up workflow —
`CheckoutAssignment`/`CheckoutCallAttempt`'s counterpart of
`test_order_assignment.py`/`test_call_attempts.py`/`test_follow_ups.py`,
plus the spec's safety rule: a checkout with no phone/email is never a
telecalling lead, and a recovered (completed) checkout can't be assigned.
"""

from __future__ import annotations

import pytest
from app.db.session import get_db
from app.main import app
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import (
    bearer_client,
    make_abandoned_checkout,
    make_role,
    make_user,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clear_get_db_override():
    yield
    app.dependency_overrides.clear()


async def _setup_team(db_session: AsyncSession, *, telecaller_count: int = 1):
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage"]
    )
    leader = await make_user(db_session, email="checkout-leader@example.com", role=team_leader_role)
    telecallers = [
        await make_user(
            db_session,
            email=f"checkout-tc{i}@example.com",
            role=telecaller_role,
            team_leader_id=leader.id,
        )
        for i in range(telecaller_count)
    ]
    return leader, telecallers


async def test_pool_excludes_checkouts_with_no_contact_info(db_session: AsyncSession) -> None:
    leader, _ = await _setup_team(db_session)
    contactable = await make_abandoned_checkout(db_session, external_id="chk-1")
    anonymous = await make_abandoned_checkout(
        db_session, external_id="chk-2", customer_phone=None, customer_email=None
    )

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        response = await client.get("/api/v1/team/checkouts")
        assert response.status_code == 200
        checkout_ids = {row["checkout_id"] for row in response.json()["data"]}
        assert str(contactable.id) in checkout_ids
        assert str(anonymous.id) not in checkout_ids


async def test_pool_excludes_recovered_checkouts(db_session: AsyncSession) -> None:
    leader, _ = await _setup_team(db_session)
    open_checkout = await make_abandoned_checkout(db_session, external_id="chk-3")
    recovered = await make_abandoned_checkout(db_session, external_id="chk-4", is_recovered=True)

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        response = await client.get("/api/v1/team/checkouts")
        checkout_ids = {row["checkout_id"] for row in response.json()["data"]}
        assert str(open_checkout.id) in checkout_ids
        assert str(recovered.id) not in checkout_ids


async def test_recovered_checkout_cannot_be_assigned(db_session: AsyncSession) -> None:
    leader, (telecaller,) = await _setup_team(db_session)
    recovered = await make_abandoned_checkout(db_session, external_id="chk-5", is_recovered=True)

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        response = await client.post(
            "/api/v1/team/checkouts/assign",
            json={
                "checkout_ids": [str(recovered.id)],
                "mode": "manual",
                "telecaller_id": str(telecaller.id),
            },
        )
        assert response.status_code == 409


async def test_full_checkout_assign_call_followup_workflow(db_session: AsyncSession) -> None:
    leader, (telecaller,) = await _setup_team(db_session)
    checkout = await make_abandoned_checkout(db_session, external_id="chk-6")

    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        assign = await leader_client.post(
            "/api/v1/team/checkouts/assign",
            json={
                "checkout_ids": [str(checkout.id)],
                "mode": "manual",
                "telecaller_id": str(telecaller.id),
            },
        )
        assert assign.status_code == 201
        assert assign.json()["data"][0]["assigned_to"] == str(telecaller.id)

        # A second plain "assign" of the same checkout must be rejected —
        # reassign is the explicit path (mirrors order-assignment rule).
        conflict = await leader_client.post(
            "/api/v1/team/checkouts/assign",
            json={
                "checkout_ids": [str(checkout.id)],
                "mode": "manual",
                "telecaller_id": str(telecaller.id),
            },
        )
        assert conflict.status_code == 409

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        my_leads = await tc_client.get("/api/v1/telecaller/checkouts")
        assert my_leads.status_code == 200
        assert {row["checkout_id"] for row in my_leads.json()["data"]} == {str(checkout.id)}
        assert my_leads.json()["data"][0]["lead_category"] == "abandoned_checkout"
        assert my_leads.json()["data"][0]["priority"] == "high"

        log_call = await tc_client.post(
            f"/api/v1/telecaller/checkouts/{checkout.id}/calls",
            json={"outcome": "call_back_requested", "notes": "Wants a callback tomorrow."},
        )
        assert log_call.status_code == 201
        assert log_call.json()["data"]["attempt_number"] == 1

        follow_up = await tc_client.post(
            f"/api/v1/telecaller/checkouts/{checkout.id}/follow-up",
            json={"next_follow_up_at": "2099-01-01T10:00:00Z"},
        )
        assert follow_up.status_code == 200
        assert follow_up.json()["data"]["call_status"] == "call_back_requested"

        history = await tc_client.get(f"/api/v1/telecaller/checkouts/{checkout.id}/calls")
        assert history.status_code == 200
        assert len(history.json()["data"]) == 1

        my_calls = await tc_client.get("/api/v1/telecaller/calls")
        assert my_calls.status_code == 200  # order-call-history endpoint unaffected


async def test_telecaller_cannot_access_another_telecallers_checkout(
    db_session: AsyncSession,
) -> None:
    leader, (telecaller_a, telecaller_b) = await _setup_team(db_session, telecaller_count=2)
    checkout = await make_abandoned_checkout(db_session, external_id="chk-7")

    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await leader_client.post(
            "/api/v1/team/checkouts/assign",
            json={
                "checkout_ids": [str(checkout.id)],
                "mode": "manual",
                "telecaller_id": str(telecaller_a.id),
            },
        )

    async with bearer_client(app, get_db, db_session, telecaller_b.id) as tc_b_client:
        response = await tc_b_client.get(f"/api/v1/telecaller/checkouts/{checkout.id}")
        assert response.status_code == 403


async def test_team_summary_includes_abandoned_checkouts_count(db_session: AsyncSession) -> None:
    leader, _ = await _setup_team(db_session)
    await make_abandoned_checkout(db_session, external_id="chk-8")
    await make_abandoned_checkout(db_session, external_id="chk-9", is_recovered=True)

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        response = await client.get("/api/v1/team/summary")
        assert response.status_code == 200
        # Only the open, contactable checkout counts — the recovered one
        # is excluded, same as the pool.
        assert response.json()["data"]["abandoned_checkouts"] == 1
