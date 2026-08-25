"""OMS -> Shiprocket operational actions (spec §7/§26): create a
Shiprocket shipment from an OMS order, assign an AWB, cancel, request
pickup, refresh tracking on demand, and act on an NDR. Each action calls
the Shiprocket adapter directly (this service is explicitly
Shiprocket-aware, the same way `app/api/v1/webhooks/shopify.py` is
explicitly Shopify-aware) but every OMS write goes through the existing
services/repositories — never a raw session mutation — and OMS state is
only updated *after* Shiprocket confirms success (spec §17).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, IntegrationError, NotFoundError
from app.integrations.registry import get_adapter
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.integrations.shiprocket.config import ShiprocketConfig
from app.integrations.shiprocket.normalizer import TRACKING_NORMALIZER, extract_tracking_events
from app.models.auth import User
from app.models.enums import NDRStatus, ShipmentStatus
from app.models.integration import IntegrationCode
from app.models.ndr import NDR
from app.models.shipment import Shipment
from app.repositories.order import OrderRepository
from app.repositories.shipment import ShipmentRepository
from app.services.audit_service import AuditService
from app.services.courier_service import CourierService
from app.services.ndr_service import NDRService
from app.services.shipment_service import ShipmentService


class ShiprocketOperationsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.orders = OrderRepository(session)
        self.shipments = ShipmentRepository(session)
        self.shipment_service = ShipmentService(session)
        self.courier_service = CourierService(session)
        self.ndr_service = NDRService(session)
        self.audit = AuditService(session)

    def _get_adapter(self) -> ShiprocketAdapter:
        adapter = get_adapter(IntegrationCode.SHIPROCKET)
        if adapter is None:
            raise IntegrationError(
                "No adapter registered for integration 'shiprocket'.",
                details={"error_type": "integration_error"},
            )
        return adapter  # type: ignore[return-value]

    async def _get_shipment(self, shipment_id: uuid.UUID) -> Shipment:
        shipment = await self.shipments.get_by_id(shipment_id)
        if shipment is None:
            raise NotFoundError("Shipment not found.")
        return shipment

    async def create_shipment_for_order(
        self,
        order_id: uuid.UUID,
        *,
        actor: User | None,
        length_cm: float = 10.0,
        breadth_cm: float = 10.0,
        height_cm: float = 10.0,
        weight_kg: float = 0.5,
    ) -> Shipment:
        order = await self.orders.get_by_id_with_items_and_customer(order_id)
        if order is None:
            raise NotFoundError("Order not found.")

        config = ShiprocketConfig.from_settings()
        if config is None or not config.pickup_location:
            raise IntegrationError(
                "Shiprocket is not configured with a pickup location "
                "(SHIPROCKET_EMAIL/SHIPROCKET_PASSWORD/SHIPROCKET_PICKUP_LOCATION).",
                details={"error_type": "not_configured"},
            )

        adapter = self._get_adapter()
        response = await adapter.create_order(
            order,
            pickup_location=config.pickup_location,
            length_cm=length_cm,
            breadth_cm=breadth_cm,
            height_cm=height_cm,
            weight_kg=weight_kg,
        )

        shiprocket_shipment_id = response.get("shipment_id")
        if not shiprocket_shipment_id:
            raise IntegrationError(
                "Shiprocket order creation response did not include a shipment_id.",
                details={"error_type": "validation_error"},
            )

        shipment, _created = await self.shipment_service.upsert_synced_shipment(
            source_system="shiprocket",
            external_id=str(shiprocket_shipment_id),
            order_id=order.id,
            shiprocket_shipment_id=str(shiprocket_shipment_id),
            raw_external_payload=response,
        )

        await self.audit.record(
            user=actor,
            action="shipment.created_via_shiprocket",
            entity_type="shipment",
            entity_id=str(shipment.id),
            new_value={
                "order_id": str(order.id),
                "shiprocket_order_id": response.get("order_id"),
                "shiprocket_shipment_id": shiprocket_shipment_id,
            },
        )
        await self.session.commit()
        return shipment

    async def assign_awb(
        self, shipment_id: uuid.UUID, *, actor: User | None, courier_id: str | None
    ) -> Shipment:
        shipment = await self._get_shipment(shipment_id)
        if not shipment.shiprocket_shipment_id:
            raise ConflictError(
                "Shipment has no Shiprocket shipment id — create it via Shiprocket first."
            )

        adapter = self._get_adapter()
        response = await adapter.assign_awb(shipment.shiprocket_shipment_id, courier_id=courier_id)
        data = response.get("response", {}).get("data", response)
        awb_code = data.get("awb_code")
        courier_name = data.get("courier_name")
        courier_company_id = data.get("courier_company_id") or courier_id

        update_fields: dict[str, object] = {}
        if awb_code:
            update_fields["awb"] = awb_code
        if courier_name and courier_company_id:
            courier, _ = await self.courier_service.upsert_synced_courier(
                source_system="shiprocket",
                external_id=str(courier_company_id),
                name=courier_name,
            )
            update_fields["courier_id"] = courier.id
        if update_fields:
            await self.shipments.update(shipment, **update_fields)

        await self.audit.record(
            user=actor,
            action="shipment.awb_assigned",
            entity_type="shipment",
            entity_id=str(shipment.id),
            new_value={"awb": awb_code, "courier": courier_name},
        )
        await self.session.commit()
        return shipment

    async def cancel_shipment(self, shipment_id: uuid.UUID, *, actor: User | None) -> Shipment:
        shipment = await self._get_shipment(shipment_id)
        if not shipment.shiprocket_shipment_id:
            raise ConflictError(
                "Shipment has no Shiprocket shipment id — nothing to cancel in Shiprocket."
            )

        adapter = self._get_adapter()
        await adapter.cancel_shipment([shipment.shiprocket_shipment_id])

        previous_status = shipment.current_status
        await self.shipments.update(shipment, current_status=ShipmentStatus.CANCELLED)

        await self.audit.record(
            user=actor,
            action="shipment.cancelled_via_shiprocket",
            entity_type="shipment",
            entity_id=str(shipment.id),
            previous_value={"status": previous_status.value},
            new_value={"status": ShipmentStatus.CANCELLED.value},
        )
        await self.session.commit()
        return shipment

    async def request_pickup(self, shipment_id: uuid.UUID, *, actor: User | None) -> Shipment:
        shipment = await self._get_shipment(shipment_id)
        if not shipment.shiprocket_shipment_id:
            raise ConflictError(
                "Shipment has no Shiprocket shipment id — create it via Shiprocket first."
            )

        adapter = self._get_adapter()
        response = await adapter.request_pickup(shipment.shiprocket_shipment_id)

        await self.shipment_service.add_tracking_event(
            shipment.id,
            external_event_id=None,
            status="PICKUP SCHEDULED",
            location=None,
            event_timestamp=datetime.now(UTC),
            description="Pickup requested via Shiprocket.",
            courier_name=None,
            source="shiprocket",
            raw_payload=response,
        )

        await self.audit.record(
            user=actor,
            action="shipment.pickup_requested",
            entity_type="shipment",
            entity_id=str(shipment.id),
            new_value={},
        )
        await self.session.commit()
        return await self._get_shipment(shipment_id)

    async def refresh_tracking_for_shipment(
        self, shipment_id: uuid.UUID, *, actor: User | None
    ) -> Shipment:
        shipment = await self._get_shipment(shipment_id)
        if not shipment.awb:
            raise ConflictError("Shipment has no AWB yet — assign one first.")

        adapter = self._get_adapter()
        raw_response = await adapter.get_tracking(shipment.awb)

        for raw_event in extract_tracking_events(raw_response):
            normalized = TRACKING_NORMALIZER.normalize_event(raw_event)
            if normalized["event_timestamp"] is None:
                continue
            await self.shipment_service.add_tracking_event(
                shipment.id,
                external_event_id=normalized["external_event_id"],
                status=normalized["status"],
                location=normalized["location"],
                event_timestamp=normalized["event_timestamp"],
                description=normalized["description"],
                courier_name=normalized["courier_name"],
                source="shiprocket",
                raw_payload=normalized["raw_payload"],
            )
            if normalized["mapped_status"] is not None:
                await self.shipment_service.update_shipment(
                    shipment.id, actor=actor, current_status=normalized["mapped_status"]
                )

        await self.audit.record(
            user=actor,
            action="shipment.tracking_refreshed",
            entity_type="shipment",
            entity_id=str(shipment.id),
            new_value={},
        )
        await self.session.commit()
        return await self._get_shipment(shipment_id)

    async def ndr_reattempt(
        self,
        ndr_id: uuid.UUID,
        *,
        actor: User | None,
        address_1: str,
        address_2: str | None,
        phone: str,
    ) -> NDR:
        ndr = await self.ndr_service.get_ndr(ndr_id)
        shipment = await self.shipments.get_by_id(ndr.shipment_id)
        if shipment is None or not shipment.awb:
            raise ConflictError("This NDR's shipment has no AWB — cannot request a reattempt.")

        adapter = self._get_adapter()
        await adapter.ndr_reattempt(
            awb=shipment.awb, address_1=address_1, address_2=address_2, phone=phone
        )

        updated = await self.ndr_service.update_ndr(
            ndr_id,
            actor=actor,
            status=NDRStatus.REATTEMPT_SCHEDULED,
            reattempt_status="requested",
            customer_response=f"Reattempt requested: {address_1}",
        )

        await self.audit.record(
            user=actor,
            action="ndr.reattempt_requested",
            entity_type="ndr",
            entity_id=str(ndr_id),
            new_value={"address_1": address_1, "phone": phone},
        )
        await self.session.commit()
        return updated
