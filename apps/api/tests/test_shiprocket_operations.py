"""OMS -> Shiprocket operational actions: shipment creation (push), AWB
assignment, courier mapping, cancellation, pickup, on-demand tracking
refresh, NDR reattempt — the full push workflow (spec §7/§26), plus RBAC,
credential protection, and audit logging. No real Shiprocket account;
the stub client returns hand-built REST response shapes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.core.config import settings
from app.core.exceptions import IntegrationError
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.integrations.shiprocket.errors import ShiprocketApiError
from app.models.audit_log import AuditLog
from app.models.courier import Courier
from app.models.enums import NDRStatus, PaymentType
from app.repositories.ndr import NDRRepository
from app.repositories.shipment import ShipmentRepository
from app.schemas.order import OrderItemCreateRequest
from app.services.order_service import OrderService
from app.services.shiprocket_service import ShiprocketOperationsService
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


class _StubClient:
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
def _reset_registry():
    yield
    clear_adapters()


@pytest.fixture(autouse=True)
def _configure_shiprocket(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "SHIPROCKET_EMAIL", "ops@example.com")
    monkeypatch.setattr(settings, "SHIPROCKET_PASSWORD", "super-secret-password")
    monkeypatch.setattr(settings, "SHIPROCKET_PICKUP_LOCATION", "Main Warehouse")


async def _make_order(session: AsyncSession, order_number: str = "OMS-SR-1"):
    return await OrderService(session).create_order(
        actor=None,
        order_number=order_number,
        customer_id=None,
        order_datetime=datetime.now(UTC),
        currency="INR",
        payment_type=PaymentType.PREPAID,
        shipping_charge=Decimal("0"),
        notes=None,
        items=[
            OrderItemCreateRequest(
                sku="SKU1", product_name="Ashwagandha", quantity=1, unit_price=Decimal("499.00")
            )
        ],
    )


def _create_order_response(shipment_id: str = "5001", shiprocket_order_id: str = "9001") -> dict:
    return {"order_id": shiprocket_order_id, "shipment_id": shipment_id, "status": "NEW"}


def _assign_awb_response(
    *, awb: str = "AWB999", courier_name: str = "Delhivery", courier_id: str = "51"
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


# 6. Shipment creation / 28. Shopify -> OMS -> Shiprocket flow (start)
async def test_create_shipment_for_order_creates_shipment(db_session: AsyncSession) -> None:
    order = await _make_order(db_session)
    client = _StubClient([_create_order_response()])
    register_adapter(ShiprocketAdapter(client=client))

    shipment = await ShiprocketOperationsService(db_session).create_shipment_for_order(
        order.id, actor=None
    )

    assert shipment.order_id == order.id
    assert shipment.shiprocket_shipment_id == "5001"
    assert shipment.source_system == "shiprocket"


async def test_create_shipment_requires_pickup_location(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SHIPROCKET_PICKUP_LOCATION", None)
    order = await _make_order(db_session, "OMS-SR-NOPICKUP")
    register_adapter(ShiprocketAdapter(client=_StubClient([])))

    with pytest.raises(IntegrationError) as exc_info:
        await ShiprocketOperationsService(db_session).create_shipment_for_order(
            order.id, actor=None
        )
    assert exc_info.value.details["error_type"] == "not_configured"


# 14. Duplicate shipment prevention
async def test_creating_shipment_twice_for_same_shiprocket_id_does_not_duplicate(
    db_session: AsyncSession,
) -> None:
    order = await _make_order(db_session, "OMS-SR-2")
    client = _StubClient(
        [_create_order_response(shipment_id="6001"), _create_order_response(shipment_id="6001")]
    )
    register_adapter(ShiprocketAdapter(client=client))

    service = ShiprocketOperationsService(db_session)
    first = await service.create_shipment_for_order(order.id, actor=None)
    second = await service.create_shipment_for_order(order.id, actor=None)

    assert first.id == second.id

    from app.models.shipment import Shipment

    total = await db_session.execute(select(func.count()).select_from(Shipment))
    assert total.scalar_one() == 1


# 8. AWB assignment / 9. Courier mapping
async def test_assign_awb_updates_shipment_and_creates_courier(db_session: AsyncSession) -> None:
    order = await _make_order(db_session, "OMS-SR-3")
    client = _StubClient([_create_order_response(), _assign_awb_response()])
    register_adapter(ShiprocketAdapter(client=client))
    service = ShiprocketOperationsService(db_session)

    shipment = await service.create_shipment_for_order(order.id, actor=None)
    shipment = await service.assign_awb(shipment.id, actor=None, courier_id=None)

    assert shipment.awb == "AWB999"
    courier = await db_session.get(Courier, shipment.courier_id)
    assert courier is not None
    assert courier.name == "Delhivery"
    assert courier.source_system == "shiprocket"
    assert courier.external_id == "51"


# 15. Duplicate AWB / courier prevention
async def test_assigning_awb_twice_does_not_duplicate_courier(db_session: AsyncSession) -> None:
    order = await _make_order(db_session, "OMS-SR-4")
    client = _StubClient(
        [_create_order_response(), _assign_awb_response(), _assign_awb_response(awb="AWB1000")]
    )
    register_adapter(ShiprocketAdapter(client=client))
    service = ShiprocketOperationsService(db_session)

    shipment = await service.create_shipment_for_order(order.id, actor=None)
    await service.assign_awb(shipment.id, actor=None, courier_id=None)
    await service.assign_awb(shipment.id, actor=None, courier_id=None)

    total = await db_session.execute(select(func.count()).select_from(Courier))
    assert total.scalar_one() == 1


async def test_assign_awb_without_shiprocket_shipment_id_is_rejected(
    db_session: AsyncSession,
) -> None:
    from app.services.shipment_service import ShipmentService

    order = await _make_order(db_session, "OMS-SR-5")
    manual_shipment = await ShipmentService(db_session).create_shipment(
        actor=None, order_id=order.id, awb=None, courier_id=None, expected_delivery_date=None
    )
    register_adapter(ShiprocketAdapter(client=_StubClient([])))

    from app.core.exceptions import ConflictError

    with pytest.raises(ConflictError):
        await ShiprocketOperationsService(db_session).assign_awb(
            manual_shipment.id, actor=None, courier_id=None
        )


# Cancel / pickup
async def test_cancel_shipment_updates_status(db_session: AsyncSession) -> None:
    order = await _make_order(db_session, "OMS-SR-6")
    client = _StubClient([_create_order_response(), {"message": "cancelled"}])
    register_adapter(ShiprocketAdapter(client=client))
    service = ShiprocketOperationsService(db_session)

    shipment = await service.create_shipment_for_order(order.id, actor=None)
    shipment = await service.cancel_shipment(shipment.id, actor=None)

    assert shipment.current_status == "cancelled"


async def test_request_pickup_appends_timeline_event(db_session: AsyncSession) -> None:
    order = await _make_order(db_session, "OMS-SR-7")
    client = _StubClient([_create_order_response(), {"pickup_scheduled_date": "2026-01-10"}])
    register_adapter(ShiprocketAdapter(client=client))
    service = ShiprocketOperationsService(db_session)

    shipment = await service.create_shipment_for_order(order.id, actor=None)
    await service.request_pickup(shipment.id, actor=None)

    from app.services.shipment_service import ShipmentService

    timeline = await ShipmentService(db_session).get_timeline(shipment.id)
    assert any(e.status == "PICKUP SCHEDULED" for e in timeline)


# 27. Audit logging
async def test_shiprocket_operations_write_audit_logs(db_session: AsyncSession) -> None:
    order = await _make_order(db_session, "OMS-SR-8")
    client = _StubClient([_create_order_response(), _assign_awb_response()])
    register_adapter(ShiprocketAdapter(client=client))
    service = ShiprocketOperationsService(db_session)

    shipment = await service.create_shipment_for_order(order.id, actor=None)
    await service.assign_awb(shipment.id, actor=None, courier_id=None)

    actions = (
        (
            await db_session.execute(
                select(AuditLog.action).where(AuditLog.entity_id == str(shipment.id))
            )
        )
        .scalars()
        .all()
    )
    assert "shipment.created_via_shiprocket" in actions
    assert "shipment.awb_assigned" in actions


# NDR reattempt — state updates only after Shiprocket confirms (spec §17)
async def test_ndr_reattempt_updates_state_only_after_shiprocket_success(
    db_session: AsyncSession,
) -> None:
    order = await _make_order(db_session, "OMS-SR-9")
    client = _StubClient([_create_order_response(), _assign_awb_response()])
    register_adapter(ShiprocketAdapter(client=client))
    service = ShiprocketOperationsService(db_session)
    shipment = await service.create_shipment_for_order(order.id, actor=None)
    shipment = await service.assign_awb(shipment.id, actor=None, courier_id=None)

    ndr = await NDRRepository(db_session).create(
        shipment_id=shipment.id,
        order_id=order.id,
        courier_id=shipment.courier_id,
        status=NDRStatus.OPEN,
    )
    await db_session.commit()

    # Swap in a client that will succeed for the reattempt call.
    client._responses.append({"message": "reattempt scheduled"})

    updated = await service.ndr_reattempt(
        ndr.id, actor=None, address_1="123 New Address", address_2=None, phone="9876543210"
    )
    assert updated.status == "reattempt_scheduled"


async def test_ndr_reattempt_leaves_state_unchanged_when_shiprocket_call_fails(
    db_session: AsyncSession,
) -> None:
    order = await _make_order(db_session, "OMS-SR-10")
    client = _StubClient([_create_order_response(), _assign_awb_response()])
    register_adapter(ShiprocketAdapter(client=client))
    service = ShiprocketOperationsService(db_session)
    shipment = await service.create_shipment_for_order(order.id, actor=None)
    shipment = await service.assign_awb(shipment.id, actor=None, courier_id=None)

    ndr = await NDRRepository(db_session).create(
        shipment_id=shipment.id,
        order_id=order.id,
        courier_id=shipment.courier_id,
        status=NDRStatus.OPEN,
    )
    await db_session.commit()

    client._responses.append(
        ShiprocketApiError("Shiprocket rejected the request.", error_type="validation_error")
    )

    with pytest.raises(IntegrationError):
        await service.ndr_reattempt(
            ndr.id, actor=None, address_1="123 New Address", address_2=None, phone="9876543210"
        )

    refreshed = await NDRRepository(db_session).get_by_id(ndr.id)
    assert refreshed.status == "open"  # unchanged — Shiprocket call never succeeded


# 26. Credential protection
async def test_shiprocket_password_never_appears_in_any_api_response(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    order = await _make_order(db_session, "OMS-SR-11")
    client = _StubClient([_create_order_response()])
    register_adapter(ShiprocketAdapter(client=client))

    async with await make_authenticated_client(
        db_session, permission_codes=["shipments.update"]
    ) as authed_client:
        response = await authed_client.post(f"/api/v1/orders/{order.id}/ship", json={})
        assert "super-secret-password" not in response.text


# 25. RBAC
async def test_shiprocket_operational_endpoints_require_permission(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    order = await _make_order(db_session, "OMS-SR-12")
    register_adapter(ShiprocketAdapter(client=_StubClient([])))

    async with await make_authenticated_client(db_session, permission_codes=[]) as client:
        assert (await client.post(f"/api/v1/orders/{order.id}/ship", json={})).status_code == 403

        shipment_resp = await client.get("/api/v1/shipments")
        assert shipment_resp.status_code == 403


async def test_ndr_reattempt_endpoint_requires_ndr_update_permission(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    order = await _make_order(db_session, "OMS-SR-13")
    client_stub = _StubClient([_create_order_response(), _assign_awb_response()])
    register_adapter(ShiprocketAdapter(client=client_stub))
    service = ShiprocketOperationsService(db_session)
    shipment = await service.create_shipment_for_order(order.id, actor=None)
    shipment = await service.assign_awb(shipment.id, actor=None, courier_id=None)
    ndr = await NDRRepository(db_session).create(
        shipment_id=shipment.id,
        order_id=order.id,
        courier_id=shipment.courier_id,
        status=NDRStatus.OPEN,
    )
    await db_session.commit()

    async with await make_authenticated_client(db_session, permission_codes=["ndr.read"]) as client:
        response = await client.post(
            f"/api/v1/ndr/{ndr.id}/reattempt",
            json={"address_1": "New address", "phone": "9876543210"},
        )
        assert response.status_code == 403


# 28. Full Shopify (order) -> OMS -> Shiprocket flow
async def test_full_order_to_shipment_to_awb_to_tracking_flow(db_session: AsyncSession) -> None:
    order = await _make_order(db_session, "OMS-SR-FULL")
    client = _StubClient(
        [
            _create_order_response(shipment_id="7777"),
            _assign_awb_response(awb="AWBFULL"),
            {
                "tracking_data": {
                    "shipment_track_activities": [
                        {
                            "id": 1,
                            "status": "IN TRANSIT",
                            "date": "2026-01-06 09:00:00",
                            "activity": "In transit",
                        }
                    ]
                }
            },
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))
    service = ShiprocketOperationsService(db_session)

    shipment = await service.create_shipment_for_order(order.id, actor=None)
    assert shipment.shiprocket_shipment_id == "7777"

    shipment = await service.assign_awb(shipment.id, actor=None, courier_id=None)
    assert shipment.awb == "AWBFULL"

    shipment = await service.refresh_tracking_for_shipment(shipment.id, actor=None)
    assert shipment.current_status == "in_transit"

    timeline = await ShipmentRepository(db_session).get_by_id(shipment.id)
    assert timeline is not None

    audit_actions = (
        (
            await db_session.execute(
                select(AuditLog.action).where(AuditLog.entity_id == str(shipment.id))
            )
        )
        .scalars()
        .all()
    )
    assert {
        "shipment.created_via_shiprocket",
        "shipment.awb_assigned",
        "shipment.tracking_refreshed",
    }.issubset(set(audit_actions))
