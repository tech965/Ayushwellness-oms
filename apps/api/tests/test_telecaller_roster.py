"""`GET /team/telecallers/roster` — the "Select Telecaller" assignment-
dialog data source. Distinct from `GET /team/telecallers` (performance
counts, which only ever lists telecallers with existing assignment
activity — see `test_order_assignment.py`): a brand-new TELECALLER user
with zero leads assigned so far must still appear here.
"""

from __future__ import annotations

import pytest
from app.db.session import get_db
from app.main import app
from app.repositories.auth import UserRepository
from app.services.telecalling_service import resolve_team_scope
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import bearer_client, make_role, make_user

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clear_get_db_override():
    yield
    app.dependency_overrides.clear()


async def test_brand_new_telecaller_with_zero_assignments_appears_in_roster(
    db_session: AsyncSession,
) -> None:
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage"]
    )
    leader = await make_user(db_session, email="roster-leader@example.com", role=team_leader_role)
    telecaller = await make_user(
        db_session,
        email="brand-new-tc@example.com",
        name="Brand New Telecaller",
        role=telecaller_role,
        team_leader_id=leader.id,
    )

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        # The performance endpoint must NOT list this telecaller yet --
        # they have no assignment activity at all.
        performance = await client.get("/api/v1/team/telecallers")
        assert performance.status_code == 200
        assert performance.json()["data"] == []

        # The roster endpoint must list them anyway.
        roster = await client.get("/api/v1/team/telecallers/roster")
        assert roster.status_code == 200
        rows = roster.json()["data"]
        assert len(rows) == 1
        assert rows[0]["id"] == str(telecaller.id)
        assert rows[0]["name"] == "Brand New Telecaller"
        assert rows[0]["email"] == "brand-new-tc@example.com"


async def test_roster_matches_a_role_name_created_with_different_casing(
    db_session: AsyncSession,
) -> None:
    """Reproduces the reported production bug: `Role.name` is free-typed by
    an Admin via Administration -> Roles (`RoleCreateRequest.name`) with no
    normalization, and `TELECALLER` isn't part of the default seeded roles
    -- it must be created by hand. A role named e.g. "Telecaller" (not the
    exact-cased "TELECALLER" every backend query hardcodes) must still
    surface its users here, the same way it already shows correctly on the
    Users/Roles admin pages.
    """
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    mixed_case_telecaller_role = await make_role(
        db_session, name="Telecaller", permission_codes=["calls.manage"]
    )
    leader = await make_user(
        db_session, email="roster-leader-casing@example.com", role=team_leader_role
    )
    telecaller = await make_user(
        db_session,
        email="mixed-case-tc@example.com",
        name="Mixed Case Telecaller",
        role=mixed_case_telecaller_role,
        team_leader_id=leader.id,
    )

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        roster = await client.get("/api/v1/team/telecallers/roster")
        assert roster.status_code == 200
        rows = roster.json()["data"]
        assert {row["id"] for row in rows} == {str(telecaller.id)}


async def test_resolve_team_scope_gives_a_role_based_admin_global_scope(
    db_session: AsyncSession,
) -> None:
    """Regression for a confirmed production bug: `resolve_team_scope`
    previously branched on `actor.is_superuser` alone, so a real Admin
    account (`role=ADMIN`, `is_superuser=False`, `team_leader_id=None`)
    fell through to the Team-Leader branch -- `team_leader_id` became the
    Admin's own id, and `/team/telecallers/roster` then filtered out every
    telecaller reporting to no one/a different leader (a real active
    TELECALLER with `team_leader_id=None` was silently excluded from this
    Admin's own roster view).
    """
    admin_role = await make_role(db_session, name="ADMIN", permission_codes=["telecalling.manage"])
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )

    # 1 & 2: an ADMIN user can have is_superuser=False and team_leader_id=None.
    admin = await make_user(
        db_session,
        email="role-based-admin@example.com",
        role=admin_role,
        is_superuser=False,
        team_leader_id=None,
    )
    leader = await make_user(
        db_session, email="scope-leader@example.com", role=team_leader_role
    )

    # `resolve_team_scope` reads `actor.role_names`, which walks
    # `user.user_roles[].role` -- re-fetch through the same eager-loading
    # path `get_current_user` uses in production (`get_with_permissions`)
    # rather than the bare `make_user` return value, whose `user_roles`
    # collection isn't loaded.
    loaded_admin = await UserRepository(db_session).get_with_permissions(admin.id)
    assert loaded_admin is not None

    # 3: a role-based (non-superuser) ADMIN gets global scope.
    admin_scope = resolve_team_scope(loaded_admin)
    assert admin_scope.assigned_to is None
    assert admin_scope.team_leader_id is None

    # 4: a real Team Leader's own scope is unaffected.
    loaded_leader = await UserRepository(db_session).get_with_permissions(leader.id)
    assert loaded_leader is not None
    leader_scope = resolve_team_scope(loaded_leader)
    assert leader_scope.assigned_to is None
    assert leader_scope.team_leader_id == leader.id

    # 5: existing is_superuser=True behavior is unchanged.
    superuser = await make_user(db_session, email="scope-superuser@example.com", is_superuser=True)
    loaded_superuser = await UserRepository(db_session).get_with_permissions(superuser.id)
    assert loaded_superuser is not None
    superuser_scope = resolve_team_scope(loaded_superuser)
    assert superuser_scope.assigned_to is None
    assert superuser_scope.team_leader_id is None


