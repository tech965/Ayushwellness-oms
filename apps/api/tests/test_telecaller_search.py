"""Requirement 7 (review meeting): telecaller order search by order
number, phone number, or customer name -- `GET /telecaller/orders?q=...`,
reusing the existing `/telecaller/orders` list endpoint/scoping rather
than a new one. Every result must already be scoped to the caller's own
assigned orders (`resolve_telecaller_scope`) -- a search term can only
narrow that set, never widen it into another telecaller's data.
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
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage"]
    )
    leader = await make_user(db_session, email="leader@search.example.com", role=team_leader_role)
    telecaller = await make_user(
        db_session, email="tc@search.example.com", role=telecaller_role, team_leader_id=leader.id
    )
    other_telecaller = await make_user(
        db_session,
        email="tc2@search.example.com",
        role=telecaller_role,
        team_leader_id=leader.id,
    )
    return leader, telecaller, other_telecaller


async def _assign(client, order_id: str, telecaller_id: str) -> None:
    response = await client.post(
        "/api/v1/team/orders/assign",
        json={"order_ids": [order_id], "mode": "manual", "telecaller_id": telecaller_id},
    )
    assert response.status_code == 201


async def test_search_matches_order_number(db_session: AsyncSession) -> None:
    leader, telecaller, _other = await _setup(db_session)
    customer = await make_customer(db_session)
    match = await make_order(db_session, order_number="SEARCH-ORD-9001", customer=customer)
    decoy = await make_order(db_session, order_number="SEARCH-ORD-9002", customer=customer)

    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(match.id), str(telecaller.id))
        await _assign(leader_client, str(decoy.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.get("/api/v1/telecaller/orders", params={"q": "9001"})

    assert response.status_code == 200
    rows = response.json()["data"]
    assert [row["order_number"] for row in rows] == ["SEARCH-ORD-9001"]


async def test_search_matches_phone_number(db_session: AsyncSession) -> None:
    leader, telecaller, _other = await _setup(db_session)
    match_customer = await make_customer(db_session, phone="9123456780")
    decoy_customer = await make_customer(db_session, phone="9000000000")
    match = await make_order(db_session, order_number="SEARCH-PH-1", customer=match_customer)
    decoy = await make_order(db_session, order_number="SEARCH-PH-2", customer=decoy_customer)

    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(match.id), str(telecaller.id))
        await _assign(leader_client, str(decoy.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.get("/api/v1/telecaller/orders", params={"q": "9123456780"})

    assert response.status_code == 200
    rows = response.json()["data"]
    assert [row["order_number"] for row in rows] == ["SEARCH-PH-1"]


async def test_search_matches_customer_name_case_insensitively(db_session: AsyncSession) -> None:
    leader, telecaller, _other = await _setup(db_session)
    from app.models.customer import Customer

    priya = Customer(full_name="Priya Sharma", phone="9111111111", email="priya@example.com")
    db_session.add(priya)
    await db_session.flush()
    decoy_customer = await make_customer(db_session, phone="9222222222")

    match = await make_order(db_session, order_number="SEARCH-NAME-1", customer=priya)
    decoy = await make_order(db_session, order_number="SEARCH-NAME-2", customer=decoy_customer)

    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(match.id), str(telecaller.id))
        await _assign(leader_client, str(decoy.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.get("/api/v1/telecaller/orders", params={"q": "priya"})

    assert response.status_code == 200
    rows = response.json()["data"]
    assert [row["order_number"] for row in rows] == ["SEARCH-NAME-1"]


async def test_search_never_returns_another_telecallers_assignment(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, other_telecaller = await _setup(db_session)
    customer = await make_customer(db_session, phone="9333333333")
    others_order = await make_order(
        db_session, order_number="SEARCH-CROSS-1", customer=customer
    )

    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(others_order.id), str(other_telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        # Search by order number, phone, AND name -- none should leak the
        # other telecaller's assignment even though every term matches it.
        for term in ("SEARCH-CROSS-1", "9333333333", "Test Customer"):
            response = await tc_client.get("/api/v1/telecaller/orders", params={"q": term})
            assert response.status_code == 200
            assert response.json()["data"] == []


async def test_search_with_no_match_returns_empty_list_not_an_error(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, _other = await _setup(db_session)
    customer = await make_customer(db_session)
    order = await make_order(db_session, order_number="SEARCH-EMPTY-1", customer=customer)

    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.get(
            "/api/v1/telecaller/orders", params={"q": "no-such-thing-exists"}
        )

    assert response.status_code == 200
    assert response.json()["data"] == []
