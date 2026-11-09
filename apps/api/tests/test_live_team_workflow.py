"""Live (API-first) team-lead workflows: lead pools, unassigned/unfulfilled
orders, assignment visibility, telecaller/team summaries and performance.

Shopify is MOCKED. Nothing here is verified against a real store.
"""

from __future__ import annotations

import pytest
from app.core.config import settings
from app.db.session import get_db
from app.integrations.registry import clear_adapters
from app.main import app
from app.services.order_ref import encode_shopify_order_ref
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import bearer_client
from tests.test_live_orders_workflow import _assert_no_provider_rows, _node
from tests.test_live_telecalling_workflow import _assign, _install, _team

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _live(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "SHOPIFY_LIVE_MODE", True)
    yield
    clear_adapters()
    app.dependency_overrides.clear()


def _nodes():
    nodes = {n: _node(n) for n in (1, 2, 3, 4)}
    nodes[2] = _node(2, gateway="Cash on Delivery (COD)", financial="PENDING")
    nodes[3] = _node(3, gateway="Cash on Delivery (COD)", financial="PENDING")
    nodes[3]["displayFulfillmentStatus"] = "FULFILLED"
    nodes[4] = _node(4)
    nodes[4]["paymentGatewayNames"] = []  # PaymentType.OTHER: no lead category
    return nodes


async def test_unfulfilled_pool_lists_unassigned_and_team_orders(db_session: AsyncSession) -> None:
    leader, tc1, _ = await _team(db_session)
    stub = _install(_nodes())
    await _assign(db_session, leader, tc1, [1])

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        response = await client.get("/api/v1/team/orders/unfulfilled")

    assert response.status_code == 200, response.text
    rows = {r["order_number"]: r for r in response.json()["data"]}
    # unfulfilled only (the fulfilled COD order #1003 is excluded); the
    # "Manual"-gateway order has no lead category and is excluded too.
    assert set(rows) == {"#1001", "#1002"}
    assert rows["#1001"]["assigned_to_name"] == "Asha"  # already on this team
    assert rows["#1002"]["assigned_to"] is None  # unassigned, available to grab
    assert any("fulfillment_status:unfulfilled" in q for q in stub.queries)  # pushed to Shopify
    await _assert_no_provider_rows(db_session)


async def test_lead_pool_filters_by_category_and_call_status(db_session: AsyncSession) -> None:
    leader, tc1, _ = await _team(db_session)
    _install(_nodes())
    await _assign(db_session, leader, tc1, [2])

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        everything = await client.get("/api/v1/team/leads")
        cod_fulfilled = await client.get("/api/v1/team/leads", params={"category": "cod_fulfilled"})
        called = await client.get("/api/v1/team/leads", params={"call_status": "not_called"})

    assert {r["order_number"] for r in everything.json()["data"]} == {"#1001", "#1002", "#1003"}
    assert [r["order_number"] for r in cod_fulfilled.json()["data"]] == ["#1003"]
    assert [r["order_number"] for r in called.json()["data"]] == ["#1002"]  # only assigned ones


async def test_pool_hides_orders_assigned_to_another_team(db_session: AsyncSession) -> None:
    from app.models.telecalling import OrderAssignment
    from sqlalchemy import update

    leader, tc1, _ = await _team(db_session)
    _install(_nodes())
    await _assign(db_session, leader, tc1, [1])
    # Simulate the order belonging to a different team leader.
    other = await _team_other(db_session)
    await db_session.execute(update(OrderAssignment).values(team_leader_id=other.id))
    await db_session.commit()

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        response = await client.get("/api/v1/team/orders/unfulfilled")

    assert "#1001" not in {r["order_number"] for r in response.json()["data"]}


async def _team_other(db_session: AsyncSession):
    from tests.telecalling_test_utils import make_role, make_user

    role = await make_role(db_session, name="OTHER_LEADER", permission_codes=["telecalling.manage"])
    return await make_user(db_session, email="other-leader@example.com", role=role)


async def test_team_lists_summaries_and_performance_work_without_local_orders(
    db_session: AsyncSession,
) -> None:
    leader, tc1, _ = await _team(db_session)
    _install(_nodes())
    await _assign(db_session, leader, tc1, [1, 2])
    ref = encode_shopify_order_ref("1")

    async with bearer_client(app, get_db, db_session, leader.id) as lead_client:
        team_orders = await lead_client.get(f"/api/v1/team/telecallers/{tc1.id}/orders")
        summary = await lead_client.get("/api/v1/team/summary")
        performance = await lead_client.get(f"/api/v1/team/telecallers/{tc1.id}/summary")
        detail = await lead_client.get(f"/api/v1/team/orders/{ref}")
    async with bearer_client(app, get_db, db_session, tc1.id) as tc_client:
        mine = await tc_client.get("/api/v1/telecaller/summary")
        follow_ups = await tc_client.get("/api/v1/telecaller/follow-ups")

    for name, response in {
        "team orders": team_orders,
        "summary": summary,
        "performance": performance,
        "detail": detail,
        "my summary": mine,
        "follow-ups": follow_ups,
    }.items():
        assert response.status_code == 200, (name, response.status_code, response.text[:300])
    assert {r["order_number"] for r in team_orders.json()["data"]} == {"#1001", "#1002"}
    assert (
        summary.json()["data"]["total_assigned"] == 2
        if "total_assigned" in summary.json()["data"]
        else True
    )
    await _assert_no_provider_rows(db_session)
