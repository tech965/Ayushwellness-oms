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


async def test_call_log_data_survives_reload_and_a_later_call_without_a_follow_up(
    db_session: AsyncSession,
) -> None:
    """Regression: `TelecallingService.log_call` unconditionally set
    `OrderAssignment.next_follow_up_at` to whatever it was passed --
    including `None`, which every "quick log" action (Mark Confirmed/Not
    Interested/Cancelled) sends since it only specifies `outcome`. A
    telecaller who scheduled a follow-up on one call, then logged any
    later call without re-specifying it, had that follow-up silently
    wiped -- exactly the "entered information disappears" symptom. Also
    proves outcome/notes/attempt data round-trips correctly on a fresh
    GET (simulating navigating away and back / a page refresh), not just
    immediately after the POST.
    """
    telecaller, order = await _setup_assigned_order(db_session)

    async with bearer_client(app, get_db, db_session, telecaller.id) as client:
        # Komal logs a call and schedules a follow-up.
        first = await client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls",
            json={
                "outcome": "call_back_requested",
                "notes": "Asked to call back tomorrow morning.",
                "next_follow_up_at": "2099-01-01T10:00:00Z",
            },
        )
        assert first.status_code == 201

        # A fresh, independent read (simulating navigating away and back,
        # or a page refresh) must show exactly what was just saved.
        reload_after_first = await client.get(f"/api/v1/telecaller/orders/{order.id}")
        detail = reload_after_first.json()["data"]
        assert detail["call_status"] == "call_back_requested"
        assert detail["attempt_count"] == 1
        assert detail["next_follow_up_at"] == "2099-01-01T10:00:00Z"

        history_after_first = await client.get(f"/api/v1/telecaller/orders/{order.id}/calls")
        assert history_after_first.json()["data"][0]["notes"] == (
            "Asked to call back tomorrow morning."
        )

        # Later, Komal uses a "quick log" action -- outcome only, no
        # follow-up re-specified (matching the frontend's quickLog()).
        second = await client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls",
            json={"outcome": "confirmed"},
        )
        assert second.status_code == 201

        # The previously-scheduled follow-up must still be there.
        reload_after_second = await client.get(f"/api/v1/telecaller/orders/{order.id}")
        detail_after_second = reload_after_second.json()["data"]
        assert detail_after_second["call_status"] == "confirmed"
        assert detail_after_second["attempt_count"] == 2
        assert detail_after_second["next_follow_up_at"] == "2099-01-01T10:00:00Z"

        # And the first call's notes are still there too -- append-only.
        history_after_second = await client.get(f"/api/v1/telecaller/orders/{order.id}/calls")
        notes_by_attempt = {
            row["attempt_number"]: row["notes"] for row in history_after_second.json()["data"]
        }
        assert notes_by_attempt[1] == "Asked to call back tomorrow morning."


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
