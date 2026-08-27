"""Pagination and date-range filtering on the new list endpoints, reusing
the same `PageParams`/`Order.order_datetime` machinery already exercised
elsewhere in this codebase — these tests confirm the new endpoints wire
it correctly, not that the underlying machinery itself works (that's
already covered by the existing Orders pagination tests).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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


async def test_unfulfilled_pool_pagination(db_session: AsyncSession) -> None:
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    leader = await make_user(db_session, email="leader@example.com", role=team_leader_role)
    customer = await make_customer(db_session)
    for i in range(25):
        await make_order(db_session, order_number=f"PAGE-{i:03d}", customer=customer)

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        page1 = await client.get(
            "/api/v1/team/orders/unfulfilled", params={"page": 1, "page_size": 10}
        )
        assert page1.status_code == 200
        page1_body = page1.json()
        assert page1_body["meta"]["total_items"] == 25
        assert page1_body["meta"]["total_pages"] == 3
        assert len(page1_body["data"]) == 10

        page2 = await client.get(
            "/api/v1/team/orders/unfulfilled", params={"page": 2, "page_size": 10}
        )
        page2_body = page2.json()
        assert len(page2_body["data"]) == 10

        page3 = await client.get(
            "/api/v1/team/orders/unfulfilled", params={"page": 3, "page_size": 10}
        )
        page3_body = page3.json()
        assert len(page3_body["data"]) == 5

        # No overlap between pages.
        ids_page1 = {row["order_id"] for row in page1_body["data"]}
        ids_page2 = {row["order_id"] for row in page2_body["data"]}
        ids_page3 = {row["order_id"] for row in page3_body["data"]}
        assert ids_page1.isdisjoint(ids_page2)
        assert ids_page1.isdisjoint(ids_page3)
        assert ids_page2.isdisjoint(ids_page3)
        assert len(ids_page1 | ids_page2 | ids_page3) == 25


async def test_unfulfilled_pool_date_range_filtering(db_session: AsyncSession) -> None:
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    leader = await make_user(db_session, email="leader2@example.com", role=team_leader_role)
    customer = await make_customer(db_session)

    now = datetime.now(UTC)
    old_order = await make_order(
        db_session, order_number="OLD-1", customer=customer, order_datetime=now - timedelta(days=30)
    )
    recent_order = await make_order(
        db_session,
        order_number="RECENT-1",
        customer=customer,
        order_datetime=now - timedelta(days=1),
    )

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        recent_only = await client.get(
            "/api/v1/team/orders/unfulfilled",
            params={"date_from": (now - timedelta(days=5)).isoformat(), "page_size": 100},
        )
        assert recent_only.status_code == 200
        order_ids = {row["order_id"] for row in recent_only.json()["data"]}
        assert str(recent_order.id) in order_ids
        assert str(old_order.id) not in order_ids

        old_only = await client.get(
            "/api/v1/team/orders/unfulfilled",
            params={"date_to": (now - timedelta(days=5)).isoformat(), "page_size": 100},
        )
        order_ids_old = {row["order_id"] for row in old_only.json()["data"]}
        assert str(old_order.id) in order_ids_old
        assert str(recent_order.id) not in order_ids_old


async def test_telecaller_orders_pagination(db_session: AsyncSession) -> None:
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage"]
    )
    leader = await make_user(db_session, email="leader3@example.com", role=team_leader_role)
    telecaller = await make_user(
        db_session, email="tc-page@example.com", role=telecaller_role, team_leader_id=leader.id
    )
    customer = await make_customer(db_session)
    orders = [
        await make_order(db_session, order_number=f"TCPAGE-{i}", customer=customer)
        for i in range(12)
    ]

    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        response = await leader_client.post(
            "/api/v1/team/orders/assign",
            json={
                "order_ids": [str(o.id) for o in orders],
                "mode": "manual",
                "telecaller_id": str(telecaller.id),
            },
        )
        assert response.status_code == 201

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        page1 = await tc_client.get("/api/v1/telecaller/orders", params={"page": 1, "page_size": 5})
        assert page1.json()["meta"]["total_items"] == 12
        assert len(page1.json()["data"]) == 5

        page3 = await tc_client.get("/api/v1/telecaller/orders", params={"page": 3, "page_size": 5})
        assert len(page3.json()["data"]) == 2
