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


async def test_unconfirm_allowed_when_shipment_is_still_pending_and_cancels_it(
    db_session: AsyncSession,
) -> None:
    """Production bug fix: the gate is shipment PROGRESS, never mere row
    existence. A `Shipment` row that was never actually registered with
    Shiprocket (`source_system="manual"`, no `shiprocket_shipment_id` --
    e.g. created directly via `POST /shipments` rather than through the
    real Shiprocket push) has nothing external to cancel; the revert
    proceeds and that row is marked CANCELLED directly (see
    `test_unconfirm_cancels_the_real_shiprocket_shipment_first` below for
    the case where there IS a real external shipment to cancel first).
    """
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

        shipment = Shipment(
            order_id=order.id, current_status=ShipmentStatus.PENDING, source_system="manual"
        )
        db_session.add(shipment)
        await db_session.commit()
        await db_session.refresh(shipment)

        response = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/unconfirm")
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "pending"
        assert response.json()["data"]["confirmed_by_telecaller_id"] is None

        await db_session.refresh(shipment)
        assert shipment.current_status == ShipmentStatus.CANCELLED

        # The revert cancels the existing row -- it never creates a second
        # one alongside it.
        from sqlalchemy import select

        all_shipments = (
            await db_session.execute(select(Shipment).where(Shipment.order_id == order.id))
        ).scalars().all()
        assert len(all_shipments) == 1


async def test_unconfirm_allowed_when_the_only_shipment_is_already_cancelled(
    db_session: AsyncSession,
) -> None:
    from app.models.enums import ShipmentStatus
    from app.models.shipment import Shipment

    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session, order_number="UNCONF-006", customer=customer, status=OrderStatus.PENDING
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")

        db_session.add(
            Shipment(
                order_id=order.id, current_status=ShipmentStatus.CANCELLED, source_system="manual"
            )
        )
        await db_session.commit()

        response = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/unconfirm")
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "pending"


async def test_unconfirm_blocked_once_shipment_has_progressed_past_pending(
    db_session: AsyncSession,
) -> None:
    """The one case that must always stay blocked: fulfillment may
    already be physically handling the order, and Shiprocket itself will
    not accept a cancellation at this point either.
    """
    from app.models.enums import ShipmentStatus
    from app.models.shipment import Shipment

    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session, order_number="UNCONF-007", customer=customer, status=OrderStatus.PENDING
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")

        db_session.add(
            Shipment(
                order_id=order.id,
                current_status=ShipmentStatus.IN_TRANSIT,
                source_system="manual",
            )
        )
        await db_session.commit()

        response = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/unconfirm")
        assert response.status_code == 409
        # Specific reason, never the old generic "a shipment already exists"
        # message that blocked reverting any order with a shipment row at all.
        assert (
            response.json()["error"]["message"]
            == "Cannot revert this order because the shipment is already in transit."
        )

        # The order must still be confirmed -- the block must not have
        # partially applied.
        order_view = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")
        assert order_view.json()["data"]["status"] == "confirmed"


