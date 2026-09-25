"""CRITICAL REVIEW FIX: selecting Call Status = "Confirmed" in the Log
Call modal and clicking Save must automatically confirm the OMS order
itself, not just the call-log/`TelecallingStatus`.

Root cause fixed: `TelecallingService.log_call` (the real, only, save path
behind `POST /telecaller/orders/{id}/calls` -- the endpoint the Log Call
modal, the list page's "Edit Call Status" dialog, and every quick-log
button all call, per `apps/web/services/telecaller.ts::useLogCall`) never
touched `Order.status` at all. These tests exercise that SAME endpoint --
never the separate `/confirm` endpoint alone -- to prove the real UI flow
is fixed, not just the pre-existing direct-confirm path.
"""

from __future__ import annotations

import pytest
from app.core.exceptions import IntegrationError
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
    leader = await make_user(db_session, email="leader@logcall.example.com", role=team_leader_role)
    telecaller = await make_user(
        db_session,
        email="tc@logcall.example.com",
        role=telecaller_role,
        team_leader_id=leader.id,
        name="Priya Sharma",
    )
    other_telecaller = await make_user(
        db_session,
        email="tc2@logcall.example.com",
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


async def test_log_call_confirmed_creates_the_call_log_and_confirms_the_order(
    db_session: AsyncSession,
) -> None:
    """TEST 1."""
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session, order_number="LOGCALL-001", customer=customer, status=OrderStatus.PENDING
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        # This is the REAL Log Call modal save path -- POST .../calls --
        # never the separate /confirm endpoint.
        response = await tc_client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls",
            json={"outcome": "confirmed", "notes": "Customer confirmed the order."},
        )
        assert response.status_code == 201
        assert response.json()["data"]["outcome"] == "confirmed"

        order_view = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")

    data = order_view.json()["data"]
    assert data["status"] == "confirmed"
    assert data["call_status"] == "confirmed"


async def test_log_call_confirmed_attributes_the_authenticated_telecaller(
    db_session: AsyncSession,
) -> None:
    """TEST 2. `confirmed_by_telecaller_id` must come from the
    authenticated actor server-side, never a client-supplied field (the
    request body above never even contains a telecaller id).
    """
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session, order_number="LOGCALL-002", customer=customer, status=OrderStatus.PENDING
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls", json={"outcome": "confirmed"}
        )
        assert response.status_code == 201
        order_view = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")

    data = order_view.json()["data"]
    assert data["confirmed_by_telecaller_id"] == str(telecaller.id)
    assert data["confirmed_at"] is not None


async def test_log_call_confirmed_order_enters_the_shipment_queue(
    db_session: AsyncSession,
) -> None:
    """TEST 3. `GET /shipments/queue` (`shipment_queue_query`) filters on
    `Order.status == CONFIRMED` -- confirming this exact, pre-existing
    filter now finds the order once Log Call -> Confirmed has run, with
    no second "confirmed" concept and no shipment-side code change.
    """
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session, order_number="LOGCALL-003", customer=customer, status=OrderStatus.PENDING
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls", json={"outcome": "confirmed"}
        )
        assert response.status_code == 201

    ops_role = await make_role(
        db_session, name="OPERATIONS", permission_codes=["shipments.read"]
    )
    ops_user = await make_user(db_session, email="ops@logcall.example.com", role=ops_role)
    async with bearer_client(app, get_db, db_session, ops_user.id) as ops_client:
        queue = await ops_client.get("/api/v1/shipments/queue", params={"page_size": 200})

    assert queue.status_code == 200
    queue_order_ids = {row["order_id"] for row in queue.json()["data"]}
    assert str(order.id) in queue_order_ids


@pytest.mark.parametrize(
    "outcome",
    [
        "call_attempted",
        "connected",
        "not_received",
        "busy",
        "switched_off",
        "invalid_number",
        "call_back_requested",
        "interested",
        "not_interested",
        "follow_up_required",
        "cancelled",
    ],
)
async def test_non_confirmed_call_outcomes_never_change_order_status(
    db_session: AsyncSession, outcome: str
) -> None:
    """TEST 4. Only `outcome == "confirmed"` may confirm the order --
    every other loggable `TelecallingStatus` value must leave
    `Order.status` exactly as it was.
    """
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session, order_number=f"LOGCALL-NC-{outcome}", customer=customer,
        status=OrderStatus.PENDING,
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls", json={"outcome": outcome}
        )
        assert response.status_code == 201
        order_view = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")

    data = order_view.json()["data"]
    assert data["status"] == "pending"
    assert data["confirmed_by_telecaller_id"] is None


