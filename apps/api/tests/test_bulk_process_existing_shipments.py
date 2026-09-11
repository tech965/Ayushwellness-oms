"""Process Shipment / Ship Order -- the API equivalent of Shiprocket's own
dashboard "Bulk Ship Orders" action, for orders that already have an
EXISTING Shiprocket order/shipment.

`POST /orders/bulk-process-shipments` (and its scoped Shipment Staff
equivalent, `POST /shipment-staff/orders/bulk-process-shipments`) is
deliberately distinct from `POST /orders/bulk-ship` (`test_bulk_ship.py`),
which creates a NEW Shiprocket shipment via `orders/create/adhoc` -- this
feature NEVER does that. It only resolves each order's EXISTING Shiprocket
shipment (via `locate_shiprocket_orders`, unchanged) and assigns an AWB to
it, skipping any shipment that already has one.
"""

from __future__ import annotations

import pytest
from app.db.session import get_db
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.integrations.shiprocket.errors import ShiprocketApiError
from app.main import app
from app.models.courier import Courier
from app.models.enums import OrderStatus, ShipmentStatus
from app.models.shipment import Shipment
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import (
    bearer_client,
    make_customer,
    make_order,
    make_role,
    make_user,
)

pytestmark = pytest.mark.asyncio


class _StubClient:
    """Mirrors the stub client used across the other Shiprocket test
    files -- hand-queued responses, no real HTTP.
    """

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict | None]] = []

    async def request(
        self, method: str, path: str, *, json: dict | None = None, params: dict | None = None
    ) -> dict:
        self.calls.append((method, path, json))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def ensure_authenticated(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _reset_adapter_registry():
    yield
    clear_adapters()


@pytest.fixture(autouse=True)
def _clear_get_db_override():
    yield
    app.dependency_overrides.clear()


def _assign_awb_response(
    *, awb: str = "AWB777", courier_name: str = "Delhivery", courier_id: str = "51"
) -> dict:
    return {
        "response": {
            "data": {
                "awb_code": awb,
                "courier_name": courier_name,
                "courier_company_id": courier_id,
            }
        }
    }


async def _make_ops_user(db_session: AsyncSession, *, email: str = "ops@bulk-process.example.com"):
    role = await make_role(
        db_session, name="OPS_BULK_PROCESS", permission_codes=["shipments.read", "shipments.update"]
    )
    return await make_user(db_session, email=email, role=role)


async def _order_with_existing_shipment(
    db_session: AsyncSession,
    *,
    order_number: str,
    shiprocket_shipment_id: str,
    shiprocket_order_id: str,
    awb: str | None = None,
    courier: Courier | None = None,
) -> tuple:
    customer = await make_customer(db_session)
    order = await make_order(
        db_session, order_number=order_number, customer=customer, status=OrderStatus.CONFIRMED
    )
    shipment = Shipment(
        order_id=order.id,
        current_status=ShipmentStatus.PENDING,
        source_system="shiprocket",
        external_id=shiprocket_shipment_id,
        shiprocket_shipment_id=shiprocket_shipment_id,
        raw_external_payload={
            "order_id": shiprocket_order_id,
            "shipment_id": shiprocket_shipment_id,
        },
        awb=awb,
        courier_id=courier.id if courier else None,
    )
    db_session.add(shipment)
    await db_session.commit()
    await db_session.refresh(order)
    await db_session.refresh(shipment)
    return order, shipment


async def _bare_confirmed_order(db_session: AsyncSession, *, order_number: str):
    customer = await make_customer(db_session)
    return await make_order(
        db_session, order_number=order_number, customer=customer, status=OrderStatus.CONFIRMED
    )


# --- 1/6/7/10/11: one existing shipment -> AWB assigned successfully ----


async def test_processes_one_existing_shipment_and_assigns_awb(db_session: AsyncSession) -> None:
    ops = await _make_ops_user(db_session)
    order, shipment = await _order_with_existing_shipment(
        db_session,
        order_number="#AWL90001",
        shiprocket_shipment_id="7001",
        shiprocket_order_id="1900007001",
    )
    client = _StubClient([_assign_awb_response(awb="AWB90001", courier_name="Delhivery")])
    register_adapter(ShiprocketAdapter(client=client))

    async with bearer_client(app, get_db, db_session, ops.id) as api_client:
        response = await api_client.post(
            "/api/v1/orders/bulk-process-shipments", json={"order_ids": [str(order.id)]}
        )
        assert response.status_code == 200
        body = response.json()["data"]
        assert body["processed_count"] == 1
        assert body["skipped_count"] == 0
        assert body["failed_count"] == 0
        result = body["results"][0]

        # 7. Exact existing OMS order id is preserved on the result.
        assert result["order_id"] == str(order.id)
        assert result["order_number"] == "#AWL90001"
        assert result["status"] == "success"
        # 7. Exact existing Shiprocket order id is preserved (never
        # fabricated, never the OMS order number).
        assert result["shiprocket_order_id"] == "1900007001"
        # 6. Exact existing Shiprocket shipment id was the one used.
        assert result["shiprocket_shipment_id"] == "7001"
        # 11. Returned courier + AWB are exposed correctly.
        assert result["awb"] == "AWB90001"
        assert result["courier_name"] == "Delhivery"
        assert result["reason"] is None

    # 6/10. The adapter call used the exact existing shipment_id, and
    # never sent a courier_id (Shiprocket's own selection applies).
    assert len(client.calls) == 1
    method, path, payload = client.calls[0]
    assert method == "POST"
    assert path == "/courier/assign/awb"
    assert payload == {"shipment_id": "7001"}

    # 11. Persisted on the OMS Shipment row too, not just the response.
    await db_session.refresh(shipment)
    assert shipment.awb == "AWB90001"
    courier = await db_session.get(Courier, shipment.courier_id)
    assert courier is not None
    assert courier.name == "Delhivery"


# --- 2: multiple existing shipments -> all processed independently -----


async def test_processes_multiple_existing_shipments_independently(
    db_session: AsyncSession,
) -> None:
    ops = await _make_ops_user(db_session)
    order_a, _ = await _order_with_existing_shipment(
        db_session,
        order_number="#AWL90010",
        shiprocket_shipment_id="7010",
        shiprocket_order_id="1900007010",
    )
    order_b, _ = await _order_with_existing_shipment(
        db_session,
        order_number="#AWL90011",
        shiprocket_shipment_id="7011",
        shiprocket_order_id="1900007011",
    )
    client = _StubClient(
        [
            _assign_awb_response(awb="AWB90010", courier_name="Delhivery"),
            _assign_awb_response(awb="AWB90011", courier_name="Xpressbees"),
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))

    async with bearer_client(app, get_db, db_session, ops.id) as api_client:
        response = await api_client.post(
            "/api/v1/orders/bulk-process-shipments",
            json={"order_ids": [str(order_a.id), str(order_b.id)]},
        )
        assert response.status_code == 200
        body = response.json()["data"]
        assert body["processed_count"] == 2
        results = {r["order_id"]: r for r in body["results"]}
        assert results[str(order_a.id)]["awb"] == "AWB90010"
        assert results[str(order_b.id)]["awb"] == "AWB90011"


# --- 3: shipment already has an AWB -> skipped, no assign/awb call -----


async def test_skips_a_shipment_that_already_has_an_awb(db_session: AsyncSession) -> None:
    ops = await _make_ops_user(db_session)
    order, shipment = await _order_with_existing_shipment(
        db_session,
        order_number="#AWL90020",
        shiprocket_shipment_id="7020",
        shiprocket_order_id="1900007020",
        awb="EXISTING-AWB",
    )
    # Empty stub -- calling `request()` at all would raise `IndexError`,
    # proving no assign/awb call was made.
    client = _StubClient([])
    register_adapter(ShiprocketAdapter(client=client))

    async with bearer_client(app, get_db, db_session, ops.id) as api_client:
        response = await api_client.post(
            "/api/v1/orders/bulk-process-shipments", json={"order_ids": [str(order.id)]}
        )
        assert response.status_code == 200
        body = response.json()["data"]
        assert body["processed_count"] == 0
        assert body["skipped_count"] == 1
        result = body["results"][0]
        assert result["status"] == "skipped"
        assert result["awb"] == "EXISTING-AWB"
        assert "already assigned" in result["reason"].lower()

    assert client.calls == []
    await db_session.refresh(shipment)
    assert shipment.awb == "EXISTING-AWB"


# --- 4: one failure never blocks the rest of the batch ------------------


async def test_one_failure_does_not_stop_remaining_orders(db_session: AsyncSession) -> None:
    ops = await _make_ops_user(db_session)
    order_ok, _ = await _order_with_existing_shipment(
        db_session,
        order_number="#AWL90030",
        shiprocket_shipment_id="7030",
        shiprocket_order_id="1900007030",
    )
    order_fail, _ = await _order_with_existing_shipment(
        db_session,
        order_number="#AWL90031",
        shiprocket_shipment_id="7031",
        shiprocket_order_id="1900007031",
    )
    client = _StubClient(
        [
            _assign_awb_response(awb="AWB90030"),
            ShiprocketApiError(
                "Courier serviceability check failed.", error_type="validation_error"
            ),
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))

    async with bearer_client(app, get_db, db_session, ops.id) as api_client:
        response = await api_client.post(
            "/api/v1/orders/bulk-process-shipments",
            json={"order_ids": [str(order_ok.id), str(order_fail.id)]},
        )
        assert response.status_code == 200
        body = response.json()["data"]
        assert body["processed_count"] == 1
        assert body["failed_count"] == 1
        results = {r["order_id"]: r for r in body["results"]}
        assert results[str(order_ok.id)]["status"] == "success"
        assert results[str(order_fail.id)]["status"] == "failed"
        assert results[str(order_fail.id)]["reason"]

    # Both calls were actually attempted -- the first failure didn't
    # short-circuit the loop.
    assert len(client.calls) == 2


# --- 5: no existing Shiprocket shipment locatable -> failed, clear reason


async def test_missing_shiprocket_shipment_fails_with_a_clear_reason(
    db_session: AsyncSession,
) -> None:
    ops = await _make_ops_user(db_session)
    order = await _bare_confirmed_order(db_session, order_number="#AWL90040")
    # No Shipment row, and nothing for a live scan to find.
    client = _StubClient([{"data": [], "meta": {"pagination": {"total_pages": 1}}}])
    register_adapter(ShiprocketAdapter(client=client))

    async with bearer_client(app, get_db, db_session, ops.id) as api_client:
        response = await api_client.post(
            "/api/v1/orders/bulk-process-shipments", json={"order_ids": [str(order.id)]}
        )
        assert response.status_code == 200
        result = response.json()["data"]["results"][0]
        assert result["status"] == "failed"
        assert "could not be located" in result["reason"].lower()
        assert result["awb"] is None
        assert result["shiprocket_shipment_id"] is None

    # 8. Never a create-shipment call.
    assert all(path != "/orders/create/adhoc" for _method, path, _json in client.calls)


async def test_unknown_order_id_fails_with_order_not_found(db_session: AsyncSession) -> None:
    ops = await _make_ops_user(db_session)
    register_adapter(ShiprocketAdapter(client=_StubClient([])))
    missing_id = "00000000-0000-0000-0000-000000000000"

    async with bearer_client(app, get_db, db_session, ops.id) as api_client:
        response = await api_client.post(
            "/api/v1/orders/bulk-process-shipments", json={"order_ids": [missing_id]}
        )
        assert response.status_code == 200
        result = response.json()["data"]["results"][0]
        assert result["status"] == "failed"
        assert result["reason"] == "Order not found."


# --- 8: never calls /orders/create/adhoc, across a full mixed batch -----


async def test_bulk_process_never_calls_create_adhoc_across_a_mixed_batch(
    db_session: AsyncSession,
) -> None:
    ops = await _make_ops_user(db_session)
    already_shipped, _ = await _order_with_existing_shipment(
        db_session,
        order_number="#AWL90050",
        shiprocket_shipment_id="7050",
        shiprocket_order_id="1900007050",
        awb="ALREADY-SHIPPED",
    )
    to_process, _ = await _order_with_existing_shipment(
        db_session,
        order_number="#AWL90051",
        shiprocket_shipment_id="7051",
        shiprocket_order_id="1900007051",
    )
    unresolvable = await _bare_confirmed_order(db_session, order_number="#AWL90052")
    client = _StubClient(
        [
            _assign_awb_response(awb="AWB90051"),
            {"data": [], "meta": {"pagination": {"total_pages": 1}}},
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))

    async with bearer_client(app, get_db, db_session, ops.id) as api_client:
        response = await api_client.post(
            "/api/v1/orders/bulk-process-shipments",
            json={
                "order_ids": [str(already_shipped.id), str(to_process.id), str(unresolvable.id)]
            },
        )
        assert response.status_code == 200
        body = response.json()["data"]
        assert body["skipped_count"] == 1
        assert body["processed_count"] == 1
        assert body["failed_count"] == 1

    assert all(path != "/orders/create/adhoc" for _method, path, _json in client.calls)


# --- 9: calling the bulk endpoint twice never re-assigns an AWB --------


async def test_calling_bulk_process_twice_does_not_reassign_an_awb(
    db_session: AsyncSession,
) -> None:
    ops = await _make_ops_user(db_session)
    order, shipment = await _order_with_existing_shipment(
        db_session,
        order_number="#AWL90060",
        shiprocket_shipment_id="7060",
        shiprocket_order_id="1900007060",
    )
    client = _StubClient([_assign_awb_response(awb="AWB90060", courier_name="Delhivery")])
    register_adapter(ShiprocketAdapter(client=client))

    async with bearer_client(app, get_db, db_session, ops.id) as api_client:
        first = await api_client.post(
            "/api/v1/orders/bulk-process-shipments", json={"order_ids": [str(order.id)]}
        )
        assert first.json()["data"]["results"][0]["status"] == "success"
        assert len(client.calls) == 1

        second = await api_client.post(
            "/api/v1/orders/bulk-process-shipments", json={"order_ids": [str(order.id)]}
        )
        second_result = second.json()["data"]["results"][0]
        assert second_result["status"] == "skipped"
        assert second_result["awb"] == "AWB90060"

    # No second Shiprocket call was ever made -- the stub client only had
    # one response queued, so a second `request()` call would have raised
    # `IndexError` and surfaced as a 500 above.
    assert len(client.calls) == 1
    await db_session.refresh(shipment)
    assert shipment.awb == "AWB90060"


# --- Permission gate ------------------------------------------------------


async def test_bulk_process_requires_shipments_update_permission(db_session: AsyncSession) -> None:
    role = await make_role(
        db_session, name="READ_ONLY_BULK_PROCESS", permission_codes=["shipments.read"]
    )
    user = await make_user(db_session, email="readonly@bulk-process.example.com", role=role)
    customer = await make_customer(db_session)
    order = await make_order(
        db_session, order_number="#AWL90070", customer=customer, status=OrderStatus.CONFIRMED
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.post(
            "/api/v1/orders/bulk-process-shipments", json={"order_ids": [str(order.id)]}
        )
        assert response.status_code == 403


# --- 12. Existing individual assign-awb endpoint keeps working ----------


async def test_individual_assign_awb_endpoint_still_works(db_session: AsyncSession) -> None:
    ops = await _make_ops_user(db_session)
    order, shipment = await _order_with_existing_shipment(
        db_session,
        order_number="#AWL90080",
        shiprocket_shipment_id="7080",
        shiprocket_order_id="1900007080",
    )
    client = _StubClient([_assign_awb_response(awb="AWB90080")])
    register_adapter(ShiprocketAdapter(client=client))

    async with bearer_client(app, get_db, db_session, ops.id) as api_client:
        response = await api_client.post(
            f"/api/v1/shipments/{shipment.id}/shiprocket/assign-awb", json={}
        )
        assert response.status_code == 200
        assert response.json()["data"]["awb"] == "AWB90080"


# --- Scoped Shipment Staff equivalent ------------------------------------


async def test_shipment_staff_bulk_process_rejects_an_order_outside_scope(
    db_session: AsyncSession,
) -> None:
    shipment_staff_role = await make_role(
        db_session, name="SHIPMENT_STAFF_BULK_PROCESS", permission_codes=["shipment_staff.manage"]
    )
    shipment_staff = await make_user(
        db_session, email="ss-bulk-process@example.com", role=shipment_staff_role
    )
    telecaller_role = await make_role(
        db_session,
        name="TELECALLER_BULK_PROCESS",
        permission_codes=["calls.manage", "orders.confirm"],
    )
    other_telecaller = await make_user(
        db_session, email="other-tc-bulk-process@example.com", role=telecaller_role
    )
    order, _ = await _order_with_existing_shipment(
        db_session,
        order_number="#AWL90090",
        shiprocket_shipment_id="7090",
        shiprocket_order_id="1900007090",
    )
    order.confirmed_by_telecaller_id = other_telecaller.id
    await db_session.commit()
    register_adapter(ShiprocketAdapter(client=_StubClient([])))

    async with bearer_client(app, get_db, db_session, shipment_staff.id) as client:
        response = await client.post(
            "/api/v1/shipment-staff/orders/bulk-process-shipments",
            json={"order_ids": [str(order.id)]},
        )
        assert response.status_code == 200
        result = response.json()["data"]["results"][0]
        assert result["status"] == "failed"
        assert "scope" in result["reason"].lower()


async def test_shipment_staff_bulk_process_succeeds_for_an_order_in_scope(
    db_session: AsyncSession,
) -> None:
    shipment_staff_role = await make_role(
        db_session, name="SHIPMENT_STAFF_BULK_PROCESS_2", permission_codes=["shipment_staff.manage"]
    )
    shipment_staff = await make_user(
        db_session, email="ss-bulk-process-2@example.com", role=shipment_staff_role
    )
    telecaller_role = await make_role(
        db_session, name="TELECALLER_BULK_PROCESS_2", permission_codes=["calls.manage"]
    )
    my_telecaller = await make_user(
        db_session,
        email="my-tc-bulk-process@example.com",
        role=telecaller_role,
    )
    my_telecaller.shipment_staff_id = shipment_staff.id
    await db_session.commit()

    order, _ = await _order_with_existing_shipment(
        db_session,
        order_number="#AWL90091",
        shiprocket_shipment_id="7091",
        shiprocket_order_id="1900007091",
    )
    order.confirmed_by_telecaller_id = my_telecaller.id
    await db_session.commit()

    client = _StubClient([_assign_awb_response(awb="AWB90091")])
    register_adapter(ShiprocketAdapter(client=client))

    async with bearer_client(app, get_db, db_session, shipment_staff.id) as api_client:
        response = await api_client.post(
            "/api/v1/shipment-staff/orders/bulk-process-shipments",
            json={"order_ids": [str(order.id)]},
        )
        assert response.status_code == 200
        result = response.json()["data"]["results"][0]
        assert result["status"] == "success"
        assert result["awb"] == "AWB90091"
