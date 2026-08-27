"""Follow-up scheduling and today/overdue/upcoming filtering, around the
IST calendar-day boundary — reusing `app.core.timezone.ist_day_bounds`.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from app.core.timezone import ist_day_bounds
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


async def _setup_assigned_order(db_session: AsyncSession, order_number: str):
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage"]
    )
    leader = await make_user(
        db_session, email=f"leader-{order_number}@example.com", role=team_leader_role
    )
    telecaller = await make_user(
        db_session,
        email=f"tc-{order_number}@example.com",
        role=telecaller_role,
        team_leader_id=leader.id,
    )
    customer = await make_customer(db_session)
    order = await make_order(db_session, order_number=order_number, customer=customer)

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


async def test_schedule_follow_up_without_logging_a_call(db_session: AsyncSession) -> None:
    telecaller, order = await _setup_assigned_order(db_session, "FU-SCHEDULE")
    start, _ = ist_day_bounds()
    tomorrow = (start + timedelta(days=1, hours=10)).isoformat()

    async with bearer_client(app, get_db, db_session, telecaller.id) as client:
        response = await client.post(
            f"/api/v1/telecaller/orders/{order.id}/follow-up", json={"next_follow_up_at": tomorrow}
        )
        assert response.status_code == 200
        assert response.json()["data"]["next_follow_up_at"] is not None
        # Scheduling a follow-up alone must not fabricate a call attempt.
        assert response.json()["data"]["attempt_count"] == 0


async def test_today_overdue_upcoming_buckets(db_session: AsyncSession) -> None:
    start, end = ist_day_bounds()

    telecaller_today, order_today = await _setup_assigned_order(db_session, "FU-TODAY")
    telecaller_overdue, order_overdue = await _setup_assigned_order(db_session, "FU-OVERDUE")
    telecaller_upcoming, order_upcoming = await _setup_assigned_order(db_session, "FU-UPCOMING")

    async def _schedule(telecaller, order, when):
        async with bearer_client(app, get_db, db_session, telecaller.id) as client:
            response = await client.post(
                f"/api/v1/telecaller/orders/{order.id}/follow-up",
                json={"next_follow_up_at": when.isoformat()},
            )
            assert response.status_code == 200

    await _schedule(telecaller_today, order_today, start + timedelta(hours=1))
    await _schedule(telecaller_overdue, order_overdue, start - timedelta(days=2))
    await _schedule(telecaller_upcoming, order_upcoming, end + timedelta(days=3))

    async def _order_ids_for(telecaller, when):
        async with bearer_client(app, get_db, db_session, telecaller.id) as client:
            response = await client.get(f"/api/v1/telecaller/follow-ups?when={when}")
            assert response.status_code == 200
            return {row["order_id"] for row in response.json()["data"]}

    assert str(order_today.id) in await _order_ids_for(telecaller_today, "today")
    assert str(order_today.id) not in await _order_ids_for(telecaller_today, "overdue")
    assert str(order_today.id) not in await _order_ids_for(telecaller_today, "upcoming")

    assert str(order_overdue.id) in await _order_ids_for(telecaller_overdue, "overdue")
    assert str(order_overdue.id) not in await _order_ids_for(telecaller_overdue, "today")

    assert str(order_upcoming.id) in await _order_ids_for(telecaller_upcoming, "upcoming")
    assert str(order_upcoming.id) not in await _order_ids_for(telecaller_upcoming, "today")


async def test_follow_up_on_unassigned_order_is_rejected(db_session: AsyncSession) -> None:
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage"]
    )
    telecaller = await make_user(db_session, email="lonely-tc@example.com", role=telecaller_role)
    customer = await make_customer(db_session)
    order = await make_order(db_session, order_number="FU-UNASSIGNED", customer=customer)

    async with bearer_client(app, get_db, db_session, telecaller.id) as client:
        response = await client.post(
            f"/api/v1/telecaller/orders/{order.id}/follow-up",
            json={"next_follow_up_at": "2026-09-01T10:00:00Z"},
        )
        assert response.status_code == 404