async def test_logging_confirmed_again_on_an_already_confirmed_order_is_a_safe_no_op(
    db_session: AsyncSession,
) -> None:
    """TEST 5. A second "Confirmed" call outcome on an order that's
    already CONFIRMED must still save the call log (never a 409/500 back
    to the telecaller) and must NOT reset/duplicate confirmation
    attribution or timestamps.
    """
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session, order_number="LOGCALL-005", customer=customer, status=OrderStatus.PENDING
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        first = await tc_client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls", json={"outcome": "confirmed"}
        )
        assert first.status_code == 201
        first_view = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")
        first_confirmed_at = first_view.json()["data"]["confirmed_at"]

        # A second "Confirmed" call outcome -- must not fail or 409.
        second = await tc_client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls",
            json={"outcome": "confirmed", "notes": "Confirmed again by mistake."},
        )
        assert second.status_code == 201
        assert second.json()["data"]["attempt_number"] == 2

        second_view = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")
        history = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}/calls")

    data = second_view.json()["data"]
    assert data["status"] == "confirmed"
    assert data["confirmed_by_telecaller_id"] == str(telecaller.id)
    assert data["confirmed_at"] == first_confirmed_at  # untouched by the second call
    assert len(history.json()["data"]) == 2  # both attempts logged, no duplicates suppressed


async def test_telecaller_cannot_confirm_via_log_call_on_another_telecallers_order(
    db_session: AsyncSession,
) -> None:
    """TEST 6."""
    leader, telecaller, other_telecaller, customer = await _setup(db_session)
    order = await make_order(
        db_session, order_number="LOGCALL-006", customer=customer, status=OrderStatus.PENDING
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, other_telecaller.id) as other_client:
        response = await other_client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls", json={"outcome": "confirmed"}
        )
        assert response.status_code == 403

    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        order_view = await leader_client.get(f"/api/v1/team/orders/{order.id}")
    assert order_view.json()["data"]["status"] == "pending"


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


async def test_log_call_confirmed_succeeds_even_when_shopify_is_unconfigured(
    db_session: AsyncSession,
) -> None:
    """TEST 7 (part A): a manually-created/non-Shopify order (no
    `shopify_order_id`) -- OMS confirmation must succeed with zero Shopify
    calls, per the existing failure-isolation design.
    """
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session,
        order_number="LOGCALL-007A",
        customer=customer,
        status=OrderStatus.PENDING,
        shopify_order_id=None,
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls", json={"outcome": "confirmed"}
        )
        assert response.status_code == 201
        order_view = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")

    assert order_view.json()["data"]["status"] == "confirmed"


async def test_log_call_confirmed_succeeds_even_when_the_shopify_push_fails(
    db_session: AsyncSession,
) -> None:
    """TEST 7 (part B): a real Shopify order whose tag push fails outright
    -- OMS confirmation (via the Log Call save path) must still succeed;
    the Shopify failure is logged only, exactly like the direct `/confirm`
    endpoint's own existing contract.

    A CONFIRMED log now makes THREE independent, best-effort Shopify
    calls (confirmation tag `tagsAdd`, then the call-outcome/channel
    `sync_call_tags`'s own `tagsAdd` + `tagsRemove` -- see
    `ShopifyFulfillmentService.sync_call_tags`) -- every one of them can
    fail independently without affecting the others or the OMS state.
    """
    from app.integrations.registry import clear_adapters, register_adapter
    from app.integrations.shopify.adapter import ShopifyAdapter

    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session,
        order_number="LOGCALL-007B",
        customer=customer,
        status=OrderStatus.PENDING,
        shopify_order_id="900099",
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    client = _StubShopifyClient(
        [
            IntegrationError("Shopify is down.", details={}),
            IntegrationError("Shopify is down.", details={}),
            IntegrationError("Shopify is down.", details={}),
        ]
    )
    register_adapter(ShopifyAdapter(client=client))
    try:
        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            response = await tc_client.post(
                f"/api/v1/telecaller/orders/{order.id}/calls", json={"outcome": "confirmed"}
            )
            assert response.status_code == 201
            order_view = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")

        assert order_view.json()["data"]["status"] == "confirmed"
        assert len(client.calls) == 3
        assert "tagsAdd" in client.calls[0][0]  # confirmation tag
        assert "tagsAdd" in client.calls[1][0]  # call-outcome/channel tags
        assert "tagsRemove" in client.calls[2][0]  # stale outcome/channel tags cleared
    finally:
        clear_adapters()


async def test_direct_confirm_endpoint_still_works_unchanged(db_session: AsyncSession) -> None:
    """TEST 8: the pre-existing `POST /telecaller/orders/{id}/confirm`
    endpoint (an alternate entry point to the same canonical
    `OrderService.confirm_order`) must keep working exactly as before --
    the Log Call fix must not have displaced or duplicated it.
    """
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session, order_number="LOGCALL-008", customer=customer, status=OrderStatus.PENDING
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "confirmed"
