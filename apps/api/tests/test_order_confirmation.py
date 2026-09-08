"""Telecaller order confirmation (PENDING -> CONFIRMED) and bulk confirm.

`Order.status` (this file), `TelecallingStatus` (call outcome, untouched
here), `FulfillmentStatus`, and `ShipmentStatus` are four deliberately
separate concepts — these tests never assert one implies another.
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
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage", "orders.confirm"]
    )
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    leader = await make_user(db_session, email="leader@confirm.example.com", role=team_leader_role)
    telecaller = await make_user(
        db_session,
        email="tc@confirm.example.com",
        role=telecaller_role,
        team_leader_id=leader.id,
    )
    other_telecaller = await make_user(
        db_session,
        email="tc2@confirm.example.com",
        role=telecaller_role,
        team_leader_id=leader.id,
    )
    customer = await make_customer(db_session)
    return leader, telecaller, other_telecaller, customer


async def _assign(leader_client, order_id: str, telecaller_id: str) -> None:
    response = await leader_client.post(
        "/api/v1/team/orders/assign",
        json={"order_ids": [order_id], "mode": "manual", "telecaller_id": telecaller_id},
    )
    assert response.status_code == 201


async def test_confirm_pending_order_sets_status_and_attribution(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session, order_number="CONF-001", customer=customer, status=OrderStatus.PENDING
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "confirmed"
        assert data["confirmed_by_telecaller_id"] == str(telecaller.id)
        assert data["confirmed_at"] is not None


async def test_confirm_does_not_touch_call_status_or_fulfillment(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session, order_number="CONF-002", customer=customer, status=OrderStatus.PENDING
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")

        order_view = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")
        # call_status must still be its default -- confirming an order
        # never logs a call attempt or touches TelecallingStatus.
        assert order_view.json()["data"]["call_status"] == "not_called"
        assert order_view.json()["data"]["fulfillment_status"] == "unfulfilled"


async def test_confirm_rejects_order_not_assigned_to_caller(db_session: AsyncSession) -> None:
    leader, telecaller, other_telecaller, customer = await _setup(db_session)
    order = await make_order(
        db_session, order_number="CONF-003", customer=customer, status=OrderStatus.PENDING
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, other_telecaller.id) as other_client:
        response = await other_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")
        assert response.status_code == 403


async def test_confirm_already_confirmed_order_returns_409_not_500(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session, order_number="CONF-004", customer=customer, status=OrderStatus.PENDING
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        first = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")
        assert first.status_code == 200

        second = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")
        assert second.status_code == 409


async def test_confirm_unknown_order_returns_404_not_500(db_session: AsyncSession) -> None:
    _leader, telecaller, _other, _customer = await _setup(db_session)
    fake_id = "00000000-0000-0000-0000-000000000000"
    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.post(f"/api/v1/telecaller/orders/{fake_id}/confirm")
        assert response.status_code == 404


async def test_telecaller_without_orders_confirm_permission_gets_403(
    db_session: AsyncSession,
) -> None:
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage"]
    )
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    leader = await make_user(db_session, email="leader2@confirm.example.com", role=team_leader_role)
    telecaller = await make_user(
        db_session,
        email="tc-noperm@confirm.example.com",
        role=telecaller_role,
        team_leader_id=leader.id,
    )
    customer = await make_customer(db_session)
    order = await make_order(
        db_session, order_number="CONF-005", customer=customer, status=OrderStatus.PENDING
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")
        assert response.status_code == 403


async def test_bulk_confirm_reports_per_order_success_and_failure(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, other_telecaller, customer = await _setup(db_session)
    confirmable = await make_order(
        db_session, order_number="CONF-BULK-1", customer=customer, status=OrderStatus.PENDING
    )
    already_confirmed = await make_order(
        db_session, order_number="CONF-BULK-2", customer=customer, status=OrderStatus.CONFIRMED
    )
    not_mine = await make_order(
        db_session, order_number="CONF-BULK-3", customer=customer, status=OrderStatus.PENDING
    )

    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(confirmable.id), str(telecaller.id))
        await _assign(leader_client, str(already_confirmed.id), str(telecaller.id))
        await _assign(leader_client, str(not_mine.id), str(other_telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.post(
            "/api/v1/telecaller/orders/confirm",
            json={
                "order_ids": [str(confirmable.id), str(already_confirmed.id), str(not_mine.id)]
            },
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["confirmed_count"] == 1
        assert data["failed_count"] == 2
        results_by_order = {r["order_id"]: r for r in data["results"]}
        assert results_by_order[str(confirmable.id)]["success"] is True
        assert results_by_order[str(already_confirmed.id)]["success"] is False
        assert results_by_order[str(not_mine.id)]["success"] is False

        # The one legitimate confirmation must have actually persisted,
        # not been rolled back by the other two orders' failures.
        confirmed_view = await tc_client.get(f"/api/v1/telecaller/orders/{confirmable.id}")
        assert confirmed_view.json()["data"]["status"] == "confirmed"
        assert confirmed_view.json()["data"]["confirmed_by_telecaller_id"] == str(telecaller.id)


async def test_bulk_confirm_duplicate_request_is_safe(db_session: AsyncSession) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session, order_number="CONF-DUP-1", customer=customer, status=OrderStatus.PENDING
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        first = await tc_client.post(
            "/api/v1/telecaller/orders/confirm", json={"order_ids": [str(order.id)]}
        )
        assert first.json()["data"]["confirmed_count"] == 1

        # Same order id sent twice in one request -- the second entry
        # must fail cleanly (already confirmed), not corrupt the first.
        second = await tc_client.post(
            "/api/v1/telecaller/orders/confirm",
            json={"order_ids": [str(order.id), str(order.id)]},
        )
        assert second.status_code == 200
        assert second.json()["data"]["confirmed_count"] == 0
        assert second.json()["data"]["failed_count"] == 2


async def test_bulk_confirm_empty_selection_returns_422(db_session: AsyncSession) -> None:
    _leader, telecaller, _other, _customer = await _setup(db_session)
    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.post("/api/v1/telecaller/orders/confirm", json={"order_ids": []})
        assert response.status_code == 422


async def test_attribution_survives_reassignment(db_session: AsyncSession) -> None:
    leader, telecaller, other_telecaller, customer = await _setup(db_session)
    admin = await make_user(db_session, email="admin@confirm.example.com", is_superuser=True)
    order = await make_order(
        db_session, order_number="CONF-REASSIGN-1", customer=customer, status=OrderStatus.PENDING
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        confirm = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")
        assert confirm.status_code == 200

    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        reassign = await leader_client.post(
            "/api/v1/team/orders/reassign",
            json={
                "order_id": str(order.id),
                "new_telecaller_id": str(other_telecaller.id),
                "reason": "workload rebalance",
            },
        )
        assert reassign.status_code == 201

        order_view = await leader_client.get(f"/api/v1/team/orders/{order.id}")
        # The *current* assignee changed...
        assert order_view.json()["data"]["assigned_to"] == str(other_telecaller.id)

    async with bearer_client(app, get_db, db_session, admin.id) as admin_client:
        order_detail = await admin_client.get(f"/api/v1/orders/{order.id}")
        assert order_detail.status_code == 200
        # ...but who actually confirmed the order must not change.
        assert order_detail.json()["data"]["confirmed_by_telecaller_id"] == str(telecaller.id)


async def test_unconfirm_reverts_status_and_clears_attribution(db_session: AsyncSession) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session, order_number="UNCONF-001", customer=customer, status=OrderStatus.PENDING
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        confirm = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")
        assert confirm.status_code == 200

        response = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/unconfirm")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "pending"
        assert data["confirmed_by_telecaller_id"] is None
        assert data["confirmed_at"] is None


async def test_unconfirm_does_not_touch_call_status(db_session: AsyncSession) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session, order_number="UNCONF-002", customer=customer, status=OrderStatus.PENDING
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")
        await tc_client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls", json={"outcome": "confirmed"}
        )
        await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/unconfirm")

        order_view = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")
        # Reverting the order confirmation must never touch the logged
        # call outcome -- the two axes stay independent both ways.
        assert order_view.json()["data"]["call_status"] == "confirmed"
        assert order_view.json()["data"]["status"] == "pending"


async def test_unconfirm_rejects_order_not_assigned_to_caller(db_session: AsyncSession) -> None:
    leader, telecaller, other_telecaller, customer = await _setup(db_session)
    order = await make_order(
        db_session, order_number="UNCONF-003", customer=customer, status=OrderStatus.PENDING
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))
    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")

    async with bearer_client(app, get_db, db_session, other_telecaller.id) as other_client:
        response = await other_client.post(f"/api/v1/telecaller/orders/{order.id}/unconfirm")
        assert response.status_code == 403


async def test_unconfirm_pending_order_returns_409_not_500(db_session: AsyncSession) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session, order_number="UNCONF-004", customer=customer, status=OrderStatus.PENDING
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        # Never confirmed -- nothing to revert.
        response = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/unconfirm")
        assert response.status_code == 409


async def test_unconfirm_blocked_once_a_shipment_exists(db_session: AsyncSession) -> None:
    from app.models.enums import ShipmentStatus
    from app.models.shipment import Shipment

    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session, order_number="UNCONF-005", customer=customer, status=OrderStatus.PENDING
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")

        db_session.add(
            Shipment(
                order_id=order.id, current_status=ShipmentStatus.PENDING, source_system="manual"
            )
        )
        await db_session.commit()

        response = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/unconfirm")
        assert response.status_code == 409

        # The order must still be confirmed -- the block must not have
        # partially applied.
        order_view = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")
        assert order_view.json()["data"]["status"] == "confirmed"
