"""Live (API-first) telecalling workflow: worklists, call logging,
confirmation, order detail/timeline -- for orders that exist only in
Shopify -- plus historical-record compatibility and write prevention.

Shopify is MOCKED (router stub). Nothing here is verified against a real
Shopify store.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

import pytest
from app.core.config import settings
from app.db.session import get_db
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.main import app
from app.models.enums import OrderStatus
from app.models.order import Order, OrderEvent
from app.models.order_workflow import OrderWorkflow
from app.models.telecalling import CallAttempt, OrderAssignment
from app.repositories.telecalling import OrderAssignmentRepository
from app.services.live_orders import hydrate_order
from app.services.order_ref import encode_shopify_order_ref
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import bearer_client, make_order, make_role, make_user
from tests.test_live_orders_workflow import _assert_no_provider_rows, _node

pytestmark = pytest.mark.asyncio


class RouterStub:
    """Duck-typed Shopify client: serves orders by `id:<n>` search terms
    and acknowledges tag mutations."""

    def __init__(self, nodes: dict[int, dict]) -> None:
        self.nodes = nodes
        self.queries: list[str] = []
        self.tag_calls: list[tuple[str, list[str]]] = []

    async def execute(self, query: str, variables: dict | None = None) -> dict:
        variables = variables or {}
        if "mutation TagsAdd" in query:
            self.tag_calls.append(("add", variables["tags"]))
            return {"tagsAdd": {"userErrors": []}}
        if "mutation TagsRemove" in query:
            self.tag_calls.append(("remove", variables["tags"]))
            return {"tagsRemove": {"userErrors": []}}
        search = variables.get("query") or ""
        self.queries.append(search)
        wanted = {int(n) for n in re.findall(r"id:(\d+)", search)}
        picked = [n for k, n in self.nodes.items() if not wanted or k in wanted]
        return {
            "orders": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "edges": [{"node": n} for n in picked],
            }
        }


@pytest.fixture(autouse=True)
def _live(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "SHOPIFY_LIVE_MODE", True)
    yield
    clear_adapters()
    app.dependency_overrides.clear()


def _install(nodes: dict[int, dict]) -> RouterStub:
    stub = RouterStub(nodes)
    register_adapter(ShopifyAdapter(client=stub))
    return stub


async def _team(db_session: AsyncSession):
    lead_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    tc_role = await make_role(
        db_session,
        name="TELECALLER",
        permission_codes=["calls.manage", "orders.confirm", "orders.read"],
    )
    leader = await make_user(db_session, email="leader@example.com", role=lead_role)
    tc1 = await make_user(
        db_session, email="tc1@example.com", role=tc_role, team_leader_id=leader.id, name="Asha"
    )
    tc2 = await make_user(
        db_session, email="tc2@example.com", role=tc_role, team_leader_id=leader.id, name="Ravi"
    )
    return leader, tc1, tc2


async def _assign(db_session: AsyncSession, leader, telecaller, numbers: list[int]) -> None:
    async with bearer_client(app, get_db, db_session, leader.id) as client:
        response = await client.post(
            "/api/v1/team/orders/assign",
            json={
                "order_ids": [str(encode_shopify_order_ref(str(n))) for n in numbers],
                "mode": "manual",
                "telecaller_id": str(telecaller.id),
            },
        )
    assert response.status_code in (200, 201), response.text


def _nodes(*numbers: int) -> dict[int, dict]:
    nodes = {}
    for n in numbers:
        node = _node(n)
        node["email"] = f"buyer{n}@example.com"
        nodes[n] = node
    return nodes


# --- worklists ------------------------------------------------------------------------


async def test_telecaller_worklist_shows_live_orders_scoped_to_caller(
    db_session: AsyncSession,
) -> None:
    leader, tc1, tc2 = await _team(db_session)
    _install(_nodes(1, 2, 3))
    await _assign(db_session, leader, tc1, [1, 2])
    await _assign(db_session, leader, tc2, [3])

    async with bearer_client(app, get_db, db_session, tc1.id) as client:
        response = await client.get("/api/v1/telecaller/orders")

    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    assert sorted(r["order_number"] for r in rows) == ["#1001", "#1002"]  # not tc2's #1003
    assert all(r["customer_name"].startswith("Buyer") for r in rows)
    assert response.json()["meta"]["total_items"] == 2
    await _assert_no_provider_rows(db_session)


async def test_telecaller_worklist_filters_apply_in_memory(db_session: AsyncSession) -> None:
    leader, tc1, _ = await _team(db_session)
    nodes = _nodes(1, 2)
    nodes[2]["displayFulfillmentStatus"] = "FULFILLED"
    _install(nodes)
    await _assign(db_session, leader, tc1, [1, 2])

    async with bearer_client(app, get_db, db_session, tc1.id) as client:
        searched = await client.get("/api/v1/telecaller/orders", params={"q": "Buyer 2"})
        unfulfilled = await client.get(
            "/api/v1/telecaller/orders", params={"date_from": "2026-01-01T00:00:00+00:00"}
        )

    assert [r["order_number"] for r in searched.json()["data"]] == ["#1002"]
    assert unfulfilled.json()["meta"]["total_items"] == 2


async def test_assigned_order_detail_and_missing_shopify_order(db_session: AsyncSession) -> None:
    leader, tc1, _ = await _team(db_session)
    stub = _install(_nodes(1))
    await _assign(db_session, leader, tc1, [1])
    ref = encode_shopify_order_ref("1")

    async with bearer_client(app, get_db, db_session, tc1.id) as client:
        found = await client.get(f"/api/v1/telecaller/orders/{ref}")
        stub.nodes.clear()  # Shopify no longer returns the order
        gone = await client.get(f"/api/v1/telecaller/orders/{ref}")

    assert found.status_code == 200, found.text
    detail = found.json()["data"]
    assert detail["order_number"] == "#1001"
    assert detail["items"][0]["sku"] == "SKU-1"
    assert gone.status_code in (403, 404)  # never fabricated from stale data


# --- order detail -----------------------------------------------------------------------


async def test_order_detail_is_live_with_customer_email(db_session: AsyncSession) -> None:
    leader, tc1, _ = await _team(db_session)
    _install(_nodes(5))
    ref = encode_shopify_order_ref("5")

    async with bearer_client(app, get_db, db_session, tc1.id) as client:
        response = await client.get(f"/api/v1/orders/{ref}")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["order_number"] == "#1005"
    assert data["customer"]["email"] == "buyer5@example.com"
    assert data["status"] == "pending"
    await _assert_no_provider_rows(db_session)


async def test_order_detail_404_when_shopify_lacks_order(db_session: AsyncSession) -> None:
    _, tc1, _ = await _team(db_session)
    _install({})

    async with bearer_client(app, get_db, db_session, tc1.id) as client:
        response = await client.get(f"/api/v1/orders/{encode_shopify_order_ref('999')}")

    assert response.status_code == 404


# --- call logging + confirmation ------------------------------------------------------------


async def test_log_confirmed_call_confirms_live_order_and_tags_shopify(
    db_session: AsyncSession,
) -> None:
    leader, tc1, _ = await _team(db_session)
    stub = _install(_nodes(1))
    await _assign(db_session, leader, tc1, [1])
    ref = encode_shopify_order_ref("1")

    async with bearer_client(app, get_db, db_session, tc1.id) as client:
        response = await client.post(
            f"/api/v1/telecaller/orders/{ref}/calls",
            json={"outcome": "confirmed", "notes": "spoke to buyer"},
        )
        detail = await client.get(f"/api/v1/orders/{ref}")

    assert response.status_code == 201, response.text
    attempt = (await db_session.execute(select(CallAttempt))).scalar_one()
    assert attempt.order_id == ref and attempt.notes == "spoke to buyer"
    workflow = (await db_session.execute(select(OrderWorkflow))).scalar_one()
    assert workflow.status == OrderStatus.CONFIRMED
    assert workflow.confirmed_by_telecaller_id == tc1.id
    assert detail.json()["data"]["status"] == "confirmed"
    assert detail.json()["data"]["confirmed_by_telecaller_name"] == "Asha"
    events = (await db_session.execute(select(OrderEvent))).scalars().all()
    assert events and all(e.order_id == workflow.id for e in events)
    assert any(kind == "add" and "Asha confirmed by OMS" in str(t) for kind, t in stub.tag_calls)
    await _assert_no_provider_rows(db_session)


async def test_confirm_then_unconfirm_endpoints_for_live_order(db_session: AsyncSession) -> None:
    leader, tc1, _ = await _team(db_session)
    stub = _install(_nodes(2))
    await _assign(db_session, leader, tc1, [2])
    ref = encode_shopify_order_ref("2")

    async with bearer_client(app, get_db, db_session, tc1.id) as client:
        confirmed = await client.post(f"/api/v1/telecaller/orders/{ref}/confirm")
        reverted = await client.post(f"/api/v1/telecaller/orders/{ref}/unconfirm")

    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["data"]["status"] == "confirmed"
    assert reverted.status_code == 200, reverted.text
    assert reverted.json()["data"]["status"] == "pending"
    workflow = (await db_session.execute(select(OrderWorkflow))).scalar_one()
    assert workflow.status == OrderStatus.PENDING
    assert workflow.confirmed_by_telecaller_id is None
    assert any(kind == "remove" for kind, _ in stub.tag_calls)


async def test_live_order_cannot_be_confirmed_by_another_telecaller(
    db_session: AsyncSession,
) -> None:
    leader, tc1, tc2 = await _team(db_session)
    _install(_nodes(1))
    await _assign(db_session, leader, tc1, [1])

    async with bearer_client(app, get_db, db_session, tc2.id) as client:
        response = await client.post(
            f"/api/v1/telecaller/orders/{encode_shopify_order_ref('1')}/confirm"
        )

    assert response.status_code in (403, 404)
    workflow = (await db_session.execute(select(OrderWorkflow))).scalar_one()
    assert workflow.status == OrderStatus.PENDING


async def test_status_transition_timeline_and_manual_event_for_live_order(
    db_session: AsyncSession,
) -> None:
    ops_role = await make_role(
        db_session,
        name="OPS",
        permission_codes=["orders.read", "orders.update", "orders.cancel"],
    )
    ops = await make_user(db_session, email="ops@example.com", role=ops_role)
    _install(_nodes(7))
    ref = encode_shopify_order_ref("7")

    async with bearer_client(app, get_db, db_session, ops.id) as client:
        event = await client.post(
            f"/api/v1/orders/{ref}/events",
            json={
                "event_type": "note",
                "status": None,
                "description": "called twice",
                "event_metadata": None,
            },
        )
        cancelled = await client.patch(f"/api/v1/orders/{ref}", json={"status": "cancelled"})
        timeline = await client.get(f"/api/v1/orders/{ref}/timeline")

    assert event.status_code == 201, event.text
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["data"]["status"] == "cancelled"
    types = [e["event_type"] for e in timeline.json()["data"]]
    assert "note" in types and "status_changed" in types
    workflow = (await db_session.execute(select(OrderWorkflow))).scalar_one()
    assert workflow.status == OrderStatus.CANCELLED


# --- integrity + write prevention --------------------------------------------------------------


async def test_live_order_objects_can_never_be_flushed(db_session: AsyncSession) -> None:
    _install(_nodes(1))
    from app.services.live_orders import get_reader

    data = await get_reader().get_order("1")
    order = hydrate_order(data, None)

    db_session.add(order)
    with pytest.raises(Exception, match="cannot be persisted"):
        await db_session.flush()
    await db_session.rollback()
    assert (await db_session.execute(select(func.count()).select_from(Order))).scalar_one() == 0


async def test_provider_owned_field_updates_are_refused_in_live_mode(
    db_session: AsyncSession,
) -> None:
    from app.core.exceptions import ValidationError
    from app.repositories.order import OrderRepository
    from app.services.live_orders import get_reader

    _install(_nodes(1))
    order = hydrate_order(await get_reader().get_order("1"), None)

    with pytest.raises(ValidationError, match="belong to Shopify"):
        await OrderRepository(db_session).update(order, total_amount=1)
    assert (
        await db_session.execute(select(func.count()).select_from(OrderWorkflow))
    ).scalar_one() == 0


# --- historical records ---------------------------------------------------------------------


async def test_historical_assignment_keeps_working_in_live_mode(
    db_session: AsyncSession,
) -> None:
    """A pre-migration order: local `orders` row + backfilled workflow row
    with the SAME id + an existing assignment. In live mode it is served
    from Shopify (source of truth) under its original id."""
    leader, tc1, _ = await _team(db_session)
    local = await make_order(db_session, order_number="#1001")
    local.shopify_order_id = "1"
    await db_session.commit()
    # make_order's dual-write already created the workflow row with local.id
    workflow = await db_session.get(OrderWorkflow, local.id)
    assert workflow is not None
    workflow.external_order_id = "1"
    await OrderAssignmentRepository(db_session).create(
        order_id=local.id,
        assigned_to=tc1.id,
        assigned_by=leader.id,
        assigned_at=datetime.now(UTC),
        team_leader_id=leader.id,
    )
    await db_session.commit()
    _install(_nodes(1))

    async with bearer_client(app, get_db, db_session, tc1.id) as client:
        worklist = await client.get("/api/v1/telecaller/orders")
        detail = await client.get(f"/api/v1/orders/{local.id}")

    assert worklist.status_code == 200, worklist.text
    assert [r["order_id"] for r in worklist.json()["data"]] == [str(local.id)]
    assert detail.json()["data"]["id"] == str(local.id)
    assert detail.json()["data"]["customer"]["email"] == "buyer1@example.com"
    # history untouched
    assert (
        await db_session.execute(select(func.count()).select_from(OrderAssignment))
    ).scalar_one() == 1
    assert await db_session.get(Order, local.id) is not None


async def test_legacy_orders_get_workflow_rows_when_live_mode_is_off(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SHOPIFY_LIVE_MODE", False)
    order = await make_order(db_session, order_number="ORD-LEGACY")

    workflow = await db_session.get(OrderWorkflow, order.id)
    assert workflow is not None
    assert workflow.external_order_id.startswith("local:") or workflow.external_order_id
    assert workflow.status == order.status

    order.status = OrderStatus.CANCELLED
    order.notes = "internal note"
    await db_session.commit()
    await db_session.refresh(workflow)
    assert workflow.status == OrderStatus.CANCELLED  # OMS state mirrored
    assert workflow.notes == "internal note"
    assert uuid.UUID(str(workflow.id)) == order.id


async def test_order_follow_up_can_be_scheduled_for_a_live_order(db_session: AsyncSession) -> None:
    leader, tc1, _ = await _team(db_session)
    _install(_nodes(1))
    await _assign(db_session, leader, tc1, [1])
    ref = encode_shopify_order_ref("1")

    async with bearer_client(app, get_db, db_session, tc1.id) as client:
        response = await client.post(
            f"/api/v1/telecaller/orders/{ref}/follow-up",
            json={"next_follow_up_at": "2030-01-01T10:00:00+00:00"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["next_follow_up_at"].startswith("2030-01-01")
