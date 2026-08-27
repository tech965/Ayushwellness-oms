"""Row-level scoping / authorization tests for the Team Leader and
Telecaller workflow — the security requirement the whole feature exists
to satisfy: Admin sees everything, a Team Leader sees only their own
team, a Telecaller sees only their own assigned orders, and none of them
can widen that by supplying a different id in the request.
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
    """`bearer_client` sets `app.dependency_overrides[get_db]` directly
    (it isn't a fixture with its own teardown) — clear it after every test
    so a later test that doesn't touch the DB at all never resolves a
    previous test's already-torn-down session.
    """
    yield
    app.dependency_overrides.clear()


async def _setup_two_teams(db_session: AsyncSession):
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage"]
    )

    leader_a = await make_user(db_session, email="leader-a@example.com", role=team_leader_role)
    leader_b = await make_user(db_session, email="leader-b@example.com", role=team_leader_role)
    tc_a1 = await make_user(
        db_session, email="tc-a1@example.com", role=telecaller_role, team_leader_id=leader_a.id
    )
    tc_b1 = await make_user(
        db_session, email="tc-b1@example.com", role=telecaller_role, team_leader_id=leader_b.id
    )

    customer = await make_customer(db_session)
    order_a = await make_order(db_session, order_number="RBAC-A-1", customer=customer)
    order_b = await make_order(db_session, order_number="RBAC-B-1", customer=customer)

    return {
        "leader_a": leader_a,
        "leader_b": leader_b,
        "tc_a1": tc_a1,
        "tc_b1": tc_b1,
        "order_a": order_a,
        "order_b": order_b,
    }


async def _assign(db_session: AsyncSession, ctx, *, order_id, telecaller_id, actor):
    async with bearer_client(app, get_db, db_session, actor.id) as client:
        response = await client.post(
            "/api/v1/team/orders/assign",
            json={
                "order_ids": [str(order_id)],
                "mode": "manual",
                "telecaller_id": str(telecaller_id),
            },
        )
        assert response.status_code == 201, response.text
        return response.json()


async def test_telecaller_cannot_access_another_telecallers_order(db_session: AsyncSession) -> None:
    ctx = await _setup_two_teams(db_session)
    await _assign(
        db_session,
        ctx,
        order_id=ctx["order_a"].id,
        telecaller_id=ctx["tc_a1"].id,
        actor=ctx["leader_a"],
    )
    await _assign(
        db_session,
        ctx,
        order_id=ctx["order_b"].id,
        telecaller_id=ctx["tc_b1"].id,
        actor=ctx["leader_b"],
    )

    async with bearer_client(app, get_db, db_session, ctx["tc_a1"].id) as tc_a1_client:
        own_order = await tc_a1_client.get(f"/api/v1/telecaller/orders/{ctx['order_a'].id}")
        assert own_order.status_code == 200

        others_order = await tc_a1_client.get(f"/api/v1/telecaller/orders/{ctx['order_b'].id}")
        assert others_order.status_code == 403
        assert others_order.json()["error"]["code"] == "authorization_error"


async def test_telecaller_list_only_returns_own_orders(db_session: AsyncSession) -> None:
    ctx = await _setup_two_teams(db_session)
    await _assign(
        db_session,
        ctx,
        order_id=ctx["order_a"].id,
        telecaller_id=ctx["tc_a1"].id,
        actor=ctx["leader_a"],
    )
    await _assign(
        db_session,
        ctx,
        order_id=ctx["order_b"].id,
        telecaller_id=ctx["tc_b1"].id,
        actor=ctx["leader_b"],
    )

    async with bearer_client(app, get_db, db_session, ctx["tc_a1"].id) as tc_a1_client:
        response = await tc_a1_client.get("/api/v1/telecaller/orders")
        assert response.status_code == 200
        order_ids = {row["order_id"] for row in response.json()["data"]}
        assert order_ids == {str(ctx["order_a"].id)}


async def test_telecaller_cannot_reach_team_endpoints(db_session: AsyncSession) -> None:
    ctx = await _setup_two_teams(db_session)
    async with bearer_client(app, get_db, db_session, ctx["tc_a1"].id) as tc_a1_client:
        response = await tc_a1_client.get("/api/v1/team/orders/unfulfilled")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "authorization_error"


async def test_team_leader_sees_only_their_own_team(db_session: AsyncSession) -> None:
    ctx = await _setup_two_teams(db_session)
    await _assign(
        db_session,
        ctx,
        order_id=ctx["order_a"].id,
        telecaller_id=ctx["tc_a1"].id,
        actor=ctx["leader_a"],
    )
    await _assign(
        db_session,
        ctx,
        order_id=ctx["order_b"].id,
        telecaller_id=ctx["tc_b1"].id,
        actor=ctx["leader_b"],
    )

    async with bearer_client(app, get_db, db_session, ctx["leader_a"].id) as leader_a_client:
        response = await leader_a_client.get("/api/v1/team/orders/unfulfilled")
        assert response.status_code == 200
        order_ids = {row["order_id"] for row in response.json()["data"]}
        assert order_ids == {str(ctx["order_a"].id)}


async def test_team_leader_cannot_view_another_teams_order(db_session: AsyncSession) -> None:
    ctx = await _setup_two_teams(db_session)
    await _assign(
        db_session,
        ctx,
        order_id=ctx["order_b"].id,
        telecaller_id=ctx["tc_b1"].id,
        actor=ctx["leader_b"],
    )

    async with bearer_client(app, get_db, db_session, ctx["leader_a"].id) as leader_a_client:
        response = await leader_a_client.get(f"/api/v1/team/orders/{ctx['order_b'].id}")
        assert response.status_code == 403


async def test_team_leader_cannot_assign_to_another_teams_telecaller(
    db_session: AsyncSession,
) -> None:
    ctx = await _setup_two_teams(db_session)
    async with bearer_client(app, get_db, db_session, ctx["leader_a"].id) as leader_a_client:
        response = await leader_a_client.post(
            "/api/v1/team/orders/assign",
            json={
                "order_ids": [str(ctx["order_a"].id)],
                "mode": "manual",
                "telecaller_id": str(ctx["tc_b1"].id),
            },
        )
        assert response.status_code in (403, 404)


async def test_admin_sees_every_team(db_session: AsyncSession) -> None:
    ctx = await _setup_two_teams(db_session)
    admin = await make_user(db_session, email="admin@example.com", is_superuser=True)
    await _assign(
        db_session,
        ctx,
        order_id=ctx["order_a"].id,
        telecaller_id=ctx["tc_a1"].id,
        actor=ctx["leader_a"],
    )
    await _assign(
        db_session,
        ctx,
        order_id=ctx["order_b"].id,
        telecaller_id=ctx["tc_b1"].id,
        actor=ctx["leader_b"],
    )

    async with bearer_client(app, get_db, db_session, admin.id) as admin_client:
        response = await admin_client.get("/api/v1/team/orders/unfulfilled")
        assert response.status_code == 200
        order_ids = {row["order_id"] for row in response.json()["data"]}
        assert order_ids == {str(ctx["order_a"].id), str(ctx["order_b"].id)}


async def test_telecalling_endpoints_require_authentication() -> None:
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/telecaller/orders")
        assert response.status_code == 401
        response = await client.get("/api/v1/team/orders/unfulfilled")
        assert response.status_code == 401