async def test_unconfirm_cancels_the_real_shiprocket_shipment_first(
    db_session: AsyncSession,
) -> None:
    """The important end-to-end case: a shipment genuinely registered
    with Shiprocket (has a `shiprocket_shipment_id`), still `PENDING` (not
    picked up). Revert must call the SAME `ShiprocketOperationsService.
    cancel_shipment` the shipment detail page's own "Cancel Shipment"
    button uses -- never a second, local-only copy -- and only proceed to
    revert the order once that external call actually succeeds.
    """
    from app.core.config import settings
    from app.integrations.registry import clear_adapters, register_adapter
    from app.integrations.shiprocket.adapter import ShiprocketAdapter
    from app.models.enums import ShipmentStatus
    from app.models.shipment import Shipment

    monkeypatch_targets = [
        ("SHIPROCKET_EMAIL", "ops@example.com"),
        ("SHIPROCKET_PASSWORD", "secret"),
        ("SHIPROCKET_PICKUP_LOCATION", "Main Warehouse"),
    ]
    originals = {name: getattr(settings, name) for name, _ in monkeypatch_targets}
    for name, value in monkeypatch_targets:
        setattr(settings, name, value)

    class _StubClient:
        def __init__(self, responses: list) -> None:
            self._responses = list(responses)
            self.calls: list[tuple[str, str]] = []

        async def request(self, method, path, *, json=None, params=None):  # noqa: ANN001
            self.calls.append((method, path))
            return self._responses.pop(0)

        async def ensure_authenticated(self) -> None:
            pass

    stub_client = _StubClient([{"message": "Shipment cancelled."}])
    register_adapter(ShiprocketAdapter(client=stub_client))

    try:
        leader, telecaller, _other, customer = await _setup(db_session)
        order = await make_order(
            db_session, order_number="UNCONF-008", customer=customer, status=OrderStatus.PENDING
        )
        async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
            await _assign(leader_client, str(order.id), str(telecaller.id))

        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")

            shipment = Shipment(
                order_id=order.id,
                current_status=ShipmentStatus.PENDING,
                source_system="shiprocket",
                shiprocket_shipment_id="5555",
            )
            db_session.add(shipment)
            await db_session.commit()
            await db_session.refresh(shipment)

            response = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/unconfirm")
            assert response.status_code == 200
            assert response.json()["data"]["status"] == "pending"

            # The real Shiprocket cancel endpoint was actually called.
            assert any("cancel" in path for _method, path in stub_client.calls)

            await db_session.refresh(shipment)
            assert shipment.current_status == ShipmentStatus.CANCELLED
    finally:
        clear_adapters()
        for name, value in originals.items():
            setattr(settings, name, value)


async def test_unconfirm_stays_confirmed_when_shiprocket_cancellation_fails(
    db_session: AsyncSession,
) -> None:
    """Never revert to PENDING while Shiprocket still has an active
    shipment -- if the external cancel call itself fails, the order must
    stay CONFIRMED exactly as if unconfirm was never called.
    """
    from app.core.config import settings
    from app.integrations.registry import clear_adapters, register_adapter
    from app.integrations.shiprocket.adapter import ShiprocketAdapter
    from app.integrations.shiprocket.errors import ShiprocketApiError
    from app.models.enums import ShipmentStatus
    from app.models.shipment import Shipment

    monkeypatch_targets = [
        ("SHIPROCKET_EMAIL", "ops@example.com"),
        ("SHIPROCKET_PASSWORD", "secret"),
        ("SHIPROCKET_PICKUP_LOCATION", "Main Warehouse"),
    ]
    originals = {name: getattr(settings, name) for name, _ in monkeypatch_targets}
    for name, value in monkeypatch_targets:
        setattr(settings, name, value)

    class _StubClient:
        def __init__(self, responses: list) -> None:
            self._responses = list(responses)

        async def request(self, method, path, *, json=None, params=None):  # noqa: ANN001
            response = self._responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        async def ensure_authenticated(self) -> None:
            pass

    stub_client = _StubClient(
        [ShiprocketApiError("Shipment already picked up.", error_type="validation_error")]
    )
    register_adapter(ShiprocketAdapter(client=stub_client))

    try:
        leader, telecaller, _other, customer = await _setup(db_session)
        order = await make_order(
            db_session, order_number="UNCONF-009", customer=customer, status=OrderStatus.PENDING
        )
        async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
            await _assign(leader_client, str(order.id), str(telecaller.id))

        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")

            shipment = Shipment(
                order_id=order.id,
                current_status=ShipmentStatus.PENDING,
                source_system="shiprocket",
                shiprocket_shipment_id="5556",
            )
            db_session.add(shipment)
            await db_session.commit()

            response = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/unconfirm")
            # Re-raised as a plain 409 with a clear, specific reason -- never
            # Shiprocket's own raw 502 IntegrationError text.
            assert response.status_code == 409
            assert (
                response.json()["error"]["message"]
                == "Shiprocket cancellation failed. The order was not reverted."
            )

            order_view = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")
            assert order_view.json()["data"]["status"] == "confirmed"

            await db_session.refresh(shipment)
            assert shipment.current_status == ShipmentStatus.PENDING
    finally:
        clear_adapters()
        for name, value in originals.items():
            setattr(settings, name, value)


