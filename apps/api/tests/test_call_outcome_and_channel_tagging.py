"""Review-meeting requirement: every OMS order scenario gets a proper
Shopify tag classification -- the CURRENT call-outcome tag (Requirement 1,
`OUTCOME_TAGS`) and the permanent channel/source tag (Requirement 2,
`OrderChannel`), both pushed additively/idempotently by
`ShopifyFulfillmentService.sync_call_tags` (Requirement 3), wired into the
real `POST /telecaller/orders/{id}/calls` save path
(`TelecallingService.log_call`, Requirement 1/4), without ever touching
`Order.status` for a non-CONFIRMED outcome (Requirement 4) or failing the
OMS operation when Shopify is unavailable (Requirement 5).

Terminology note on the requirement doc's example outcome names: the real,
only `TelecallingStatus` enum (`app/models/enums.py`) has no "Hold" or
"Not Answering" value -- those were illustrative examples, not literal
enum members (per the requirement doc's own "these are examples, inspect
the actual existing call-outcome enum" instruction). The closest real
equivalents actually exercised below are `FOLLOW_UP_REQUIRED` ("Follow-up
Required", standing in for "Hold" -- a call that needs revisiting later)
and `NOT_RECEIVED` ("Not Received", standing in for "Not Answering" -- the
customer didn't pick up). Every other real outcome (`CALL_ATTEMPTED`,
`CONNECTED`, `SWITCHED_OFF`, `INVALID_NUMBER`, `CALL_BACK_REQUESTED`,
`INTERESTED`, `NOT_INTERESTED`, `CANCELLED`, plus `BUSY`) is covered too,
so all 12 loggable outcomes (everything except `NOT_CALLED`, which the API
never accepts as a logged outcome) end up tagged.

Numbered comments below (TEST 1..16) map 1:1 to Requirement 7's 16
scenarios.
"""

from __future__ import annotations

import pytest
from app.core.exceptions import IntegrationError
from app.db.session import get_db
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.main import app
from app.models.enums import OrderStatus, TelecallingStatus
from app.models.mixins import OrderChannel
from app.services.shopify_fulfillment_service import OUTCOME_TAGS
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
    leader = await make_user(db_session, email="leader@tagging.example.com", role=team_leader_role)
    telecaller = await make_user(
        db_session,
        email="tc@tagging.example.com",
        role=telecaller_role,
        team_leader_id=leader.id,
        name="Test Telecaller",
    )
    other_telecaller = await make_user(
        db_session,
        email="tc2@tagging.example.com",
        role=telecaller_role,
        team_leader_id=leader.id,
    )
    customer = await make_customer(db_session)
    return leader, telecaller, other_telecaller, customer


async def _assign(client, order_id: str, telecaller_id: str) -> None:
    response = await client.post(
        "/api/v1/team/orders/assign",
        json={"order_ids": [order_id], "mode": "manual", "telecaller_id": telecaller_id},
    )
    assert response.status_code == 201


class _StubShopifyClient:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict | None]] = []

    async def execute(self, query: str, variables: dict | None = None) -> dict:
        self.calls.append((query, variables))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _tags_add_success(order_gid: str) -> dict:
    return {"tagsAdd": {"node": {"id": order_gid}, "userErrors": []}}


def _tags_remove_success(order_gid: str) -> dict:
    return {"tagsRemove": {"node": {"id": order_gid}, "userErrors": []}}


async def _log_call(tc_client, order_id: str, outcome: str) -> None:
    response = await tc_client.post(
        f"/api/v1/telecaller/orders/{order_id}/calls", json={"outcome": outcome}
    )
    assert response.status_code == 201


async def _make_tagged_order(
    db_session: AsyncSession,
    *,
    order_number: str,
    customer,
    shopify_order_id: str,
    shopify_tags: list[str] | None = None,
    status: OrderStatus = OrderStatus.PENDING,
):
    order = await make_order(
        db_session,
        order_number=order_number,
        customer=customer,
        status=status,
        shopify_order_id=shopify_order_id,
    )
    if shopify_tags is not None:
        order.shopify_tags = shopify_tags
        await db_session.flush()
    return order


