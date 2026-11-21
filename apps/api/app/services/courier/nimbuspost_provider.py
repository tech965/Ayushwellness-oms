"""NimbusPostProvider — `CourierProvider` placeholder.

Every courier operation raises immediately, with no HTTP call, no
request-field guessing, and no endpoint-path guessing. This is
deliberate and temporary: NimbusPost's courier-operation endpoints
(serviceability, shipment creation, AWB, label, manifest, pickup,
tracking, cancellation) are confirmed to exist by *path* (from
NimbusPost's official SDK source — see `app.integrations.nimbuspost`'s
module docstrings) but their field-level request/response schemas are
not, and the Postman documentation that would confirm them is not
reachable by any automated means available to this engagement (checked
exhaustively 2026-11-20: direct fetch, `.json`/`collection.json`
variants, Postman's own documenter/collections API, and a search-engine
cache check all failed to return the real content, which loads via
client-side JS this tooling cannot execute).

This is intentionally separate from, and does not change,
`app.integrations.nimbuspost` (the already-implemented, already-staged
authentication + health-check adapter) — that package is untouched by
this file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.exceptions import IntegrationError
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

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_SCHEMA_UNVERIFIED_DETAILS = {"error_type": "schema_unverified"}


def _blocked(operation: str) -> IntegrationError:
    return IntegrationError(
        f"NimbusPost {operation} is blocked: API request/response schema "
        "has not yet been verified.",
        details=_SCHEMA_UNVERIFIED_DETAILS,
    )


class NimbusPostProvider(CourierProvider):
    code = SourceSystem.NIMBUSPOST

    def __init__(self, session: AsyncSession | None = None) -> None:
        # Accepted for a uniform constructor signature across providers
        # (see `app.services.courier.registry.get_courier_provider`) —
        # unused, since every operation below raises before needing one.
        self._session = session

    async def check_serviceability(self, request: ServiceabilityRequest) -> ServiceabilityResult:
        raise _blocked("courier operation 'check_serviceability'")

    async def create_shipment(self, request: CreateShipmentRequest) -> CreateShipmentResult:
        raise _blocked("courier operation 'create_shipment'")

    async def generate_awb(self, request: GenerateAwbRequest) -> GenerateAwbResult:
        raise _blocked("courier operation 'generate_awb'")

    async def track_shipment(self, request: TrackShipmentRequest) -> TrackShipmentResult:
        raise _blocked("courier operation 'track_shipment'")

    async def cancel_shipment(self, request: CancelShipmentRequest) -> CancelShipmentResult:
        raise _blocked("courier operation 'cancel_shipment'")

    async def generate_label(self, request: GenerateLabelRequest) -> GenerateLabelResult:
        raise _blocked("courier operation 'generate_label'")

    async def generate_manifest(self, request: GenerateManifestRequest) -> GenerateManifestResult:
        raise _blocked("courier operation 'generate_manifest'")

    async def schedule_pickup(self, request: SchedulePickupRequest) -> SchedulePickupResult:
        raise _blocked("courier operation 'schedule_pickup'")