@pytest.mark.parametrize(
    ("shipment_status", "expected_message"),
    [
        (
            "out_for_delivery",
            "Cannot revert this order because the shipment is already out for delivery.",
        ),
        (
            "delivered",
            "Cannot revert this order because the shipment has already been delivered.",
        ),
        (
            "picked_up",
            "Cannot revert this order because the shipment has already been picked up.",
        ),
        (
            "rto_initiated",
            "Cannot revert this order because the shipment is already in an RTO (return) flow.",
        ),
    ],
)
async def test_unconfirm_blocked_messages_match_the_actual_shipment_status(
    db_session: AsyncSession, shipment_status: str, expected_message: str
) -> None:
    """Each blocked shipment state gets its own specific reason -- never
    the generic "a shipment already exists" message that made every
    CONFIRMED order with any shipment look permanently non-reversible.
    """
    from app.models.enums import ShipmentStatus
    from app.models.shipment import Shipment

    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session,
        order_number=f"UNCONF-STATUS-{shipment_status}",
        customer=customer,
        status=OrderStatus.PENDING,
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")

        db_session.add(
            Shipment(
                order_id=order.id,
                current_status=ShipmentStatus(shipment_status),
                source_system="manual",
            )
        )
        await db_session.commit()

        response = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/unconfirm")
        assert response.status_code == 409
        assert response.json()["error"]["message"] == expected_message

        order_view = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")
        assert order_view.json()["data"]["status"] == "confirmed"


async def test_unconfirm_blocked_when_shopify_fulfillment_already_synced(
    db_session: AsyncSession,
) -> None:
    """A shipment can be Shiprocket-`PENDING` (not yet picked up) while
    already `shopify_sync_status=SYNCED` -- the outbound Shopify push
    fires on AWB assignment, independent of courier tracking. Reverting
    here would leave a real Shopify `Fulfillment` behind with no OMS
    order to match it, and `ShopifyFulfillmentService` has no cancel
    capability to undo it, so this must block outright.
    """
    from app.models.enums import ShipmentStatus, ShopifySyncStatus
    from app.models.shipment import Shipment

    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session, order_number="UNCONF-SHOPIFY-001", customer=customer, status=OrderStatus.PENDING
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")

        shipment = Shipment(
            order_id=order.id,
            current_status=ShipmentStatus.PENDING,
            source_system="shiprocket",
            shiprocket_shipment_id="7001",
            shopify_sync_status=ShopifySyncStatus.SYNCED,
        )
        db_session.add(shipment)
        await db_session.commit()

        response = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/unconfirm")
        assert response.status_code == 409
        assert "Shopify fulfillment has already been created" in response.json()["error"]["message"]

        order_view = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")
        assert order_view.json()["data"]["status"] == "confirmed"

        # Blocked before any Shiprocket cancellation was even attempted.
        await db_session.refresh(shipment)
        assert shipment.current_status == ShipmentStatus.PENDING


async def test_unconfirm_blocked_when_shopify_already_shows_order_fulfilled(
    db_session: AsyncSession,
) -> None:
    """An order Shopify fulfilled directly (never went through this OMS's
    own shipment pipeline at all -- no local `Shipment` row to inspect)
    must still block the revert: `Order.fulfillment_status` is Shopify's
    own inbound summary, independent of any local `Shipment` row.
    """
    from app.models.enums import FulfillmentStatus

    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session,
        order_number="UNCONF-SHOPIFY-002",
        customer=customer,
        status=OrderStatus.PENDING,
        fulfillment_status=FulfillmentStatus.FULFILLED,
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")

        response = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/unconfirm")
        assert response.status_code == 409
        assert (
            response.json()["error"]["message"]
            == "Cannot revert this order because Shopify already shows it as fulfilled."
        )

        order_view = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")
        assert order_view.json()["data"]["status"] == "confirmed"