async def test_role_based_admin_sees_a_telecaller_with_no_team_leader_in_the_roster(
    db_session: AsyncSession,
) -> None:
    """End-to-end reproduction of the exact reported bug: a role-based
    Admin (`is_superuser=False`) hitting the real roster endpoint must see
    a `team_leader_id=None` TELECALLER (matching the real "Sourabh"
    production case), not an empty list.
    """
    admin_role = await make_role(db_session, name="ADMIN", permission_codes=["telecalling.manage"])
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage"]
    )
    admin = await make_user(
        db_session,
        email="role-based-admin-e2e@example.com",
        role=admin_role,
        is_superuser=False,
        team_leader_id=None,
    )
    telecaller = await make_user(
        db_session,
        email="no-team-leader-tc@example.com",
        name="Sourabh",
        role=telecaller_role,
        team_leader_id=None,
    )

    async with bearer_client(app, get_db, db_session, admin.id) as client:
        roster = await client.get("/api/v1/team/telecallers/roster")
        assert roster.status_code == 200
        rows = roster.json()["data"]
        assert {row["id"] for row in rows} == {str(telecaller.id)}


async def test_roster_excludes_non_telecaller_users(db_session: AsyncSession) -> None:
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    staff_role = await make_role(db_session, name="STAFF", permission_codes=["orders.read"])
    leader = await make_user(
        db_session, email="roster-leader2@example.com", role=team_leader_role
    )
    # A STAFF user reporting to the same team_leader_id -- must never be
    # offered as an assignable Telecaller just because the FK happens to
    # be set.
    await make_user(
        db_session,
        email="staff-under-leader@example.com",
        role=staff_role,
        team_leader_id=leader.id,
    )

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        roster = await client.get("/api/v1/team/telecallers/roster")
        assert roster.status_code == 200
        assert roster.json()["data"] == []


async def test_roster_excludes_inactive_telecallers(db_session: AsyncSession) -> None:
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage"]
    )
    leader = await make_user(
        db_session, email="roster-leader3@example.com", role=team_leader_role
    )
    active_tc = await make_user(
        db_session,
        email="active-tc@example.com",
        role=telecaller_role,
        team_leader_id=leader.id,
    )
    inactive_tc = await make_user(
        db_session,
        email="inactive-tc@example.com",
        role=telecaller_role,
        team_leader_id=leader.id,
    )
    inactive_tc.is_active = False
    db_session.add(inactive_tc)
    await db_session.commit()

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        roster = await client.get("/api/v1/team/telecallers/roster")
        assert roster.status_code == 200
        rows = roster.json()["data"]
        ids = {row["id"] for row in rows}
        assert str(active_tc.id) in ids
        assert str(inactive_tc.id) not in ids


async def test_team_leader_roster_is_scoped_to_own_team(db_session: AsyncSession) -> None:
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage"]
    )
    leader_a = await make_user(db_session, email="leader-a@example.com", role=team_leader_role)
    leader_b = await make_user(db_session, email="leader-b@example.com", role=team_leader_role)
    tc_a = await make_user(
        db_session, email="tc-a@example.com", role=telecaller_role, team_leader_id=leader_a.id
    )
    await make_user(
        db_session, email="tc-b@example.com", role=telecaller_role, team_leader_id=leader_b.id
    )

    async with bearer_client(app, get_db, db_session, leader_a.id) as client:
        roster = await client.get("/api/v1/team/telecallers/roster")
        rows = roster.json()["data"]
        assert {row["id"] for row in rows} == {str(tc_a.id)}


async def test_admin_roster_includes_every_teams_telecallers(db_session: AsyncSession) -> None:
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage"]
    )
    leader_a = await make_user(db_session, email="leader-c@example.com", role=team_leader_role)
    leader_b = await make_user(db_session, email="leader-d@example.com", role=team_leader_role)
    tc_a = await make_user(
        db_session, email="tc-c@example.com", role=telecaller_role, team_leader_id=leader_a.id
    )
    tc_b = await make_user(
        db_session, email="tc-d@example.com", role=telecaller_role, team_leader_id=leader_b.id
    )
    admin = await make_user(db_session, email="admin-roster@example.com", is_superuser=True)

    async with bearer_client(app, get_db, db_session, admin.id) as client:
        roster = await client.get("/api/v1/team/telecallers/roster")
        rows = roster.json()["data"]
        assert {row["id"] for row in rows} == {str(tc_a.id), str(tc_b.id)}


async def test_roster_id_can_be_used_to_assign_a_lead(db_session: AsyncSession) -> None:
    """End-to-end: the id the roster returns for a zero-assignment
    telecaller is a valid, assignable telecaller_id."""
    from tests.telecalling_test_utils import make_customer, make_order

    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage"]
    )
    leader = await make_user(
        db_session, email="roster-leader4@example.com", role=team_leader_role
    )
    telecaller = await make_user(
        db_session,
        email="assignable-tc@example.com",
        role=telecaller_role,
        team_leader_id=leader.id,
    )
    customer = await make_customer(db_session)
    order = await make_order(db_session, order_number="ROSTER-1", customer=customer)

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        roster = await client.get("/api/v1/team/telecallers/roster")
        roster_id = roster.json()["data"][0]["id"]
        assert roster_id == str(telecaller.id)

        assign = await client.post(
            "/api/v1/team/orders/assign",
            json={"order_ids": [str(order.id)], "mode": "manual", "telecaller_id": roster_id},
        )
        assert assign.status_code == 201
        assert assign.json()["data"][0]["assigned_to"] == roster_id
