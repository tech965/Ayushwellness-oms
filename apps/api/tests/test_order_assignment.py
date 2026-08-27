"""Assignment/bulk-assignment/equal-distribution/reassignment/history
tests for `TelecallingService` and its `/team/orders/*` endpoints.
"""

from __future__ import annotations

import pytest
from app.db.session import get_db
from app.main import app
from app.models.enums import AssignmentStatus
from app.repositories.telecalling import OrderAssignmentRepository
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


async def _setup_team(db_session: AsyncSession, *, telecaller_count: int):
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage"]
    )
    leader = await make_user(db_session, email="leader@example.com", role=team_leader_role)
    telecallers = [
        await make_user(
            db_session, email=f"tc{i}@example.com", role=telecaller_role, team_leader_id=leader.id
        )
        for i in range(telecaller_count)
    ]
    return leader, telecallers


async def test_single_manual_assignment(db_session: AsyncSession) -> None:
    leader, (telecaller,) = await _setup_team(db_session, telecaller_count=1)
    customer = await make_customer(db_session)
    order = await make_order(db_session, order_number="ORD-1", customer=customer)

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        response = await client.post(
            "/api/v1/team/orders/assign",
            json={
                "order_ids": [str(order.id)],
                "mode": "manual",
                "telecaller_id": str(telecaller.id),
            },
        )
        assert response.status_code == 201
        data = response.json()["data"]
        assert len(data) == 1
        assert data[0]["assigned_to"] == str(telecaller.id)
        assert data[0]["assignment_status"] == "active"


async def test_duplicate_assignment_is_rejected(db_session: AsyncSession) -> None:
    leader, (telecaller,) = await _setup_team(db_session, telecaller_count=1)
    customer = await make_customer(db_session)
    order = await make_order(db_session, order_number="ORD-DUP", customer=customer)

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        first = await client.post(
            "/api/v1/team/orders/assign",
            json={
                "order_ids": [str(order.id)],
                "mode": "manual",
                "telecaller_id": str(telecaller.id),
            },
        )
        assert first.status_code == 201

        second = await client.post(
            "/api/v1/team/orders/assign",
            json={
                "order_ids": [str(order.id)],
                "mode": "manual",
                "telecaller_id": str(telecaller.id),
            },
        )
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "conflict"


async def test_bulk_equal_distribution_matches_spec_example(db_session: AsyncSession) -> None:
    """100 orders over 6 telecallers -> 17,17,17,17,16,16."""
    leader, telecallers = await _setup_team(db_session, telecaller_count=6)
    customer = await make_customer(db_session)
    orders = [
        await make_order(db_session, order_number=f"BULK-{i:03d}", customer=customer)
        for i in range(100)
    ]

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        response = await client.post(
            "/api/v1/team/orders/assign",
            json={
                "order_ids": [str(o.id) for o in orders],
                "mode": "equal",
                "telecaller_ids": [str(t.id) for t in telecallers],
            },
        )
        assert response.status_code == 201
        assert len(response.json()["data"]) == 100

    from app.models.telecalling import OrderAssignment
    from sqlalchemy import func, select

    per_telecaller = {}
    for telecaller in telecallers:
        stmt = select(func.count()).where(
            OrderAssignment.assigned_to == telecaller.id,
            OrderAssignment.assignment_status == AssignmentStatus.ACTIVE,
        )
        per_telecaller[str(telecaller.id)] = await db_session.scalar(stmt)

    distribution = sorted(per_telecaller.values(), reverse=True)
    assert distribution == [17, 17, 17, 17, 16, 16]
    assert sum(distribution) == 100


async def test_reassignment_preserves_history_and_flips_active_flag(
    db_session: AsyncSession,
) -> None:
    leader, telecallers = await _setup_team(db_session, telecaller_count=2)
    tc1, tc2 = telecallers
    customer = await make_customer(db_session)
    order = await make_order(db_session, order_number="ORD-REASSIGN", customer=customer)

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        assign_response = await client.post(
            "/api/v1/team/orders/assign",
            json={"order_ids": [str(order.id)], "mode": "manual", "telecaller_id": str(tc1.id)},
        )
        assert assign_response.status_code == 201

        reassign_response = await client.post(
            "/api/v1/team/orders/reassign",
            json={
                "order_id": str(order.id),
                "new_telecaller_id": str(tc2.id),
                "reason": "TC1 on leave",
            },
        )
        assert reassign_response.status_code == 201
        new_assignment = reassign_response.json()["data"]
        assert new_assignment["assigned_to"] == str(tc2.id)
        assert new_assignment["reassigned_from"] == str(tc1.id)
        assert new_assignment["reassigned_to"] == str(tc2.id)
        assert new_assignment["reassignment_reason"] == "TC1 on leave"
        assert new_assignment["assignment_status"] == "active"

    repo = OrderAssignmentRepository(db_session)
    history = await repo.list_for_order(order.id)
    assert len(history) == 2, "reassignment must never delete the prior assignment row"
    statuses = {str(a.assigned_to): a.assignment_status for a in history}
    assert statuses[str(tc1.id)] == AssignmentStatus.INACTIVE
    assert statuses[str(tc2.id)] == AssignmentStatus.ACTIVE

    # Only one row is ever ACTIVE at a time for this order.
    active_rows = [a for a in history if a.assignment_status == AssignmentStatus.ACTIVE]
    assert len(active_rows) == 1


async def test_reassign_without_existing_assignment_is_rejected(db_session: AsyncSession) -> None:
    leader, (telecaller,) = await _setup_team(db_session, telecaller_count=1)
    customer = await make_customer(db_session)
    order = await make_order(db_session, order_number="ORD-NEVER-ASSIGNED", customer=customer)

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        response = await client.post(
            "/api/v1/team/orders/reassign",
            json={
                "order_id": str(order.id),
                "new_telecaller_id": str(telecaller.id),
                "reason": "n/a",
            },
        )
        assert response.status_code == 404