class _StubShopifyClient:
    """Matches `ShopifyClient.execute`'s interface -- see
    `test_shopify_fulfillment.py` for the identical pattern used against
    the outbound fulfillment push. IMPORTANT CORRECTION (see this file's
    git history): confirming now issues EXACTLY ONE call (`tagsAdd`);
    unconfirming issues EXACTLY ONE call (`tagsRemove`) -- NEVER
    `fulfillmentCreate`/`fulfillmentCancel` for either. Tests below queue
    exactly the responses each scenario needs.
    """

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict | None]] = []

    async def execute(self, query: str, variables: dict | None = None) -> dict:
        self.calls.append((query, variables))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _tags_add_success(order_gid: str = "gid://shopify/Order/900001") -> dict:
    return {"tagsAdd": {"node": {"id": order_gid}, "userErrors": []}}


def _tags_remove_success(order_gid: str = "gid://shopify/Order/900001") -> dict:
    return {"tagsRemove": {"node": {"id": order_gid}, "userErrors": []}}


def _assert_no_fulfillment_calls(client: _StubShopifyClient) -> None:
    """Requirements 3/6: neither confirm nor unconfirm may ever call
    `fulfillmentCreate`/`fulfillmentCancel`.
    """
    assert not any(
        "fulfillmentCreate" in query or "fulfillmentCancel" in query
        for query, _variables in client.calls
    )


async def test_confirm_pushes_the_tag_and_never_creates_a_fulfillment(
    db_session: AsyncSession,
) -> None:
    """Requirements 1/2/3: confirming a Shopify-sourced order sets it
    CONFIRMED in the OMS and tags it `CONFIRMATION_TAG` in Shopify --
    NOTHING else on the Shopify side. No `fulfillmentCreate`, no
    fulfillment-status change: `Order.fulfillment_status` stays whatever
    it was locally (Shopify's own order remains Unfulfilled until real
    shipping happens).
    """
    from app.integrations.registry import clear_adapters, register_adapter
    from app.integrations.shopify.adapter import ShopifyAdapter
    from app.models.enums import FulfillmentStatus
    from app.models.shipment import Shipment
    from app.services.shopify_fulfillment_service import CONFIRMATION_TAG
    from sqlalchemy import select

    client = _StubShopifyClient([_tags_add_success()])
    register_adapter(ShopifyAdapter(client=client))
    try:
        leader, telecaller, _other, customer = await _setup(db_session)
        order = await make_order(
            db_session,
            order_number="UNCONF-SHOPIFY-A",
            customer=customer,
            status=OrderStatus.PENDING,
            fulfillment_status=FulfillmentStatus.UNFULFILLED,
            shopify_order_id="900001",
        )
        async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
            await _assign(leader_client, str(order.id), str(telecaller.id))

        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            response = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")
            assert response.status_code == 200
            data = response.json()["data"]
            assert data["status"] == "confirmed"
            assert data["fulfillment_status"] == "unfulfilled"

        # Exactly one Shopify call, and it's the tag.
        assert len(client.calls) == 1
        tag_query, tag_variables = client.calls[0]
        assert "tagsAdd" in tag_query
        assert tag_variables == {"id": "gid://shopify/Order/900001", "tags": [CONFIRMATION_TAG]}
        _assert_no_fulfillment_calls(client)

        await db_session.refresh(order)
        # The legacy confirmation-Fulfillment column (still present for
        # `sync_fulfillment_for_shipment`'s backward-compat fallback --
        # see `Order.shopify_confirmation_fulfillment_id`) is never set by
        # a tag-only confirm.
        assert order.shopify_confirmation_fulfillment_id is None

        # Still no Shiprocket shipment -- shipping is a separate, explicit
        # action, never triggered by confirming.
        shipments = (
            await db_session.execute(select(Shipment).where(Shipment.order_id == order.id))
        ).scalars().all()
        assert shipments == []
    finally:
        clear_adapters()


