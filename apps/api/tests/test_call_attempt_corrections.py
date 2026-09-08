"""Edit/Delete for individual call attempts (Telecaller order page), plus
the new individual-telecaller summary/daily-performance endpoints (Admin
Telecalling Dashboard drill-down).

`CallAttempt` stays append-only (see its model docstring) — Edit/Delete
are both implemented as new correction rows, never a mutation or a real
delete, so these tests assert on *observed behavior* (what the API
returns, what the visible call history shows, what the denormalized
assignment fields become), not on row counts in the table, which
deliberately keeps growing under the hood.
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


async def _setup(db_session: AsyncSession):
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage"]
    )
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    leader = await make_user(db_session, email="leader@corr.example.com", role=team_leader_role)
    telecaller = await make_user(
        db_session, email="tc@corr.example.com", role=telecaller_role, team_leader_id=leader.id
    )
    other_telecaller = await make_user(
        db_session, email="tc2@corr.example.com", role=telecaller_role, team_leader_id=leader.id
    )
    customer = await make_customer(db_session)
    order = await make_order(db_session, order_number="CORR-001", customer=customer)
    return leader, telecaller, other_telecaller, order


async def _assign(leader_client, order_id: str, telecaller_id: str) -> None:
    response = await leader_client.post(
        "/api/v1/team/orders/assign",
        json={"order_ids": [order_id], "mode": "manual", "telecaller_id": telecaller_id},
    )
    assert response.status_code == 201


async def test_edit_updates_outcome_without_creating_a_visible_new_attempt(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, _other, order = await _setup(db_session)
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        logged = await tc_client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls",
            json={"outcome": "cancelled", "notes": "typo"},
        )
        assert logged.status_code == 201
        attempt_id = logged.json()["data"]["id"]

        edited = await tc_client.patch(
            f"/api/v1/telecaller/orders/{order.id}/calls/{attempt_id}",
            json={"outcome": "confirmed", "notes": "actually confirmed"},
        )
        assert edited.status_code == 200
        assert edited.json()["data"]["outcome"] == "confirmed"
        assert edited.json()["data"]["is_edited"] is True

        history = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}/calls")
        rows = history.json()["data"]
        assert len(rows) == 1
        assert rows[0]["attempt_number"] == 1
        assert rows[0]["outcome"] == "confirmed"
        assert rows[0]["notes"] == "actually confirmed"

        order_view = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")
        assert order_view.json()["data"]["call_status"] == "confirmed"
        assert order_view.json()["data"]["attempt_count"] == 1


async def test_delete_removes_attempt_from_history_and_recomputes_status(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, _other, order = await _setup(db_session)
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        first = await tc_client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls", json={"outcome": "connected"}
        )
        second = await tc_client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls", json={"outcome": "confirmed"}
        )
        second_id = second.json()["data"]["id"]

        deleted = await tc_client.delete(f"/api/v1/telecaller/orders/{order.id}/calls/{second_id}")
        assert deleted.status_code == 200

        history = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}/calls")
        rows = history.json()["data"]
        assert len(rows) == 1
        assert rows[0]["id"] == first.json()["data"]["id"]

        order_view = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")
        # Falls back to attempt #1's outcome, not stuck on the deleted one.
        assert order_view.json()["data"]["call_status"] == "connected"
        assert order_view.json()["data"]["attempt_count"] == 1


async def test_delete_the_only_attempt_reverts_to_not_called(db_session: AsyncSession) -> None:
    leader, telecaller, _other, order = await _setup(db_session)
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        logged = await tc_client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls", json={"outcome": "not_interested"}
        )
        attempt_id = logged.json()["data"]["id"]

        deleted = await tc_client.delete(f"/api/v1/telecaller/orders/{order.id}/calls/{attempt_id}")
        assert deleted.status_code == 200

        history = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}/calls")
        assert history.json()["data"] == []

        order_view = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")
        assert order_view.json()["data"]["call_status"] == "not_called"
        assert order_view.json()["data"]["attempt_count"] == 0


async def test_edit_and_delete_reject_another_telecallers_attempt(db_session: AsyncSession) -> None:
    leader, telecaller, other_telecaller, order = await _setup(db_session)
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        logged = await tc_client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls", json={"outcome": "connected"}
        )
        attempt_id = logged.json()["data"]["id"]

    async with bearer_client(app, get_db, db_session, other_telecaller.id) as other_client:
        edited = await other_client.patch(
            f"/api/v1/telecaller/orders/{order.id}/calls/{attempt_id}",
            json={"outcome": "confirmed"},
        )
        assert edited.status_code == 403

        deleted = await other_client.delete(
            f"/api/v1/telecaller/orders/{order.id}/calls/{attempt_id}"
        )
        assert deleted.status_code == 403


async def test_edit_unknown_attempt_id_returns_404_not_500(db_session: AsyncSession) -> None:
    leader, telecaller, _other, order = await _setup(db_session)
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        fake_id = "00000000-0000-0000-0000-000000000000"
        edited = await tc_client.patch(
            f"/api/v1/telecaller/orders/{order.id}/calls/{fake_id}",
            json={"outcome": "confirmed"},
        )
        assert edited.status_code == 404

        deleted = await tc_client.delete(f"/api/v1/telecaller/orders/{order.id}/calls/{fake_id}")
        assert deleted.status_code == 404


async def test_editing_an_already_deleted_attempt_returns_404_not_500(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, _other, order = await _setup(db_session)
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        logged = await tc_client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls", json={"outcome": "connected"}
        )
        attempt_id = logged.json()["data"]["id"]
        first_delete = await tc_client.delete(
            f"/api/v1/telecaller/orders/{order.id}/calls/{attempt_id}"
        )
        assert first_delete.status_code == 200

        second_delete = await tc_client.delete(
            f"/api/v1/telecaller/orders/{order.id}/calls/{attempt_id}"
        )
        assert second_delete.status_code == 404

        edited = await tc_client.patch(
            f"/api/v1/telecaller/orders/{order.id}/calls/{attempt_id}",
            json={"outcome": "confirmed"},
        )
        assert edited.status_code == 404


async def test_edit_invalid_outcome_returns_422_not_500(db_session: AsyncSession) -> None:
    leader, telecaller, _other, order = await _setup(db_session)
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        logged = await tc_client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls", json={"outcome": "connected"}
        )
        attempt_id = logged.json()["data"]["id"]

        edited = await tc_client.patch(
            f"/api/v1/telecaller/orders/{order.id}/calls/{attempt_id}",
            json={"outcome": "not_a_real_status"},
        )
        assert edited.status_code == 422


async def test_telecaller_detail_summary_and_daily_performance_empty_history(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, _other, _order = await _setup(db_session)
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        summary = await leader_client.get(f"/api/v1/team/telecallers/{telecaller.id}/summary")
        assert summary.status_code == 200
        assert summary.json()["data"]["assigned"] == 0
        assert summary.json()["data"]["fulfilled"] == 0
        assert summary.json()["data"]["conversion_rate"] == 0.0

        daily = await leader_client.get(f"/api/v1/team/telecallers/{telecaller.id}/daily")
        assert daily.status_code == 200
        assert daily.json()["data"] == []


async def test_telecaller_detail_summary_reflects_real_calls_and_fulfillment(
    db_session: AsyncSession,
) -> None:
    from app.models.enums import FulfillmentStatus

    leader, telecaller, _other, order = await _setup(db_session)
    fulfilled_order = await make_order(
        db_session,
        order_number="CORR-002",
        fulfillment_status=FulfillmentStatus.FULFILLED,
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))
        await _assign(leader_client, str(fulfilled_order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        await tc_client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls", json={"outcome": "confirmed"}
        )

    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        summary = await leader_client.get(f"/api/v1/team/telecallers/{telecaller.id}/summary")
        data = summary.json()["data"]
        assert data["assigned"] == 2
        assert data["confirmed"] == 1
        assert data["fulfilled"] == 1
        assert data["total_attempts"] == 1

        daily = await leader_client.get(f"/api/v1/team/telecallers/{telecaller.id}/daily")
        points = daily.json()["data"]
        assert len(points) == 1
        assert points[0]["attempts"] == 1
        assert points[0]["confirmed"] == 1


async def test_telecaller_summary_rejects_telecaller_outside_team(db_session: AsyncSession) -> None:
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage"]
    )
    leader_a = await make_user(db_session, email="leadera@corr.example.com", role=team_leader_role)
    leader_b = await make_user(db_session, email="leaderb@corr.example.com", role=team_leader_role)
    telecaller_b = await make_user(
        db_session,
        email="tcb@corr.example.com",
        role=telecaller_role,
        team_leader_id=leader_b.id,
    )

    async with bearer_client(app, get_db, db_session, leader_a.id) as leader_a_client:
        summary = await leader_a_client.get(f"/api/v1/team/telecallers/{telecaller_b.id}/summary")
        assert summary.status_code == 403
