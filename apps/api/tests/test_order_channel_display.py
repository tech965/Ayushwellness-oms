"""Review-meeting requirement: the Telecaller order detail page must show
a clearly visible "Order Channel" classification (Amazon Order, Flipkart
Order, Distributor Order, ...) without the telecaller ever opening
Shopify.

No new channel-detection logic here or in the frontend -- the API layer
(`AssignedOrderResponse.order_channel`, wired in
`app.api.v1.endpoints.team.to_assigned_order_response`) just exposes the
EXACT existing `ShopifyFulfillmentService.resolve_order_channel` result
(via its new `resolve_order_channel_label` label wrapper) that already
decides the outbound Shopify channel tag (see
`tests/test_call_outcome_and_channel_tagging.py`). These tests prove that
value reaches `GET /telecaller/orders/{id}` correctly -- never that the
channel logic itself is correct (already covered elsewhere).

Numbered comments map 1:1 to this feature's 12 test scenarios.
"""

from __future__ import annotations

import pytest
from app.db.session import get_db
from app.main import app
from app.models.enums import OrderStatus
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
        db_session, name="TELECALLER", permission_codes=["calls.manage", "orders.confirm"]
    )
    leader = await make_user(db_session, email="leader@channel.example.com", role=team_leader_role)
    telecaller = await make_user(
        db_session, email="tc@channel.example.com", role=telecaller_role, team_leader_id=leader.id
    )
    other_telecaller = await make_user(
        db_session, email="tc2@channel.example.com", role=telecaller_role, team_leader_id=leader.id
    )
    customer = await make_customer(db_session)
    return leader, telecaller, other_telecaller, customer


async def _assign(client, order_id: str, telecaller_id: str) -> None:
    response = await client.post(
        "/api/v1/team/orders/assign",
        json={"order_ids": [order_id], "mode": "manual", "telecaller_id": telecaller_id},
    )
    assert response.status_code == 201


async def _channel_for_order(
    db_session: AsyncSession,
    *,
    order_number: str,
    shopify_order_id: str | None,
    shopify_tags: list[str] | None = None,
) -> str:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session,
        order_number=order_number,
        customer=customer,
        status=OrderStatus.PENDING,
        shopify_order_id=shopify_order_id,
    )
    if shopify_tags is not None:
        order.shopify_tags = shopify_tags
        await db_session.flush()
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")
    assert response.status_code == 200
    return response.json()["data"]["order_channel"]


# TEST 1
async def test_amazon_order_shows_amazon_order_channel(db_session: AsyncSession) -> None:
    channel = await _channel_for_order(
        db_session,
        order_number="CHAN-001",
        shopify_order_id="920001",
        shopify_tags=["Amazon FBA"],
    )
    assert channel == "Amazon Order"


# TEST 2
async def test_flipkart_order_shows_flipkart_order_channel(db_session: AsyncSession) -> None:
    channel = await _channel_for_order(
        db_session,
        order_number="CHAN-002",
        shopify_order_id="920002",
        shopify_tags=["Flipkart Fulfilled"],
    )
    assert channel == "Flipkart Order"


# TEST 3
async def test_blinkit_order_shows_blinkit_order_channel(db_session: AsyncSession) -> None:
    channel = await _channel_for_order(
        db_session,
        order_number="CHAN-003",
        shopify_order_id="920003",
        shopify_tags=["Blinkit Quick Commerce"],
    )
    assert channel == "Blinkit Order"


# TEST 4
async def test_meesho_order_shows_meesho_order_channel(db_session: AsyncSession) -> None:
    channel = await _channel_for_order(
        db_session,
        order_number="CHAN-004",
        shopify_order_id="920004",
        shopify_tags=["Meesho Reseller"],
    )
    assert channel == "Meesho Order"


# TEST 5
async def test_distributor_order_shows_distributor_order_channel(db_session: AsyncSession) -> None:
    channel = await _channel_for_order(
        db_session,
        order_number="CHAN-005",
        shopify_order_id="920005",
        shopify_tags=["Distributor"],
    )
    assert channel == "Distributor Order"


# TEST 6
async def test_influencer_sample_shows_influencer_sample_channel(db_session: AsyncSession) -> None:
    channel = await _channel_for_order(
        db_session,
        order_number="CHAN-006",
        shopify_order_id="920006",
        shopify_tags=["Influencer Gifting"],
    )
    assert channel == "Influencer Sample"


# TEST 7
async def test_manual_order_shows_manual_order_channel(db_session: AsyncSession) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session,
        order_number="CHAN-007",
        customer=customer,
        status=OrderStatus.PENDING,
        shopify_order_id=None,
    )
    order.source_system = "manual"
    await db_session.flush()
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")
    assert response.status_code == 200
    assert response.json()["data"]["order_channel"] == "Manual Order"


# TEST 8
async def test_normal_shopify_order_shows_shopify_order_channel(db_session: AsyncSession) -> None:
    channel = await _channel_for_order(
        db_session, order_number="CHAN-008", shopify_order_id="920008", shopify_tags=[]
    )
    assert channel == "Shopify Order"


# TEST 9
async def test_unrecognized_tags_fall_back_to_shopify_order(db_session: AsyncSession) -> None:
    channel = await _channel_for_order(
        db_session,
        order_number="CHAN-009",
        shopify_order_id="920009",
        shopify_tags=["VIP", "Some Existing Tag"],
    )
    assert channel == "Shopify Order"


# TEST 9 (raw internal keys never leak to the client)
async def test_raw_internal_channel_keys_never_appear_in_the_response(
    db_session: AsyncSession,
) -> None:
    channel = await _channel_for_order(
        db_session,
        order_number="CHAN-009B",
        shopify_order_id="920010",
        shopify_tags=["Amazon FBA"],
    )
    assert channel not in {"amazon", "flipkart", "blinkit", "meesho", "distributor",
                            "influencer_sample", "shopify", "manual"}
    assert channel == "Amazon Order"


# TEST 10
async def test_telecaller_ownership_is_still_enforced_on_order_detail(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, other_telecaller, customer = await _setup(db_session)
    order = await make_order(
        db_session,
        order_number="CHAN-010",
        customer=customer,
        status=OrderStatus.PENDING,
        shopify_order_id="920011",
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, other_telecaller.id) as other_client:
        response = await other_client.get(f"/api/v1/telecaller/orders/{order.id}")
    assert response.status_code == 403


# TEST 11
async def test_order_detail_endpoint_still_returns_everything_else_unchanged(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session,
        order_number="CHAN-011",
        customer=customer,
        status=OrderStatus.PENDING,
        shopify_order_id=None,
    )
    order.source_system = "manual"
    await db_session.flush()
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["order_number"] == "CHAN-011"
    assert data["status"] == "pending"
    assert data["order_channel"] == "Manual Order"


# TEST 12
async def test_confirming_via_log_call_is_unaffected_by_the_channel_field(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session,
        order_number="CHAN-012",
        customer=customer,
        status=OrderStatus.PENDING,
        shopify_order_id=None,
    )
    order.source_system = "manual"
    await db_session.flush()
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls", json={"outcome": "confirmed"}
        )
        assert response.status_code == 201
        order_view = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")

    data = order_view.json()["data"]
    assert data["status"] == "confirmed"
    assert data["order_channel"] == "Manual Order"