async def test_confirm_skips_shopify_push_for_a_manual_order(db_session: AsyncSession) -> None:
    """An order with no `shopify_order_id` (created directly in the OMS)
    has nothing to tag -- confirming it never calls Shopify at all.
    """
    from app.integrations.registry import clear_adapters, register_adapter
    from app.integrations.shopify.adapter import ShopifyAdapter

    client = _StubShopifyClient([])
    register_adapter(ShopifyAdapter(client=client))
    try:
        leader, telecaller, _other, customer = await _setup(db_session)
        order = await make_order(
            db_session,
            order_number="UNCONF-SHOPIFY-C",
            customer=customer,
            status=OrderStatus.PENDING,
            shopify_order_id=None,
        )
        async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
            await _assign(leader_client, str(order.id), str(telecaller.id))

        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            response = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")
            assert response.status_code == 200

        assert client.calls == []
    finally:
        clear_adapters()


async def test_confirm_succeeds_even_when_the_shopify_tag_push_fails(
    db_session: AsyncSession,
) -> None:
    """OMS confirmation is authoritative -- a Shopify-side failure while
    pushing the tag must never block, delay, or roll back an
    already-successful OMS confirmation. There is no per-order failure
    status for a tag-only push (see `sync_confirmation_tag`'s docstring)
    -- the next successful confirm or a manual retry naturally re-pushes
    it, and it's never a `fulfillmentCreate` call either way.
    """
    from app.core.exceptions import IntegrationError
    from app.integrations.registry import clear_adapters, register_adapter
    from app.integrations.shopify.adapter import ShopifyAdapter
    from app.services.shopify_fulfillment_service import CONFIRMATION_TAG

    client = _StubShopifyClient([IntegrationError("Shopify is down.", details={})])
    register_adapter(ShopifyAdapter(client=client))
    try:
        leader, telecaller, _other, customer = await _setup(db_session)
        order = await make_order(
            db_session,
            order_number="UNCONF-SHOPIFY-D",
            customer=customer,
            status=OrderStatus.PENDING,
            shopify_order_id="900004",
        )
        async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
            await _assign(leader_client, str(order.id), str(telecaller.id))

        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            response = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")
            assert response.status_code == 200
            assert response.json()["data"]["status"] == "confirmed"

        # A manual retry (OPERATIONS/ADMIN-only, see the RBAC test below)
        # re-pushes just the tag -- `tagsAdd` is a set-union, so this is
        # always safe to repeat.
        ops_role = await make_role(
            db_session, name="OPERATIONS", permission_codes=["orders.update"]
        )
        ops_user = await make_user(
            db_session, email="ops-retry-d@confirm.example.com", role=ops_role
        )
        client._responses = [_tags_add_success("gid://shopify/Order/900004")]
        async with bearer_client(app, get_db, db_session, ops_user.id) as ops_client:
            retry = await ops_client.post(
                f"/api/v1/orders/{order.id}/shopify/retry-confirmation-sync"
            )
            assert retry.status_code == 200

        assert len(client.calls) == 2
        assert "tagsAdd" in client.calls[0][0]
        assert "tagsAdd" in client.calls[1][0]
        assert client.calls[1][1] == {
            "id": "gid://shopify/Order/900004",
            "tags": [CONFIRMATION_TAG],
        }
        _assert_no_fulfillment_calls(client)
    finally:
        clear_adapters()