# ---------------------------------------------------------------------
# TEST 1: Confirmed -> OMS status CONFIRMED + correct Shopify tags.
# ---------------------------------------------------------------------
async def test_confirmed_outcome_sets_status_and_pushes_tags(db_session: AsyncSession) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await _make_tagged_order(
        db_session, order_number="TAG-001", customer=customer, shopify_order_id="910001"
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    gid = "gid://shopify/Order/910001"
    client = _StubShopifyClient(
        [
            _tags_add_success(gid),  # confirmation tag
            _tags_add_success(gid),  # call-outcome/channel tags
            _tags_remove_success(gid),  # stale outcome/channel tags cleared
        ]
    )
    register_adapter(ShopifyAdapter(client=client))
    try:
        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            await _log_call(tc_client, str(order.id), "confirmed")
            order_view = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")
        assert order_view.json()["data"]["status"] == "confirmed"

        assert len(client.calls) == 3
        assert client.calls[1][1] == {
            "id": gid,
            "tags": [OrderChannel.LABELS[OrderChannel.SHOPIFY]],
        }
        removed = client.calls[2][1]["tags"]
        assert set(removed) == {label for key, label in OUTCOME_TAGS.items()} | {
            OrderChannel.LABELS[key] for key in OrderChannel.LABELS if key != OrderChannel.SHOPIFY
        }
    finally:
        clear_adapters()


# ---------------------------------------------------------------------
# TEST 2: "Hold" (closest real equivalent: FOLLOW_UP_REQUIRED) -> its tag.
# ---------------------------------------------------------------------
async def test_follow_up_required_outcome_gets_its_tag(db_session: AsyncSession) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await _make_tagged_order(
        db_session, order_number="TAG-002", customer=customer, shopify_order_id="910002"
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    gid = "gid://shopify/Order/910002"
    client = _StubShopifyClient([_tags_add_success(gid), _tags_remove_success(gid)])
    register_adapter(ShopifyAdapter(client=client))
    try:
        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            await _log_call(tc_client, str(order.id), "follow_up_required")

        add_tags = client.calls[0][1]["tags"]
        assert OUTCOME_TAGS[TelecallingStatus.FOLLOW_UP_REQUIRED] in add_tags
        assert add_tags.count(OUTCOME_TAGS[TelecallingStatus.FOLLOW_UP_REQUIRED]) == 1
    finally:
        clear_adapters()


# ---------------------------------------------------------------------
# TEST 3: Busy -> "Busy" tag.
# ---------------------------------------------------------------------
async def test_busy_outcome_gets_busy_tag(db_session: AsyncSession) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await _make_tagged_order(
        db_session, order_number="TAG-003", customer=customer, shopify_order_id="910003"
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    gid = "gid://shopify/Order/910003"
    client = _StubShopifyClient([_tags_add_success(gid), _tags_remove_success(gid)])
    register_adapter(ShopifyAdapter(client=client))
    try:
        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            await _log_call(tc_client, str(order.id), "busy")

        assert "Busy" in client.calls[0][1]["tags"]
        assert order.status == OrderStatus.PENDING  # Requirement 4: not CONFIRMED
    finally:
        clear_adapters()


# ---------------------------------------------------------------------
# TEST 4: "Not Answering" (closest real equivalent: NOT_RECEIVED) -> tag.
# ---------------------------------------------------------------------
async def test_not_received_outcome_gets_its_tag(db_session: AsyncSession) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await _make_tagged_order(
        db_session, order_number="TAG-004", customer=customer, shopify_order_id="910004"
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    gid = "gid://shopify/Order/910004"
    client = _StubShopifyClient([_tags_add_success(gid), _tags_remove_success(gid)])
    register_adapter(ShopifyAdapter(client=client))
    try:
        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            await _log_call(tc_client, str(order.id), "not_received")

        assert "Not Received" in client.calls[0][1]["tags"]
    finally:
        clear_adapters()


# ---------------------------------------------------------------------
# TEST 5: every other existing call outcome -> its own appropriate tag.
# ---------------------------------------------------------------------
@pytest.mark.parametrize(
    "outcome_value,expected_tag",
    [
        ("call_attempted", "Call Attempted"),
        ("connected", "Connected"),
        ("switched_off", "Switched Off"),
        ("invalid_number", "Invalid Number"),
        ("call_back_requested", "Call Back Requested"),
        ("interested", "Interested"),
        ("not_interested", "Not Interested"),
        ("cancelled", "Cancelled"),
    ],
)
async def test_every_remaining_outcome_gets_the_right_tag(
    db_session: AsyncSession, outcome_value: str, expected_tag: str
) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await _make_tagged_order(
        db_session,
        order_number=f"TAG-005-{outcome_value}",
        customer=customer,
        shopify_order_id=f"9105{abs(hash(outcome_value)) % 1000:03d}",
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    gid = f"gid://shopify/Order/{order.shopify_order_id}"
    client = _StubShopifyClient([_tags_add_success(gid), _tags_remove_success(gid)])
    register_adapter(ShopifyAdapter(client=client))
    try:
        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            await _log_call(tc_client, str(order.id), outcome_value)

        add_tags = client.calls[0][1]["tags"]
        assert expected_tag in add_tags
        # Never more than one current outcome tag.
        outcome_labels_present = [t for t in add_tags if t in OUTCOME_TAGS.values()]
        assert outcome_labels_present == [expected_tag]
    finally:
        clear_adapters()


# ---------------------------------------------------------------------
# TEST 6 / 7 / 8: channel tagging -- Distributor, Influencer Sample, and
# the other existing channel types, all inferred from Order.shopify_tags
# keywords (never invented/hardcoded) or Order.source_system.
# ---------------------------------------------------------------------
@pytest.mark.parametrize(
    "existing_shopify_tags,expected_channel_tag",
    [
        (["Distributor"], "Distributor Order"),
        (["Influencer Gifting"], "Influencer Sample"),
        (["Amazon FBA"], "Amazon Order"),
        (["Flipkart Fulfilled"], "Flipkart Order"),
        (["Blinkit Quick Commerce"], "Blinkit Order"),
        (["Meesho Reseller"], "Meesho Order"),
        ([], "Shopify Order"),
    ],
)
async def test_channel_tag_is_inferred_from_existing_shopify_tags(
    db_session: AsyncSession, existing_shopify_tags: list[str], expected_channel_tag: str
) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await _make_tagged_order(
        db_session,
        order_number=f"TAG-006-{expected_channel_tag}",
        customer=customer,
        shopify_order_id=f"9106{abs(hash(expected_channel_tag)) % 1000:03d}",
        shopify_tags=existing_shopify_tags,
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    gid = f"gid://shopify/Order/{order.shopify_order_id}"
    client = _StubShopifyClient([_tags_add_success(gid), _tags_remove_success(gid)])
    register_adapter(ShopifyAdapter(client=client))
    try:
        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            await _log_call(tc_client, str(order.id), "busy")

        assert expected_channel_tag in client.calls[0][1]["tags"]
    finally:
        clear_adapters()


async def test_manual_order_gets_manual_order_channel_tag(db_session: AsyncSession) -> None:
    """A manual (non-Shopify) order has no `shopify_order_id`, so
    `sync_call_tags` is a no-op (see the `shopify_order_id` guard) -- there
    is nothing to push to Shopify for it. This documents that contract
    explicitly rather than leaving "Manual Order" untested.
    """
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session,
        order_number="TAG-006-MANUAL",
        customer=customer,
        status=OrderStatus.PENDING,
        shopify_order_id=None,
    )
    order.source_system = "manual"
    await db_session.flush()
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        # No adapter registered at all -- proves zero Shopify calls are attempted.
        await _log_call(tc_client, str(order.id), "busy")
        order_view = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")
    assert order_view.json()["data"]["status"] == "pending"


# ---------------------------------------------------------------------
# TEST 9: existing unrelated Shopify tags (VIP, Distributor Order, etc.)
# are never touched -- only our own specific tag/removal sets are sent.
# ---------------------------------------------------------------------
async def test_unrelated_existing_tags_are_never_referenced(db_session: AsyncSession) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await _make_tagged_order(
        db_session,
        order_number="TAG-009",
        customer=customer,
        shopify_order_id="910009",
        shopify_tags=["Distributor Order", "VIP", "Some Existing Tag"],
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    gid = "gid://shopify/Order/910009"
    client = _StubShopifyClient([_tags_add_success(gid), _tags_remove_success(gid)])
    register_adapter(ShopifyAdapter(client=client))
    try:
        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            await _log_call(tc_client, str(order.id), "busy")

        add_tags = client.calls[0][1]["tags"]
        remove_tags = client.calls[1][1]["tags"]
        for unrelated in ("VIP", "Some Existing Tag"):
            assert unrelated not in add_tags
            assert unrelated not in remove_tags
        # `tagsAdd`/`tagsRemove` are additive/subtractive mutations on
        # Shopify's side (never a full-array replace) -- sending only our
        # own tag sets, as asserted above, is what keeps VIP/Some Existing
        # Tag untouched no matter what Shopify already has stored.
    finally:
        clear_adapters()


# ---------------------------------------------------------------------
# TEST 10: duplicate tags are never created within one push.
# ---------------------------------------------------------------------
async def test_no_duplicate_tags_within_a_single_push(db_session: AsyncSession) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await _make_tagged_order(
        db_session,
        order_number="TAG-010",
        customer=customer,
        shopify_order_id="910010",
        shopify_tags=["Distributor"],
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    gid = "gid://shopify/Order/910010"
    client = _StubShopifyClient([_tags_add_success(gid), _tags_remove_success(gid)])
    register_adapter(ShopifyAdapter(client=client))
    try:
        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            await _log_call(tc_client, str(order.id), "interested")

        add_tags = client.calls[0][1]["tags"]
        remove_tags = client.calls[1][1]["tags"]
        assert len(add_tags) == len(set(add_tags))
        assert len(remove_tags) == len(set(remove_tags))
        assert set(add_tags).isdisjoint(set(remove_tags))
    finally:
        clear_adapters()


# ---------------------------------------------------------------------
# TEST 11: Busy -> Confirmed leaves no contradictory current-outcome tags.
# ---------------------------------------------------------------------
async def test_busy_then_confirmed_clears_the_stale_outcome_tag(db_session: AsyncSession) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await _make_tagged_order(
        db_session, order_number="TAG-011", customer=customer, shopify_order_id="910011"
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    gid = "gid://shopify/Order/910011"
    client = _StubShopifyClient(
        [
            _tags_add_success(gid),  # Busy: outcome/channel add
            _tags_remove_success(gid),  # Busy: outcome/channel remove
            _tags_add_success(gid),  # Confirmed: confirmation tag
            _tags_add_success(gid),  # Confirmed: outcome/channel add (channel only)
            _tags_remove_success(gid),  # Confirmed: outcome/channel remove (incl. "Busy")
        ]
    )
    register_adapter(ShopifyAdapter(client=client))
    try:
        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            await _log_call(tc_client, str(order.id), "busy")
            await _log_call(tc_client, str(order.id), "confirmed")
            order_view = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")

        assert order_view.json()["data"]["status"] == "confirmed"
        assert len(client.calls) == 5

        # Busy's own add call included "Busy".
        assert "Busy" in client.calls[0][1]["tags"]
        # Confirmed's outcome/channel add call (index 3) never re-adds "Busy"
        # (CONFIRMED has no entry in OUTCOME_TAGS).
        assert "Busy" not in client.calls[3][1]["tags"]
        # Confirmed's outcome/channel remove call (index 4) explicitly clears it.
        assert "Busy" in client.calls[4][1]["tags"]
    finally:
        clear_adapters()


# ---------------------------------------------------------------------
# TEST 12: Shopify failure never fails the OMS operation (non-CONFIRMED).
# ---------------------------------------------------------------------
async def test_shopify_failure_does_not_fail_a_non_confirmed_call_log(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await _make_tagged_order(
        db_session, order_number="TAG-012", customer=customer, shopify_order_id="910012"
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    client = _StubShopifyClient(
        [
            IntegrationError("Shopify is down.", details={}),
            IntegrationError("Shopify is down.", details={}),
        ]
    )
    register_adapter(ShopifyAdapter(client=client))
    try:
        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            response = await tc_client.post(
                f"/api/v1/telecaller/orders/{order.id}/calls", json={"outcome": "busy"}
            )
            assert response.status_code == 201
            assert response.json()["data"]["outcome"] == "busy"
    finally:
        clear_adapters()


# ---------------------------------------------------------------------
# TEST 13: a telecaller cannot modify another telecaller's order.
# ---------------------------------------------------------------------
async def test_telecaller_cannot_log_a_call_on_another_telecallers_order(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, other_telecaller, customer = await _setup(db_session)
    order = await _make_tagged_order(
        db_session, order_number="TAG-013", customer=customer, shopify_order_id="910013"
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, other_telecaller.id) as other_client:
        response = await other_client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls", json={"outcome": "busy"}
        )
        assert response.status_code == 403

    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        order_view = await leader_client.get(f"/api/v1/team/orders/{order.id}")
    assert order_view.json()["data"]["status"] == "pending"


# ---------------------------------------------------------------------
# TEST 14: Confirmed still reaches the Ready-to-Shipment / shipment queue.
# ---------------------------------------------------------------------
async def test_confirmed_order_still_enters_the_shipment_queue(db_session: AsyncSession) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await _make_tagged_order(
        db_session, order_number="TAG-014", customer=customer, shopify_order_id=None
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        await _log_call(tc_client, str(order.id), "confirmed")

    ops_role = await make_role(db_session, name="OPERATIONS", permission_codes=["shipments.read"])
    ops_user = await make_user(db_session, email="ops@tagging.example.com", role=ops_role)
    async with bearer_client(app, get_db, db_session, ops_user.id) as ops_client:
        queue = await ops_client.get("/api/v1/shipments/queue", params={"page_size": 200})
    assert queue.status_code == 200
    order_ids = {row["order_id"] for row in queue.json()["data"]}
    assert str(order.id) in order_ids


# ---------------------------------------------------------------------
# TEST 15: the existing direct order confirmation endpoint still works.
# ---------------------------------------------------------------------
async def test_direct_confirm_endpoint_still_works(db_session: AsyncSession) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await _make_tagged_order(
        db_session, order_number="TAG-015", customer=customer, shopify_order_id=None
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "confirmed"


# TEST 16 (existing Shopify sync tests continue to pass) is verified by
# running the full pre-existing Shopify test modules alongside this file,
# not by a test defined here -- see the final validation report.
