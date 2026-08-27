"""Call-attempt logging: sequential numbering, append-only history, and
the denormalized `OrderAssignment` fields staying in sync.
"""

from __future__ import annotations

import pytest
from app.db.session import get_db
from app.main import app
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


async def _setup_assigned_order(db_session: AsyncSession):
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage"]
    )
    leader = await make_user(db_session, email="leader@example.com", role=team_leader_role)
    telecaller = await make_user(
        db_session, email="tc@example.com", role=telecaller_role, team_leader_id=leader.id
    )
    customer = await make_customer(db_session)
    order = await make_order(db_session, order_number="CALL-1", customer=customer)

    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        response = await leader_client.post(
            "/api/v1/team/orders/assign",
            json={
                "order_ids": [str(order.id)],
                "mode": "manual",
                "telecaller_id": str(telecaller.id),
            },
        )
        assert response.status_code == 201

    return telecaller, order


async def test_sequential_attempts_and_history(db_session: AsyncSession) -> None:
    telecaller, order = await _setup_assigned_order(db_session)

    async with bearer_client(app, get_db, db_session, telecaller.id) as client:
        first = await client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls",
            json={"outcome": "not_received", "notes": "No answer."},
        )
        assert first.status_code == 201
        assert first.json()["data"]["attempt_number"] == 1

        second = await client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls",
            json={
                "outcome": "connected",
                "notes": "Customer requested callback tomorrow.",
                "next_follow_up_at": "2026-08-28T10:00:00Z",
            },
        )
        assert second.status_code == 201
        assert second.json()["data"]["attempt_number"] == 2

        history_response = await client.get(f"/api/v1/telecaller/orders/{order.id}/calls")
        assert history_response.status_code == 200
        history = history_response.json()["data"]
        # Append-only, both attempts still present — most recent first.
        assert len(history) == 2
        assert history[0]["attempt_number"] == 2
        assert history[0]["outcome"] == "connected"
        assert history[1]["attempt_number"] == 1
        assert history[1]["outcome"] == "not_received"
        assert history[1]["notes"] == "No answer."

        order_detail = await client.get(f"/api/v1/telecaller/orders/{order.id}")
        detail = order_detail.json()["data"]
        assert detail["call_status"] == "connected"
        assert detail["attempt_count"] == 2
        assert detail["next_follow_up_at"] is not None

        # Cross-order call history includes the order number for context.
        my_calls = await client.get("/api/v1/telecaller/calls")
        assert my_calls.status_code == 200
        my_calls_data = my_calls.json()["data"]
        assert len(my_calls_data) == 2
        assert {entry["order_number"] for entry in my_calls_data} == {order.order_number}


async def test_not_called_is_rejected_as_a_loggable_outcome(db_session: AsyncSession) -> None:
    telecaller, order = await _setup_assigned_order(db_session)
    async with bearer_client(app, get_db, db_session, telecaller.id) as client:
        response = await client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls", json={"outcome": "not_called"}
        )
        assert response.status_code == 422


async def test_quick_status_buttons_are_just_a_preset_log_call(db_session: AsyncSession) -> None:
    telecaller, order = await _setup_assigned_order(db_session)
    async with bearer_client(app, get_db, db_session, telecaller.id) as client:
        response = await client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls", json={"outcome": "confirmed"}
        )
        assert response.status_code == 201
        detail = (await client.get(f"/api/v1/telecaller/orders/{order.id}")).json()["data"]
        assert detail["call_status"] == "confirmed"