async def test_confirm_unconfirm_cycle_is_idempotent_tag_only(db_session: AsyncSession) -> None:
    """Requirement 9: re-confirming after an unconfirm just re-adds the
    tag (`tagsAdd` is a set-union, so this is always safe even if a
    previous `tagsRemove` never actually landed) -- never a
    `fulfillmentCreate`/`fulfillmentCancel` anywhere in the cycle. A third
    confirm attempt WITHOUT an intervening unconfirm is blocked by
    `transition_status` itself (already CONFIRMED) before the Shopify
    push is ever reached, so nothing duplicates there either.
    """
    from app.integrations.registry import clear_adapters, register_adapter
    from app.integrations.shopify.adapter import ShopifyAdapter
    from app.services.shopify_fulfillment_service import CONFIRMATION_TAG

    client = _StubShopifyClient(
        [
            _tags_add_success("gid://shopify/Order/900005"),
            _tags_remove_success("gid://shopify/Order/900005"),
            _tags_add_success("gid://shopify/Order/900005"),
        ]
    )
    register_adapter(ShopifyAdapter(client=client))
    try:
        leader, telecaller, _other, customer = await _setup(db_session)
        order = await make_order(
            db_session,
            order_number="UNCONF-SHOPIFY-E",
            customer=customer,
            status=OrderStatus.PENDING,
            shopify_order_id="900005",
        )
        async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
            await _assign(leader_client, str(order.id), str(telecaller.id))

        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            first = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")
            assert first.status_code == 200
            unconfirm = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/unconfirm")
            assert unconfirm.status_code == 200
            second = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")
            assert second.status_code == 200

        assert len(client.calls) == 3
        for query, variables in client.calls:
            assert "tagsAdd" in query or "tagsRemove" in query
            assert variables == {"id": "gid://shopify/Order/900005", "tags": [CONFIRMATION_TAG]}
        _assert_no_fulfillment_calls(client)

        # A third confirm attempt WITHOUT an intervening unconfirm never
        # reaches the Shopify push at all -- blocked by transition_status.
        client._responses = []
        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            third = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")
            assert third.status_code == 409
        assert client.calls[3:] == []
    finally:
        clear_adapters()


async def test_unconfirm_removes_the_tag_and_never_cancels_a_fulfillment(
    db_session: AsyncSession,
) -> None:
    """Requirements 4/5/6: unconfirming reverts the order to PENDING in
    the OMS and removes `CONFIRMATION_TAG` from the Shopify order --
    NOTHING else. No `fulfillmentCancel`, no fulfillment-status change.
    Shiprocket is never touched (no shipment exists yet at this point in
    the flow).
    """
    from app.integrations.registry import clear_adapters, register_adapter
    from app.integrations.shopify.adapter import ShopifyAdapter

    client = _StubShopifyClient(
        [
            _tags_add_success("gid://shopify/Order/900006"),
            _tags_remove_success("gid://shopify/Order/900006"),
        ]
    )
    register_adapter(ShopifyAdapter(client=client))
    try:
        leader, telecaller, _other, customer = await _setup(db_session)
        order = await make_order(
            db_session,
            order_number="UNCONF-SHOPIFY-F",
            customer=customer,
            status=OrderStatus.PENDING,
            shopify_order_id="900006",
        )
        async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
            await _assign(leader_client, str(order.id), str(telecaller.id))

        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            confirm = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")
            assert confirm.status_code == 200

            response = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/unconfirm")
            assert response.status_code == 200
            assert response.json()["data"]["status"] == "pending"

        assert len(client.calls) == 2
        remove_query, remove_variables = client.calls[1]
        assert "tagsRemove" in remove_query
        assert remove_variables == {"id": "gid://shopify/Order/900006", "tags": ["OMS Confirmed"]}
        _assert_no_fulfillment_calls(client)
    finally:
        clear_adapters()


async def test_unconfirm_succeeds_even_when_the_tag_removal_fails(
    db_session: AsyncSession,
) -> None:
    """Tag removal is best-effort -- unlike the previous fulfillment-
    cancel reversal (which could block the revert), a Shopify failure
    here must never block the OMS revert. The order still reverts to
    PENDING and attribution is still cleared.
    """
    from app.core.exceptions import IntegrationError
    from app.integrations.registry import clear_adapters, register_adapter
    from app.integrations.shopify.adapter import ShopifyAdapter

    client = _StubShopifyClient(
        [
            _tags_add_success("gid://shopify/Order/900007"),
            IntegrationError("Shopify rejected the tag removal.", details={}),
        ]
    )
    register_adapter(ShopifyAdapter(client=client))
    try:
        leader, telecaller, _other, customer = await _setup(db_session)
        order = await make_order(
            db_session,
            order_number="UNCONF-SHOPIFY-G",
            customer=customer,
            status=OrderStatus.PENDING,
            shopify_order_id="900007",
        )
        async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
            await _assign(leader_client, str(order.id), str(telecaller.id))

        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            confirm = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/confirm")
            assert confirm.status_code == 200

            response = await tc_client.post(f"/api/v1/telecaller/orders/{order.id}/unconfirm")
            assert response.status_code == 200
            assert response.json()["data"]["status"] == "pending"

            order_view = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")
            data = order_view.json()["data"]
            assert data["status"] == "pending"
            assert data["confirmed_by_telecaller_id"] is None

        _assert_no_fulfillment_calls(client)
    finally:
        clear_adapters()


