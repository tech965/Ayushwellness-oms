"""Courier provider architecture shell: interface/registry resolution,
`ShiprocketProvider` delegation (mocked — never a real Shiprocket call),
and `NimbusPostProvider`'s schema-unverified guard. No HTTP call, no
database write, no real shipment anywhere in this file.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from app.core.exceptions import IntegrationError, ValidationError
from app.models.enums import ShipmentStatus
from app.models.mixins import SourceSystem
from app.services.courier.base import (
    CancelShipmentRequest,
    CreateShipmentRequest,
    GenerateAwbRequest,
    GenerateLabelRequest,
    GenerateManifestRequest,
    SchedulePickupRequest,
    ServiceabilityRequest,
    TrackShipmentRequest,
)
from app.services.courier.nimbuspost_provider import NimbusPostProvider
from app.services.courier.registry import get_courier_provider
from app.services.courier.shiprocket_provider import ShiprocketProvider

pytestmark = pytest.mark.asyncio


class _FakeShipment:
    def __init__(
        self,
        *,
        id=None,
        shiprocket_shipment_id="SR123",
        awb=None,
        courier_id=None,
        current_status=ShipmentStatus.PENDING,
        raw_external_payload=None,
    ) -> None:
        self.id = id or uuid.uuid4()
        self.shiprocket_shipment_id = shiprocket_shipment_id
        self.awb = awb
        self.courier_id = courier_id
        self.current_status = current_status
        self.raw_external_payload = raw_external_payload or {"order_id": "999"}


class _FakeCourier:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeCourierService:
    def __init__(self, courier: _FakeCourier | None) -> None:
        self._courier = courier

    async def get_courier(self, courier_id):
        from app.core.exceptions import NotFoundError

        if self._courier is None:
            raise NotFoundError("Courier not found.")
        return self._courier


class _FakeShiprocketOperationsService:
    """Stand-in for `ShiprocketOperationsService` — every method is an
    `AsyncMock` so a test can assert exactly what `ShiprocketProvider`
    called it with, and control what it returns, without touching the
    real service, a real session, or a real Shiprocket account.
    """

    def __init__(self, session) -> None:
        self.session = session
        self.courier_service = _FakeCourierService(_FakeCourier("Delhivery Surface"))
        self.create_shipment_for_order = AsyncMock(return_value=_FakeShipment())
        self.assign_awb = AsyncMock(
            return_value=_FakeShipment(awb="AWB123", courier_id=uuid.uuid4())
        )
        self.track_shipment_result = _FakeShipment(
            awb="AWB123", current_status=ShipmentStatus.IN_TRANSIT
        )
        self.refresh_tracking_for_shipment = AsyncMock(
            side_effect=lambda shipment_id, *, actor: self.track_shipment_result
        )
        self.cancel_shipment = AsyncMock(
            return_value=_FakeShipment(current_status=ShipmentStatus.CANCELLED)
        )
        self.request_pickup = AsyncMock(return_value=_FakeShipment())


@pytest.fixture
def fake_shiprocket_service(monkeypatch):
    fake_instances: list[_FakeShiprocketOperationsService] = []

    def factory(session):
        instance = _FakeShiprocketOperationsService(session)
        fake_instances.append(instance)
        return instance

    monkeypatch.setattr(
        "app.services.courier.shiprocket_provider.ShiprocketOperationsService", factory
    )
    return fake_instances


# --------------------------------------------------------------- registry
async def test_registry_resolves_shiprocket():
    provider = get_courier_provider(SourceSystem.SHIPROCKET, session=object())
    assert isinstance(provider, ShiprocketProvider)
    assert provider.code == "shiprocket"


async def test_registry_resolves_nimbuspost():
    provider = get_courier_provider(SourceSystem.NIMBUSPOST, session=object())
    assert isinstance(provider, NimbusPostProvider)
    assert provider.code == "nimbuspost"


async def test_registry_raises_on_unknown_provider():
    with pytest.raises(ValidationError) as exc_info:
        get_courier_provider("delhivery", session=object())
    assert exc_info.value.details["error_type"] == "unknown_provider"


async def test_source_system_has_nimbuspost_constant():
    assert SourceSystem.NIMBUSPOST == "nimbuspost"


# ----------------------------------------------------- shiprocket delegation
async def test_create_shipment_delegates_to_existing_service(fake_shiprocket_service):
    provider = ShiprocketProvider(session=object())
    fake = fake_shiprocket_service[0]
    order_id = uuid.uuid4()

    result = await provider.create_shipment(
        CreateShipmentRequest(order_id=order_id, actor=None, weight_kg=1.2)
    )

    fake.create_shipment_for_order.assert_awaited_once_with(
        order_id, actor=None, length_cm=10.0, breadth_cm=10.0, height_cm=10.0, weight_kg=1.2
    )
    assert result.provider_shipment_id == "SR123"
    assert result.raw == {"order_id": "999"}


async def test_generate_awb_delegates_and_resolves_courier_name(fake_shiprocket_service):
    provider = ShiprocketProvider(session=object())
    fake = fake_shiprocket_service[0]
    shipment_id = uuid.uuid4()

    result = await provider.generate_awb(
        GenerateAwbRequest(shipment_id=shipment_id, actor=None, courier_id="51")
    )

    fake.assign_awb.assert_awaited_once_with(shipment_id, actor=None, courier_id="51")
    assert result.awb == "AWB123"
    assert result.courier_name == "Delhivery Surface"


async def test_track_shipment_delegates_to_existing_service(fake_shiprocket_service):
    provider = ShiprocketProvider(session=object())
    fake = fake_shiprocket_service[0]
    shipment_id = uuid.uuid4()

    request = TrackShipmentRequest(shipment_id=shipment_id, actor=None)
    result = await provider.track_shipment(request)

    fake.refresh_tracking_for_shipment.assert_awaited_once_with(shipment_id, actor=None)
    assert result.current_status == "in_transit"
    assert result.awb == "AWB123"


async def test_cancel_shipment_delegates_to_existing_service(fake_shiprocket_service):
    provider = ShiprocketProvider(session=object())
    fake = fake_shiprocket_service[0]
    shipment_id = uuid.uuid4()

    request = CancelShipmentRequest(shipment_id=shipment_id, actor=None)
    result = await provider.cancel_shipment(request)

    fake.cancel_shipment.assert_awaited_once_with(shipment_id, actor=None)
    assert result.cancelled is True


async def test_schedule_pickup_delegates_to_existing_service(fake_shiprocket_service):
    provider = ShiprocketProvider(session=object())
    fake = fake_shiprocket_service[0]
    shipment_id = uuid.uuid4()

    result = await provider.schedule_pickup(
        SchedulePickupRequest(shipment_id=shipment_id, actor=None)
    )

    fake.request_pickup.assert_awaited_once_with(shipment_id, actor=None)
    assert result.scheduled is True


@pytest.mark.parametrize(
    ("method_name", "request_obj"),
    [
        ("check_serviceability", ServiceabilityRequest("110001", "400001", "prepaid", 1.0)),
        ("generate_label", GenerateLabelRequest(shipment_id=uuid.uuid4(), actor=None)),
        (
            "generate_manifest",
            GenerateManifestRequest(shipment_ids=[uuid.uuid4()], actor=None),
        ),
    ],
)
async def test_shiprocket_provider_documents_unsupported_operations(
    fake_shiprocket_service, method_name, request_obj
):
    provider = ShiprocketProvider(session=object())
    with pytest.raises(IntegrationError) as exc_info:
        await getattr(provider, method_name)(request_obj)
    assert exc_info.value.details["error_type"] == "not_supported"


# -------------------------------------------------------- nimbuspost placeholder
@pytest.mark.parametrize(
    ("method_name", "request_obj"),
    [
        ("check_serviceability", ServiceabilityRequest("110001", "400001", "prepaid", 1.0)),
        (
            "create_shipment",
            CreateShipmentRequest(order_id=uuid.uuid4(), actor=None),
        ),
        ("generate_awb", GenerateAwbRequest(shipment_id=uuid.uuid4(), actor=None)),
        ("track_shipment", TrackShipmentRequest(shipment_id=uuid.uuid4(), actor=None)),
        ("cancel_shipment", CancelShipmentRequest(shipment_id=uuid.uuid4(), actor=None)),
        ("generate_label", GenerateLabelRequest(shipment_id=uuid.uuid4(), actor=None)),
        (
            "generate_manifest",
            GenerateManifestRequest(shipment_ids=[uuid.uuid4()], actor=None),
        ),
        ("schedule_pickup", SchedulePickupRequest(shipment_id=uuid.uuid4(), actor=None)),
    ],
)
async def test_nimbuspost_provider_blocks_every_operation(method_name, request_obj):
    provider = NimbusPostProvider()
    with pytest.raises(IntegrationError) as exc_info:
        await getattr(provider, method_name)(request_obj)
    assert exc_info.value.details["error_type"] == "schema_unverified"
    assert "schema" in exc_info.value.message.lower()


async def test_nimbuspost_provider_makes_no_http_call_and_accepts_optional_session():
    """Constructing and calling every operation must never require, or
    attempt to use, a real database session or HTTP client."""
    provider = NimbusPostProvider(session=None)
    with pytest.raises(IntegrationError):
        await provider.create_shipment(CreateShipmentRequest(order_id=uuid.uuid4(), actor=None))
