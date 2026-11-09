"""Live (API-first) Shiprocket operations.

Shiprocket is the source of truth for shipment status, courier, tracking
history and NDR/RTO. `shipment_refs` holds only the identifiers needed to
ask Shiprocket about the right shipment (plus OMS bookkeeping about
actions the OMS performed). Every read here goes to Shiprocket; every
returned `Shipment`/`ShipmentEvent` is a transient, never-flushed object
(see `app.services.live_orders` for the flush guard).

Shipment creation needs no local `orders` row: the order is fetched live
from Shopify and the Shiprocket request is built from it, with the
Shopify order id as the external reference.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, IntegrationError, NotFoundError
from app.core.logging import get_logger
from app.integrations.registry import get_adapter
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.integrations.shiprocket.config import ShiprocketConfig
from app.integrations.shiprocket.normalizer import TRACKING_NORMALIZER, extract_tracking_events
from app.integrations.shopify.adapter import ShopifyAdapter
from app.models.auth import User
from app.models.enums import (
    FulfillmentStatus,
    NDRStatus,
    OrderStatus,
    RTOStatus,
    ShipmentDelayStatus,
    ShipmentStatus,
    ShopifySyncStatus,
)
from app.models.integration import IntegrationCode
from app.models.order_workflow import OrderWorkflow
from app.models.shipment import Shipment, ShipmentEvent
from app.models.shipment_ref import ShipmentRef
from app.repositories.order import OrderRepository
from app.services.audit_service import AuditService
from app.services.inventory_reservation_service import InventoryReservationService
from app.services.inventory_service import InventoryService
from app.services.order_ref import resolve_ref

logger = get_logger(__name__)

_DISPATCHED = {
    ShipmentStatus.PICKED_UP,
    ShipmentStatus.IN_TRANSIT,
    ShipmentStatus.OUT_FOR_DELIVERY,
    ShipmentStatus.DELIVERED,
    ShipmentStatus.NDR,
}
_NS = uuid.UUID("0b1d6a5e-3c4f-4e55-8f52-7a31f9c2d0b7")


class LiveShipmentState:
    def __init__(
        self,
        status: ShipmentStatus,
        events: list[dict[str, Any]],
        courier_name: str | None,
    ) -> None:
        self.status = status
        self.events = events
        self.courier_name = courier_name

    @property
    def last(self) -> dict[str, Any] | None:
        return self.events[-1] if self.events else None


def _mark_transient(*objects: object) -> None:
    for obj in objects:
        obj._live_transient = True  # type: ignore[attr-defined]  # noqa: SLF001


class LiveShiprocketService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)
        self.orders = OrderRepository(session)

    # --- plumbing ------------------------------------------------------------

    def _adapter(self) -> ShiprocketAdapter:
        adapter = get_adapter(IntegrationCode.SHIPROCKET)
        if adapter is None:
            raise IntegrationError(
                "No adapter registered for integration 'shiprocket'.",
                details={"error_type": "integration_error"},
            )
        return adapter  # type: ignore[return-value]

    async def get_ref(self, ref_id: uuid.UUID) -> ShipmentRef | None:
        return await self.session.get(ShipmentRef, ref_id)

    async def _require_ref(self, ref_id: uuid.UUID) -> ShipmentRef:
        ref = await self.get_ref(ref_id)
        if ref is None:
            raise NotFoundError("Shipment not found.")
        return ref

    async def refs_for_order(self, order_ref_id: uuid.UUID) -> list[ShipmentRef]:
        rows = await self.session.execute(
            select(ShipmentRef)
            .where(ShipmentRef.order_id == order_ref_id)
            .order_by(ShipmentRef.created_at)
        )
        return list(rows.scalars().all())

    # --- live state ---------------------------------------------------------------

    async def live_state(self, ref: ShipmentRef) -> LiveShipmentState:
        if ref.cancelled_at is not None:
            return LiveShipmentState(ShipmentStatus.CANCELLED, [], None)
        if not ref.awb:
            return LiveShipmentState(ShipmentStatus.PENDING, [], None)
        raw = await self._adapter().get_tracking(ref.awb)
        events = []
        for raw_event in extract_tracking_events(raw):
            normalized = TRACKING_NORMALIZER.normalize_event(raw_event)
            if normalized["event_timestamp"] is not None:
                events.append(normalized)
        events.sort(key=lambda e: e["event_timestamp"])
        status = ShipmentStatus.PENDING
        for event in events:
            if event["mapped_status"] is not None:
                status = event["mapped_status"]
        courier = next((e["courier_name"] for e in reversed(events) if e["courier_name"]), None)
        return LiveShipmentState(status, events, courier)

    def to_shipment(self, ref: ShipmentRef, state: LiveShipmentState) -> Shipment:
        last = state.last
        rto = None
        if state.status == ShipmentStatus.RTO_INITIATED:
            rto = RTOStatus.INITIATED
        elif state.status == ShipmentStatus.RTO_DELIVERED:
            rto = RTOStatus.RECEIVED
        shipment = Shipment(
            id=ref.id,
            order_id=ref.order_id,
            shiprocket_shipment_id=ref.shiprocket_shipment_id,
            awb=ref.awb,
            courier_id=None,
            current_status=state.status,
            delay_status=ShipmentDelayStatus.UNKNOWN,
            ndr_status=NDRStatus.OPEN if state.status == ShipmentStatus.NDR else None,
            rto_status=rto,
            pickup_date=None,
            expected_delivery_date=None,
            actual_delivery_date=(
                last["event_timestamp"]
                if last and state.status == ShipmentStatus.DELIVERED
                else None
            ),
            current_location=last["location"] if last else None,
            last_tracking_update_at=last["event_timestamp"] if last else None,
            source_system="shiprocket",
            shopify_fulfillment_id=ref.shopify_fulfillment_id,
            shopify_sync_status=ref.shopify_sync_status,
            shopify_sync_error=ref.shopify_sync_error,
            shopify_synced_at=ref.shopify_synced_at,
            created_at=ref.created_at,
            updated_at=ref.updated_at,
        )
        shipment.courier = None
        _mark_transient(shipment)
        return shipment

    async def get_shipment(self, ref_id: uuid.UUID) -> Shipment:
        ref = await self._require_ref(ref_id)
        return self.to_shipment(ref, await self.live_state(ref))

    async def shipments_for_order(self, order_ref_id: uuid.UUID) -> list[Shipment]:
        return [await self.get_shipment(ref.id) for ref in await self.refs_for_order(order_ref_id)]

    async def timeline(self, ref_id: uuid.UUID) -> list[ShipmentEvent]:
        ref = await self._require_ref(ref_id)
        state = await self.live_state(ref)
        events = []
        for index, e in enumerate(state.events):
            event = ShipmentEvent(
                id=uuid.uuid5(_NS, f"{ref.id}:{e['external_event_id'] or index}"),
                shipment_id=ref.id,
                external_event_id=e["external_event_id"],
                status=e["status"],
                location=e["location"],
                event_timestamp=e["event_timestamp"],
                description=e["description"],
                courier_name=e["courier_name"],
                source="shiprocket",
                created_at=e["event_timestamp"],
            )
            _mark_transient(event)
            events.append(event)
        return events

    # --- actions --------------------------------------------------------------------

    async def _eligibility(self, order: Any) -> None:
        if order.status != OrderStatus.CONFIRMED:
            raise ConflictError(
                f"Order is '{order.status.value}', not confirmed — cannot ship.",
                details={"error_type": "not_confirmed"},
            )
        if order.fulfillment_status == FulfillmentStatus.FULFILLED:
            raise ConflictError(
                "Order is already fulfilled.", details={"error_type": "already_fulfilled"}
            )
        if not order.shipping_address:
            raise ConflictError(
                "Order has no shipping address on file.",
                details={"error_type": "missing_shipping_address"},
            )
        for ref in await self.refs_for_order(order.id):
            if (await self.live_state(ref)).status != ShipmentStatus.CANCELLED:
                raise ConflictError(
                    "A shipment already exists for this order — refresh the page instead of "
                    "creating another one.",
                    details={"error_type": "shipment_already_exists"},
                )

    async def _reserve_stock(self, order: Any, order_ref_id: uuid.UUID) -> None:
        """Reserve boxes for every line whose SKU resolves to an OMS variant.
        An unresolvable SKU is skipped (a data-linking gap, not a shortage),
        matching `InventoryService.apply_dispatch`."""
        inventory = InventoryService(self.session)
        lines: list[tuple[uuid.UUID, int]] = []
        for item in order.items:
            if item.quantity <= 0:
                continue
            variant = await inventory._resolve_variant(  # noqa: SLF001
                product_variant_id=item.product_variant_id, sku=item.sku
            )
            if variant is None:
                logger.warning("live_reservation_unresolved_sku", sku=item.sku)
                continue
            lines.append((variant.id, item.quantity))
        try:
            await InventoryReservationService(self.session).reserve(order_ref_id, lines)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

    async def create_for_order(
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
        await self._eligibility(order)

        config = ShiprocketConfig.from_settings()
        if config is None or not config.pickup_location:
            raise IntegrationError(
                "Shiprocket is not configured with a pickup location "
                "(SHIPROCKET_EMAIL/SHIPROCKET_PASSWORD/SHIPROCKET_PICKUP_LOCATION).",
                details={"error_type": "not_configured"},
            )
        # The workflow row is the FK target for the reservation and the
        # shipment reference; make sure it exists for a live-only order
        # (idempotent). Only identifiers are stored.
        from app.services.live_orders import is_live_transient
        from app.services.order_ref import ensure_workflow

        order_ref_id = order.id
        if is_live_transient(order):
            order_ref_id = (await ensure_workflow(self.session, order._live_external_id)).id  # type: ignore[attr-defined]  # noqa: SLF001

        # Set the stock aside first (all-or-nothing, row-locked, committed) so
        # the lock is not held across the Shiprocket call and two concurrent
        # shipments cannot both promise the last boxes.
        await self._reserve_stock(order, order_ref_id)
        try:
            response = await self._adapter().create_order(
                order,
                pickup_location=config.pickup_location,
                length_cm=length_cm,
                breadth_cm=breadth_cm,
                height_cm=height_cm,
                weight_kg=weight_kg,
            )
            shipment_id = response.get("shipment_id")
            if not shipment_id:
                raise IntegrationError(
                    "Shiprocket order creation response did not include a shipment_id.",
                    details={"error_type": "validation_error"},
                )
        except Exception:
            await self.session.rollback()
            await InventoryReservationService(self.session).release(order_ref_id)
            await self.session.commit()
            raise
        ref = ShipmentRef(
            order_id=order_ref_id,
            shiprocket_order_id=str(response["order_id"]) if response.get("order_id") else None,
            shiprocket_shipment_id=str(shipment_id),
            shopify_sync_status=(
                ShopifySyncStatus.PENDING
                if order.shopify_order_id
                else ShopifySyncStatus.NOT_APPLICABLE
            ),
            created_by=actor.id if actor else None,
        )
        self.session.add(ref)
        await self.session.flush()
        await self.audit.record(
            user=actor,
            action="shipment.created_via_shiprocket",
            entity_type="shipment",
            entity_id=str(ref.id),
            new_value={
                "order_id": str(order_ref_id),
                "shiprocket_order_id": ref.shiprocket_order_id,
                "shiprocket_shipment_id": ref.shiprocket_shipment_id,
            },
        )
        await self.session.commit()
        return self.to_shipment(ref, LiveShipmentState(ShipmentStatus.PENDING, [], None))

    async def assign_awb(
        self, ref_id: uuid.UUID, *, actor: User | None, courier_id: str | None
    ) -> Shipment:
        ref = await self._require_ref(ref_id)
        if not ref.shiprocket_shipment_id:
            raise ConflictError(
                "Shipment has no Shiprocket shipment id — create it via Shiprocket first."
            )
        response = await self._adapter().assign_awb(
            ref.shiprocket_shipment_id, courier_id=courier_id
        )
        data = response.get("response", {}).get("data", response)
        awb = data.get("awb_code")
        courier_name = data.get("courier_name")
        if awb:
            ref.awb = awb
        await self.audit.record(
            user=actor,
            action="shipment.awb_assigned",
            entity_type="shipment",
            entity_id=str(ref.id),
            new_value={"awb": awb, "courier": courier_name},
        )
        await self.session.commit()
        if awb:
            await self._push_shopify_fulfillment(ref, awb=awb, courier_name=courier_name)
        return await self.get_shipment(ref.id)

    async def _push_shopify_fulfillment(
        self, ref: ShipmentRef, *, awb: str, courier_name: str | None
    ) -> None:
        """Best-effort OMS -> Shopify fulfilment with tracking. Never raises:
        a failure is recorded on the reference (retryable) and the already
        successful AWB assignment stands."""
        workflow = await self.session.get(OrderWorkflow, ref.order_id)
        shopify = get_adapter(IntegrationCode.SHOPIFY)
        if workflow is None or not isinstance(shopify, ShopifyAdapter):
            return
        if workflow.external_order_id.startswith("local:"):
            return
        gid = f"gid://shopify/Order/{workflow.external_order_id}"
        try:
            fulfillment_order_id = await shopify.get_open_fulfillment_order_id(gid)
            if fulfillment_order_id is None:
                raise IntegrationError(
                    "No open Shopify fulfillment order (already fulfilled?).",
                    details={"error_type": "validation_error"},
                )
            result = await shopify.create_fulfillment(
                fulfillment_order_id=fulfillment_order_id,
                tracking_number=awb,
                tracking_company=courier_name,
                tracking_url=None,
            )
        except Exception as exc:  # noqa: BLE001 - never fail the AWB assignment
            ref.shopify_sync_status = ShopifySyncStatus.FAILED
            ref.shopify_sync_error = str(getattr(exc, "message", exc))[:500]
            logger.warning("live_shopify_fulfillment_push_failed", shipment_ref=str(ref.id))
        else:
            ref.shopify_sync_status = ShopifySyncStatus.SYNCED
            ref.shopify_sync_error = None
            ref.shopify_synced_at = datetime.now(UTC)
            fulfillment_id = (result or {}).get("id")
            ref.shopify_fulfillment_id = (
                str(fulfillment_id).rsplit("/", 1)[-1] if fulfillment_id else None
            )
        await self.session.commit()

    async def retry_shopify_sync(self, ref_id: uuid.UUID) -> Shipment:
        """Re-pushes the Shopify fulfilment for a shipment whose earlier push
        failed (idempotent on Shopify's side once an order is fulfilled)."""
        ref = await self._require_ref(ref_id)
        if not ref.awb:
            raise ConflictError("Shipment has no AWB yet — assign one first.")
        if ref.shopify_sync_status != ShopifySyncStatus.SYNCED:
            state = await self.live_state(ref)
            await self._push_shopify_fulfillment(ref, awb=ref.awb, courier_name=state.courier_name)
        return await self.get_shipment(ref.id)

    async def cancel(self, ref_id: uuid.UUID, *, actor: User | None) -> Shipment:
        ref = await self._require_ref(ref_id)
        if not ref.shiprocket_shipment_id:
            raise ConflictError(
                "Shipment has no Shiprocket shipment id — nothing to cancel in Shiprocket."
            )
        await self._adapter().cancel_shipment([ref.shiprocket_shipment_id])
        ref.cancelled_at = datetime.now(UTC)
        await InventoryReservationService(self.session).release(ref.order_id)
        await self.audit.record(
            user=actor,
            action="shipment.cancelled_via_shiprocket",
            entity_type="shipment",
            entity_id=str(ref.id),
            new_value={"status": ShipmentStatus.CANCELLED.value},
        )
        await self.session.commit()
        return self.to_shipment(ref, LiveShipmentState(ShipmentStatus.CANCELLED, [], None))

    async def request_pickup(self, ref_id: uuid.UUID, *, actor: User | None) -> Shipment:
        ref = await self._require_ref(ref_id)
        if not ref.shiprocket_shipment_id:
            raise ConflictError(
                "Shipment has no Shiprocket shipment id — create it via Shiprocket first."
            )
        await self._adapter().request_pickup(ref.shiprocket_shipment_id)
        await self.audit.record(
            user=actor,
            action="shipment.pickup_requested",
            entity_type="shipment",
            entity_id=str(ref.id),
            new_value={},
        )
        await self.session.commit()
        return await self.get_shipment(ref.id)

    async def refresh(self, ref_id: uuid.UUID, *, actor: User | None) -> Shipment:
        """Reads tracking live. The only write is the OMS-owned inventory
        movement, applied once when the shipment is first seen dispatched."""
        ref = await self._require_ref(ref_id)
        if not ref.awb:
            raise ConflictError("Shipment has no AWB yet — assign one first.")
        state = await self.live_state(ref)
        if state.status in _DISPATCHED:
            order = await self.orders.get_by_id_with_items_and_customer(ref.order_id)
            if order is not None:
                await InventoryService(self.session).apply_dispatch(
                    order_id=ref.order_id, shipment_id=ref.id, items=list(order.items)
                )
        await self.audit.record(
            user=actor,
            action="shipment.tracking_refreshed",
            entity_type="shipment",
            entity_id=str(ref.id),
            new_value={},
        )
        await self.session.commit()
        return self.to_shipment(ref, state)

    # --- lists -------------------------------------------------------------------------

    async def _states(self, refs: list[ShipmentRef]) -> list[LiveShipmentState]:
        """Live state for many refs with bounded concurrency."""
        gate = asyncio.Semaphore(5)

        async def one(ref: ShipmentRef) -> LiveShipmentState:
            async with gate:
                return await self.live_state(ref)

        return list(await asyncio.gather(*(one(r) for r in refs)))

    async def list_shipments(
        self,
        *,
        page: int,
        page_size: int,
        order_ref_id: uuid.UUID | None = None,
        status: str | None = None,
        q: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[Shipment], int, bool]:
        """Shipments the OMS knows about (its `shipment_refs`), with status
        read live from Shiprocket for the requested page. `status` is
        applied after the live read, so when it is set the total is an
        estimate (third return value True)."""
        conditions = []
        if order_ref_id is not None:
            conditions.append(ShipmentRef.order_id == order_ref_id)
        if q:
            like = f"%{q}%"
            conditions.append(
                or_(ShipmentRef.awb.ilike(like), ShipmentRef.shiprocket_order_id.ilike(like))
            )
        if date_from:
            conditions.append(ShipmentRef.created_at >= date_from)
        if date_to:
            conditions.append(ShipmentRef.created_at <= date_to)
        total = (
            await self.session.execute(
                select(func.count()).select_from(ShipmentRef).where(*conditions)
            )
        ).scalar_one()
        rows = list(
            (
                await self.session.execute(
                    select(ShipmentRef)
                    .where(*conditions)
                    .order_by(ShipmentRef.created_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
        states = await self._states(rows)
        shipments = [self.to_shipment(r, s) for r, s in zip(rows, states, strict=True)]
        if status:
            shipments = [s for s in shipments if s.current_status.value == status]
        await self._attach_orders(shipments)
        return shipments, total, bool(status)

    async def _attach_orders(self, shipments: list[Shipment]) -> None:
        """Attach each shipment's live order (batched) for list columns."""
        from app.services.live_orders import get_reader, hydrate_order

        workflows = {
            w.id: w
            for w in (
                await self.session.execute(
                    select(OrderWorkflow).where(
                        OrderWorkflow.id.in_({s.order_id for s in shipments})
                    )
                )
            )
            .scalars()
            .all()
        }
        external = [
            w.external_order_id
            for w in workflows.values()
            if not w.external_order_id.startswith("local:")
        ]
        live = (
            {o["external_id"]: o for o in await get_reader().get_orders_by_ids(external)}
            if external
            else {}
        )
        for shipment in shipments:
            workflow = workflows.get(shipment.order_id)
            data = live.get(workflow.external_order_id) if workflow else None
            shipment.order = hydrate_order(data, workflow) if data else None  # type: ignore[assignment]

    async def queue(
        self,
        *,
        page_params: Any,
        q: str | None,
        payment_type: str | None,
        telecaller_id: uuid.UUID | None,
        sku: str | None,
        date_from: datetime | None,
        date_to: datetime | None,
        scope_telecaller_ids: list[uuid.UUID] | None,
    ) -> tuple[list[tuple[Any, Shipment | None]], int]:
        """Confirmed orders with no active shipment, paged from
        `order_workflow` (OMS state) and hydrated live from Shopify."""
        from app.services.live_orders import get_reader, hydrate_order

        active_ref = (
            select(ShipmentRef.id)
            .where(ShipmentRef.order_id == OrderWorkflow.id, ShipmentRef.cancelled_at.is_(None))
            .exists()
        )
        conditions = [OrderWorkflow.status == OrderStatus.CONFIRMED, ~active_ref]
        if telecaller_id is not None:
            conditions.append(OrderWorkflow.confirmed_by_telecaller_id == telecaller_id)
        if scope_telecaller_ids is not None:
            conditions.append(OrderWorkflow.confirmed_by_telecaller_id.in_(scope_telecaller_ids))
        total = (
            await self.session.execute(
                select(func.count()).select_from(OrderWorkflow).where(*conditions)
            )
        ).scalar_one()
        workflows = list(
            (
                await self.session.execute(
                    select(OrderWorkflow)
                    .where(*conditions)
                    .order_by(OrderWorkflow.confirmed_at.desc().nulls_last())
                    .offset((page_params.page - 1) * page_params.page_size)
                    .limit(page_params.page_size)
                )
            )
            .scalars()
            .all()
        )
        by_external = {w.external_order_id: w for w in workflows}
        live = await get_reader().get_orders_by_ids(
            [e for e in by_external if not e.startswith("local:")]
        )
        rows: list[tuple[Any, Shipment | None]] = []
        needle = q.lower() if q else None
        for data in live:
            order = hydrate_order(data, by_external[data["external_id"]])
            if payment_type and order.payment_type.value != payment_type:
                continue
            if sku and not any(sku.lower() in (i.sku or "").lower() for i in order.items):
                continue
            if date_from and order.order_datetime < date_from:
                continue
            if date_to and order.order_datetime > date_to:
                continue
            if (
                needle
                and needle not in order.order_number.lower()
                and needle
                not in ((order.customer.full_name or "").lower() if order.customer else "")
            ):
                continue
            rows.append((order, None))
        return rows, total

    # --- NDR -------------------------------------------------------------------------

    async def list_ndr(self, *, page: int = 1, per_page: int = 50) -> dict[str, Any]:
        """Live NDR list straight from Shiprocket, joined (by AWB) to the
        OMS order reference when this OMS created/knows the shipment."""
        fetched = await self._adapter().fetch("ndr", cursor=str(page), limit=per_page)
        awbs = []
        rows = []
        for node in fetched.nodes:
            data = self._adapter().normalize("ndr", node)
            rows.append(data)
            if data.get("awb"):
                awbs.append(data["awb"])
        refs = {}
        if awbs:
            found = await self.session.execute(select(ShipmentRef).where(ShipmentRef.awb.in_(awbs)))
            refs = {r.awb: r for r in found.scalars().all()}
        return {
            "items": [
                {
                    **{k: v for k, v in row.items() if k != "raw_external_payload"},
                    "shipment_id": str(refs[row["awb"]].id) if row.get("awb") in refs else None,
                    "order_id": str(refs[row["awb"]].order_id) if row.get("awb") in refs else None,
                }
                for row in rows
            ],
            "has_more": fetched.has_more,
            "next_page": int(fetched.next_cursor) if fetched.next_cursor else None,
        }

    async def ndr_reattempt(
        self,
        ref_id: uuid.UUID,
        *,
        actor: User | None,
        address_1: str,
        address_2: str | None,
        phone: str,
    ) -> dict[str, Any]:
        ref = await self._require_ref(ref_id)
        if not ref.awb:
            raise ConflictError("This shipment has no AWB — cannot request a reattempt.")
        await self._adapter().ndr_reattempt(
            awb=ref.awb, address_1=address_1, address_2=address_2, phone=phone
        )
        await self.audit.record(
            user=actor,
            action="ndr.reattempt_requested",
            entity_type="shipment",
            entity_id=str(ref.id),
            new_value={"address_1": address_1, "phone": phone},
        )
        await self.session.commit()
        return {"requested": True, "awb": ref.awb}


async def live_ref_for(session: AsyncSession, ref_id: uuid.UUID) -> ShipmentRef | None:
    return await session.get(ShipmentRef, ref_id)


async def order_ref_id(session: AsyncSession, order_id: uuid.UUID) -> uuid.UUID | None:
    ref = await resolve_ref(session, order_id)
    return ref.ref_id if ref is not None else None


async def handle_tracking_webhook(
    session: AsyncSession, topic: str, payload: dict[str, Any]
) -> None:
    """Shiprocket tracking webhook (live mode). Reads only the AWB and the
    status; stores nothing from the payload. When the shipment is first
    seen dispatched, applies the OMS-owned inventory movement (idempotent
    per order+variant). Unknown AWBs are ignored."""
    from app.integrations.shiprocket.normalizer import WEBHOOK_TRACKING_NORMALIZER

    awb = payload.get("awb") or payload.get("awb_code")
    if not awb:
        return
    ref = (
        await session.execute(select(ShipmentRef).where(ShipmentRef.awb == str(awb)))
    ).scalar_one_or_none()
    if ref is None:
        logger.info("live_shiprocket_webhook_unknown_awb")
        return
    mapped = WEBHOOK_TRACKING_NORMALIZER.normalize_event(payload)["mapped_status"]
    if mapped in _DISPATCHED:
        order = await OrderRepository(session).get_by_id_with_items_and_customer(ref.order_id)
        if order is not None:
            await InventoryService(session).apply_dispatch(
                order_id=ref.order_id, shipment_id=ref.id, items=list(order.items)
            )