async def test_bulk_confirm_tags_every_order_and_never_creates_a_fulfillment(
    db_session: AsyncSession,
) -> None:
    """Requirement 8: bulk confirmation reuses the exact same per-order
    sync logic as a single confirm -- no separate Shopify code path for
    bulk, and never a `fulfillmentCreate` for any order in the batch.
    """
    from app.integrations.registry import clear_adapters, register_adapter
    from app.integrations.shopify.adapter import ShopifyAdapter

    client = _StubShopifyClient(
        [
            _tags_add_success("gid://shopify/Order/900008"),
            _tags_add_success("gid://shopify/Order/900009"),
        ]
    )
    register_adapter(ShopifyAdapter(client=client))
    try:
        leader, telecaller, _other, customer = await _setup(db_session)
        order_a = await make_order(
            db_session,
            order_number="UNCONF-SHOPIFY-H1",
            customer=customer,
            status=OrderStatus.PENDING,
            shopify_order_id="900008",
        )
        order_b = await make_order(
            db_session,
            order_number="UNCONF-SHOPIFY-H2",
            customer=customer,
            status=OrderStatus.PENDING,
            shopify_order_id="900009",
        )
        async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
            await _assign(leader_client, str(order_a.id), str(telecaller.id))
            await _assign(leader_client, str(order_b.id), str(telecaller.id))

        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            response = await tc_client.post(
                "/api/v1/telecaller/orders/confirm",
                json={"order_ids": [str(order_a.id), str(order_b.id)]},
            )
            assert response.status_code == 200
            assert response.json()["data"]["confirmed_count"] == 2

        assert len(client.calls) == 2
        for query, _variables in client.calls:
            assert "tagsAdd" in query
        _assert_no_fulfillment_calls(client)

        await db_session.refresh(order_a)
        await db_session.refresh(order_b)
        assert order_a.status == OrderStatus.CONFIRMED
        assert order_b.status == OrderStatus.CONFIRMED
        assert order_a.shopify_confirmation_fulfillment_id is None
        assert order_b.shopify_confirmation_fulfillment_id is None
    finally:
        clear_adapters()


async def test_telecaller_cannot_retry_shopify_confirmation_sync(db_session: AsyncSession) -> None:
    """The manual retry endpoint is gated by `orders.update`, which
    TELECALLER does not have (same tier as the shipment-level `POST
    /shipments/{id}/shopify/retry-sync`, gated by `shipments.update`,
    which TELECALLER also lacks) -- a telecaller confirms/unconfirms, but
    manually retrying a stuck sync is an operations action.
    """
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session,
        order_number="UNCONF-SHOPIFY-I",
        customer=customer,
        status=OrderStatus.PENDING,
        shopify_order_id="900010",
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.post(
            f"/api/v1/orders/{order.id}/shopify/retry-confirmation-sync"
        )
        assert response.status_code == 403


# Requirement 7 (actual AWB/shipment -> existing Shopify fulfillment/
# tracking sync still works) is proven by `test_shopify_fulfillment.py`,
# unchanged and still passing: `test_assign_awb_triggers_the_shopify_sync`
# exercises exactly this path with `shopify_confirmation_fulfillment_id`
# at its default `None` -- i.e. the ordinary condition every order is in
# now that confirmation never sets that column. That module also keeps
# `test_awb_assignment_attaches_tracking_to_the_confirmation_fulfillment`,
# which proves the backward-compat fallback for an order confirmed under
# the PREVIOUS (fulfillment-creating) behaviour still works unchanged.
