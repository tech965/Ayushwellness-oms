"""NimbusPost tracking-webhook processing (New/v2 B2C format only).

Mirrors `app.services.shiprocket_webhook_service.ShiprocketWebhookService`
exactly: matches an inbound webhook delivery to an existing OMS
`Shipment`, then applies the update via the SAME shared
`apply_tracking_event` every other ingestion path (Shiprocket pull-sync,
Shiprocket webhook, Shiprocket manual refresh) already uses -- so no
separate inventory-deduction or RTO-restocking path is ever introduced
for a second courier. Kept separate from `app.api.v1.webhooks.couriers`
(the FastAPI layer -- signature verification, JSON parsing, idempotent
`WebhookEvent` ingestion) for the same unit-testability reason
`shiprocket_webhook_service.py` is split from its own route.

Three event types, three narrow responsibilities (business decision,
Module 5):
  - `tracking.updated`: authoritative for shipment status/RTO
    transitions -- the only event type that calls `apply_tracking_event`
    with a real `mapped_status`.
  - `order.updated`: ONLY AWB/identifier backfill ("booked"/"" status)
    and cancellation -- never a channel for shipped/in-transit/delivered/
    rto-* status, which `tracking.updated` already owns. See
    `app.integrations.nimbuspost.normalizer.order_updated_kind`.
  - `order.created`: recorded for idempotency/audit only -- an
    order-number match confirms NimbusPost knows about this order, but
    nothing is invented or overwritten (no AWB/status to apply yet).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.integrations.nimbuspost.normalizer import (
    NIMBUSPOST_TRACKING_WEBHOOK_NORMALIZER,
    UNSUPPORTED_STATUS_CODES,
    extract_webhook_identifiers,
    order_updated_kind,
)
from app.integrations.shiprocket.sync import apply_tracking_event
from app.models.enums import ShipmentStatus
from app.models.mixins import SourceSystem
from app.models.order import Order
from app.models.shipment import Shipment
from app.repositories.order import OrderRepository
from app.repositories.rto import RTORepository
from app.repositories.shipment import ShipmentRepository
from app.services.inventory_service import InventoryService
from app.services.rto_service import RTOService
from app.services.shipment_service import ShipmentService

logger = get_logger(__name__)

_SOURCE = "nimbuspost_webhook"


class NimbusPostWebhookMatchResult:
    """Mirrors `ShiprocketWebhookService.WebhookMatchResult` exactly --
    never raised for an ordinary "no safe match" outcome (ack 200,
    record for reconciliation); only a genuine processing failure
    propagates out of these methods.
    """

    def __init__(
        self, *, matched: bool, match_strategy: str, shipment_id: uuid.UUID | None
    ) -> None:
        self.matched = matched
        self.match_strategy = match_strategy
        self.shipment_id = shipment_id


class NimbusPostWebhookService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.shipments = ShipmentRepository(session)
        self.orders = OrderRepository(session)
        self.rtos = RTORepository(session)
        self.shipment_service = ShipmentService(session)
        self.rto_service = RTOService(session)
        self.inventory_service = InventoryService(session)

    # --- tracking.updated ------------------------------------------------

    async def apply_tracking_webhook(self, payload: dict[str, Any]) -> NimbusPostWebhookMatchResult:
        ids = extract_webhook_identifiers(payload)
        shipment, match_strategy = await self._find_or_create_shipment(ids)
        if shipment is None:
            return NimbusPostWebhookMatchResult(
                matched=False, match_strategy=match_strategy, shipment_id=None
            )

        status_code = (payload.get("status_code") or "").strip()
        if status_code in UNSUPPORTED_STATUS_CODES:
            # Business decision: damaged/lost are recorded (the
            # ShipmentEvent write below still happens, via
            # `apply_tracking_event` -- `mapped_status` is simply `None`
            # for these codes) but never drive a state transition, never
            # dispatch, never RTO. Logged explicitly here too since this
            # is the one case worth a dedicated structured log line, not
            # just "no mapped_status found".
            logger.warning(
                "nimbuspost_webhook_unsupported_status",
                shipment_id=str(shipment.id),
                status_code=status_code,
            )

        normalized = NIMBUSPOST_TRACKING_WEBHOOK_NORMALIZER.normalize_event(payload)
        if normalized["event_timestamp"] is None:
            normalized = {**normalized, "event_timestamp": datetime.now(UTC)}

        await apply_tracking_event(
            self.session,
            shipment,
            normalized,
            shipment_service=self.shipment_service,
            rto_service=self.rto_service,
            inventory_service=self.inventory_service,
            source=_SOURCE,
            source_system=SourceSystem.NIMBUSPOST,
        )

        await self._apply_identifier_updates(shipment, ids)
        await self._record_rto_awb(shipment, payload)

        return NimbusPostWebhookMatchResult(
            matched=True, match_strategy=match_strategy, shipment_id=shipment.id
        )

    async def _record_rto_awb(self, shipment: Shipment, payload: dict[str, Any]) -> None:
        """Stores `rto_awb` (the courier's separate return-leg AWB, when
        it issues one) in `RTO.rto_metadata` -- the existing JSON field
        already used for courier-specific extras (`normalized_reason`/
        `external_reason` are columns; anything not confirmed to need
        its own column goes here instead, same convention Module 1
        already established). Only written once the RTO row this
        delivery's `process_courier_rto_status` call just
        created/updated can be looked up by the exact same
        (source_system, external_id) key it was upserted under -- never
        guessed, never a second, parallel RTO lookup path.
        """
        rto_awb = (payload.get("rto_awb") or "").strip()
        if not rto_awb or not shipment.awb:
            return
        rto = await self.rtos.get_by_source_external_id(
            source_system=SourceSystem.NIMBUSPOST, external_id=shipment.awb
        )
        if rto is None:
            return
        existing_metadata = rto.rto_metadata or {}
        if existing_metadata.get("rto_awb") == rto_awb:
            return  # already recorded, idempotent
        await self.rtos.update(rto, rto_metadata={**existing_metadata, "rto_awb": rto_awb})
        await self.session.commit()

    # --- order.updated -----------------------------------------------------

    async def apply_order_updated_webhook(
        self, payload: dict[str, Any]
    ) -> NimbusPostWebhookMatchResult:
        ids = extract_webhook_identifiers(payload)
        shipment, match_strategy = await self._find_or_create_shipment(ids)
        if shipment is None:
            return NimbusPostWebhookMatchResult(
                matched=False, match_strategy=match_strategy, shipment_id=None
            )

        kind = order_updated_kind(payload.get("status") or "")
        if kind == "awb_update":
            await self._apply_identifier_updates(shipment, ids)
        elif kind == "cancelled":
            normalized = {
                "external_event_id": None,
                "status": payload.get("status") or "cancelled",
                "mapped_status": ShipmentStatus.CANCELLED,
                "location": None,
                "event_timestamp": self._parse_event_time(payload) or datetime.now(UTC),
                "description": payload.get("message") or "Cancelled (order.updated)",
                "courier_name": payload.get("courier_name") or None,
                "expected_delivery_date": None,
                "raw_payload": payload,
            }
            await apply_tracking_event(
                self.session,
                shipment,
                normalized,
                shipment_service=self.shipment_service,
                rto_service=self.rto_service,
                inventory_service=self.inventory_service,
                source=_SOURCE,
                source_system=SourceSystem.NIMBUSPOST,
            )
        else:
            # "not_handled": shipped/in transit/out for delivery/
            # delivered/rto * -- tracking.updated already owns these
            # transitions (see module docstring). Still a real match
            # (we found the shipment), just nothing to apply from THIS
            # event; the caller records it accordingly.
            return NimbusPostWebhookMatchResult(
                matched=True,
                match_strategy=f"{match_strategy}_not_handled",
                shipment_id=shipment.id,
            )

        return NimbusPostWebhookMatchResult(
            matched=True, match_strategy=match_strategy, shipment_id=shipment.id
        )

    # --- order.created -------------------------------------------------

    async def apply_order_created_webhook(
        self, payload: dict[str, Any]
    ) -> NimbusPostWebhookMatchResult:
        """Never creates or mutates anything -- `order.created` carries
        no AWB/courier/status information worth applying (see module
        docstring). A resolved `order_number` only confirms NimbusPost
        has registered this order; the match result is purely for the
        route's `WebhookEvent` bookkeeping.
        """
        ids = extract_webhook_identifiers(payload)
        order: Order | None = None
        if ids["order_number"]:
            order = await self.orders.get_by_order_number(ids["order_number"])
        if order is None:
            return NimbusPostWebhookMatchResult(
                matched=False, match_strategy="unmatched", shipment_id=None
            )
        shipments = await self.shipments.list_for_order(order.id)
        shipment_id = shipments[0].id if len(shipments) == 1 else None
        return NimbusPostWebhookMatchResult(
            matched=True, match_strategy="order_number", shipment_id=shipment_id
        )

    # --- shared matching/identifier helpers -----------------------------

    async def _find_or_create_shipment(
        self, ids: dict[str, str | None]
    ) -> tuple[Shipment | None, str]:
        """Preferred matching order: AWB -> NimbusPost's own order id (once
        a prior delivery has let us record one via `_apply_identifier_
        updates`) -> your own `order_number` (confirmed present on every
        NimbusPost B2C event -- no live API call needed, unlike
        Shiprocket's `GET /orders/show/{id}` fallback). Never falls back
        to name/phone/address matching.
        """
        if ids["awb"]:
            shipment = await self.shipments.get_by_awb(ids["awb"])
            if shipment is not None:
                return shipment, "awb"

        if ids["nimbuspost_order_id"]:
            shipment = await self.shipments.get_by_source_external_id(
                source_system=SourceSystem.NIMBUSPOST, external_id=ids["nimbuspost_order_id"]
            )
            if shipment is not None:
                return shipment, "nimbuspost_order_id"

        order: Order | None = None
        if ids["order_number"]:
            order = await self.orders.get_by_order_number(ids["order_number"])
        if order is None:
            return None, "unmatched"

        existing = await self.shipments.list_for_order(order.id)
        if len(existing) == 1:
            return existing[0], "order_number"
        if len(existing) > 1:
            # Can't tell which of several shipments on this order the
            # webhook refers to from an order-level match alone — same
            # "never guess" rule as Shiprocket's own matcher.
            return None, "ambiguous_multiple_shipments_for_order"

        new_shipment_external_id = ids["nimbuspost_order_id"] or ids["awb"]
        if new_shipment_external_id is None:
            return None, "order_resolved_no_shipment_identifier"

        shipment, _created = await self.shipment_service.upsert_synced_shipment(
            source_system=SourceSystem.NIMBUSPOST,
            external_id=new_shipment_external_id,
            order_id=order.id,
            awb=ids["awb"],
        )
        return shipment, "order_number_created_shipment"

    async def _apply_identifier_updates(
        self, shipment: Shipment, ids: dict[str, str | None]
    ) -> None:
        """Mirrors `ShiprocketWebhookService._apply_identifier_updates`:
        fills in `awb` when this shipment didn't already have one, never
        overwrites an existing value with an absent one.
        """
        updates: dict[str, Any] = {}
        if ids["awb"] and shipment.awb != ids["awb"]:
            updates["awb"] = ids["awb"]
        if updates:
            await self.shipment_service.update_shipment(shipment.id, actor=None, **updates)

    def _parse_event_time(self, payload: dict[str, Any]) -> datetime | None:
        raw = (payload.get("event_time") or "").strip()
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        except ValueError:
            return None
