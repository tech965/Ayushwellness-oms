"""Provider-agnostic courier operation interface.

`CourierProvider` is the common surface a courier-operations caller
(future fulfillment-flow code) would depend on, instead of a concrete
`ShiprocketOperationsService`/NimbusPost client directly — mirrors how
`app.integrations.base.IntegrationAdapter` lets `SyncService`/
`IntegrationService` depend on a provider-agnostic interface rather than
a concrete adapter subclass.

This is an architecture shell only (2026-11-20): `ShiprocketProvider`
delegates to the existing, unmodified `ShiprocketOperationsService` for
every operation it already supports, and documents (raises, never
guesses) the operations it does not. `NimbusPostProvider` raises a clear
schema-unverified error for every operation — see its own module
docstring for why. Nothing here is wired into any existing fulfillment
call site yet (Fulfillment Queue, order detail page, NDR flow all still
call `ShiprocketOperationsService` directly, unchanged).

Typed request/response models (frozen dataclasses, matching the
existing `HealthCheckResult`/`FetchPage` style in `app.integrations.
base`) rather than Pydantic — this is an internal service-layer
boundary, not a wire/API schema.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models.auth import User


@dataclass(frozen=True)
class ServiceabilityRequest:
    pickup_pincode: str
    delivery_pincode: str
    payment_type: str  # "prepaid" | "cod"
    weight_kg: float
    length_cm: float | None = None
    breadth_cm: float | None = None
    height_cm: float | None = None


@dataclass(frozen=True)
class CourierOption:
    courier_id: str
    courier_name: str
    estimated_delivery_days: float | None
    freight_charge: float | None
    cod_available: bool


@dataclass(frozen=True)
class ServiceabilityResult:
    serviceable: bool
    couriers: list[CourierOption]


@dataclass(frozen=True)
class CreateShipmentRequest:
    order_id: uuid.UUID
    actor: User | None
    length_cm: float = 10.0
    breadth_cm: float = 10.0
    height_cm: float = 10.0
    weight_kg: float = 0.5


@dataclass(frozen=True)
class CreateShipmentResult:
    shipment_id: uuid.UUID
    provider_shipment_id: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class GenerateAwbRequest:
    shipment_id: uuid.UUID
    actor: User | None
    courier_id: str | None = None


@dataclass(frozen=True)
class GenerateAwbResult:
    shipment_id: uuid.UUID
    awb: str | None
    courier_name: str | None


@dataclass(frozen=True)
class TrackShipmentRequest:
    shipment_id: uuid.UUID
    actor: User | None


@dataclass(frozen=True)
class TrackShipmentResult:
    shipment_id: uuid.UUID
    current_status: str
    awb: str | None


@dataclass(frozen=True)
class CancelShipmentRequest:
    shipment_id: uuid.UUID
    actor: User | None


@dataclass(frozen=True)
class CancelShipmentResult:
    shipment_id: uuid.UUID
    cancelled: bool


@dataclass(frozen=True)
class GenerateLabelRequest:
    shipment_id: uuid.UUID
    actor: User | None


@dataclass(frozen=True)
class GenerateLabelResult:
    shipment_id: uuid.UUID
    label_url: str | None


@dataclass(frozen=True)
class GenerateManifestRequest:
    shipment_ids: list[uuid.UUID]
    actor: User | None


@dataclass(frozen=True)
class GenerateManifestResult:
    manifest_url: str | None


@dataclass(frozen=True)
class SchedulePickupRequest:
    shipment_id: uuid.UUID
    actor: User | None


@dataclass(frozen=True)
class SchedulePickupResult:
    shipment_id: uuid.UUID
    scheduled: bool


class CourierProvider(ABC):
    """One instance per courier provider (`ShiprocketProvider`,
    `NimbusPostProvider`, ...). `code` must match a `SourceSystem` value.
    """

    code: str

    @abstractmethod
    async def check_serviceability(self, request: ServiceabilityRequest) -> ServiceabilityResult:
        """Raises `IntegrationError` if this provider has no serviceability
        check available (see each provider's docstring)."""

    @abstractmethod
    async def create_shipment(self, request: CreateShipmentRequest) -> CreateShipmentResult: ...

    @abstractmethod
    async def generate_awb(self, request: GenerateAwbRequest) -> GenerateAwbResult: ...

    @abstractmethod
    async def track_shipment(self, request: TrackShipmentRequest) -> TrackShipmentResult: ...

    @abstractmethod
    async def cancel_shipment(self, request: CancelShipmentRequest) -> CancelShipmentResult: ...

    @abstractmethod
    async def generate_label(self, request: GenerateLabelRequest) -> GenerateLabelResult:
        """Raises `IntegrationError` if this provider has no label-retrieval
        operation available (see each provider's docstring)."""

    @abstractmethod
    async def generate_manifest(self, request: GenerateManifestRequest) -> GenerateManifestResult:
        """Raises `IntegrationError` if this provider has no manifest
        operation available (see each provider's docstring)."""

    @abstractmethod
    async def schedule_pickup(self, request: SchedulePickupRequest) -> SchedulePickupResult: ...
