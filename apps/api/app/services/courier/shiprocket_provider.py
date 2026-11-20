"""ShiprocketProvider — `CourierProvider` implementation that delegates
every supported operation to the existing, unmodified
`ShiprocketOperationsService`. No Shiprocket API payload, endpoint,
status mapping, retry logic or error-handling behavior is changed or
duplicated here; this is a pure adaptation layer between the generic
interface's typed request/response shapes and
`ShiprocketOperationsService`'s existing method signatures.

Four of the eight interface operations have no existing
`ShiprocketOperationsService` method to delegate to — confirmed by
inspection (`grep` across `app/services/shiprocket_service.py` and
`app/integrations/shiprocket/adapter.py` found no serviceability, rate,
label, or manifest method anywhere). Per instruction, these are NOT
invented; they raise `IntegrationError` documenting the limitation.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import IntegrationError, NotFoundError
from app.models.mixins import SourceSystem
from app.services.courier.base import (
    CancelShipmentRequest,
    CancelShipmentResult,
    CourierProvider,
    CreateShipmentRequest,
    CreateShipmentResult,
    GenerateAwbRequest,
    GenerateAwbResult,
    GenerateLabelRequest,
    GenerateLabelResult,
    GenerateManifestRequest,
    GenerateManifestResult,
    SchedulePickupRequest,
    SchedulePickupResult,
    ServiceabilityRequest,
    ServiceabilityResult,
    TrackShipmentRequest,
    TrackShipmentResult,
)
from app.services.shiprocket_service import ShiprocketOperationsService


def _not_supported(operation: str) -> IntegrationError:
    return IntegrationError(
        f"Shiprocket provider does not support {operation} — no existing "
        "ShiprocketOperationsService method implements it, and this wrapper "
        "does not invent new Shiprocket API calls.",
        details={"error_type": "not_supported"},
    )


class ShiprocketProvider(CourierProvider):
    code = SourceSystem.SHIPROCKET

    def __init__(self, session: AsyncSession) -> None:
        self._service = ShiprocketOperationsService(session)

    async def check_serviceability(self, request: ServiceabilityRequest) -> ServiceabilityResult:
        raise _not_supported("check_serviceability")

    async def create_shipment(self, request: CreateShipmentRequest) -> CreateShipmentResult:
        shipment = await self._service.create_shipment_for_order(
            request.order_id,
            actor=request.actor,
            length_cm=request.length_cm,
            breadth_cm=request.breadth_cm,
            height_cm=request.height_cm,
            weight_kg=request.weight_kg,
        )
        return CreateShipmentResult(
            shipment_id=shipment.id,
            provider_shipment_id=shipment.shiprocket_shipment_id,
            raw=shipment.raw_external_payload or {},
        )

    async def generate_awb(self, request: GenerateAwbRequest) -> GenerateAwbResult:
        shipment = await self._service.assign_awb(
            request.shipment_id, actor=request.actor, courier_id=request.courier_id
        )
        courier_name = None
        if shipment.courier_id is not None:
            try:
                courier = await self._service.courier_service.get_courier(shipment.courier_id)
                courier_name = courier.name
            except NotFoundError:
                pass
        return GenerateAwbResult(
            shipment_id=shipment.id, awb=shipment.awb, courier_name=courier_name
        )

    async def track_shipment(self, request: TrackShipmentRequest) -> TrackShipmentResult:
        shipment = await self._service.refresh_tracking_for_shipment(
            request.shipment_id, actor=request.actor
        )
        return TrackShipmentResult(
            shipment_id=shipment.id,
            current_status=shipment.current_status.value,
            awb=shipment.awb,
        )

    async def cancel_shipment(self, request: CancelShipmentRequest) -> CancelShipmentResult:
        shipment = await self._service.cancel_shipment(request.shipment_id, actor=request.actor)
        return CancelShipmentResult(
            shipment_id=shipment.id, cancelled=shipment.current_status.value == "cancelled"
        )

    async def generate_label(self, request: GenerateLabelRequest) -> GenerateLabelResult:
        raise _not_supported("generate_label")

    async def generate_manifest(self, request: GenerateManifestRequest) -> GenerateManifestResult:
        raise _not_supported("generate_manifest")

    async def schedule_pickup(self, request: SchedulePickupRequest) -> SchedulePickupResult:
        await self._service.request_pickup(request.shipment_id, actor=request.actor)
        return SchedulePickupResult(shipment_id=request.shipment_id, scheduled=True)
