from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.auth import User
from app.models.enums import InventoryMovementType, RTORestockStatus, RTOStatus
from app.models.rto import RTO
from app.repositories.rto import RTORepository
from app.repositories.shipment import ShipmentRepository
from app.schemas.common import PageParams, SortParams
from app.services.audit_service import AuditService
from app.services.inventory_service import InventoryService


class RTOService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.rtos = RTORepository(session)
        self.shipments = ShipmentRepository(session)
        self.audit = AuditService(session)

    async def list_rtos(
        self,
        *,
        page_params: PageParams,
        sort_params: SortParams,
        q: str | None = None,
        status: str | None = None,
        payment_type: str | None = None,
        courier_id: uuid.UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[RTO], int]:
        query = self.rtos.search_query(
            q=q,
            status=status,
            payment_type=payment_type,
            courier_id=courier_id,
            date_from=date_from,
            date_to=date_to,
        )
        items, total = await self.rtos.list(
            page_params=page_params, sort_params=sort_params, query=query
        )
        return list(items), total

    async def get_rto(self, rto_id: uuid.UUID) -> RTO:
        rto = await self.rtos.get_by_id(rto_id)
        if rto is None:
            raise NotFoundError("RTO not found.")
        return rto

    async def update_rto(
        self, rto_id: uuid.UUID, *, actor: User | None, **fields
    ) -> RTO:  # noqa: ANN003
        """Manual staff edit (e.g. correcting a status by hand). Shares
        `_apply_status_change` with `upsert_synced_rto`/`process_courier_
        rto_status` so a courier-driven update and a manual one can never
        diverge on what a status change does -- see that method's
        docstring. `status=RECEIVED` here flips to `AWAITING_VERIFICATION`
        exactly like a courier-driven one; it never restocks directly
        (Module 1 requirement 1 — see `RTOService.verify_warehouse_receipt`).
        """
        rto = await self.get_rto(rto_id)
        clean = {k: v for k, v in fields.items() if v is not None}
        previous_status = rto.status
        if clean:
            await self.rtos.update(rto, **clean)
            if "status" in clean:
                await self._apply_status_change(
                    rto, previous_status=previous_status, actor=actor
                )
        await self.session.commit()
        return rto

    async def _apply_status_change(
        self, rto: RTO, *, previous_status: RTOStatus, actor: User | None
    ) -> None:
        """Shared by a manual `update_rto` edit and `process_courier_rto_
        status` (webhook/scheduled-sync/manual-refresh, via `app.
        integrations.shiprocket.sync`): keeps `Shipment.rto_status` in
        sync, records the audit trail, and -- the one rule that must
        never diverge between a manual and an automatic status change --
        flips `restock_status` to AWAITING_VERIFICATION on `RECEIVED`
        instead of restocking. Idempotent: only flips
        `NOT_APPLICABLE -> AWAITING_VERIFICATION`, so a replayed/duplicate
        RECEIVED status (or a manual re-save) can never regress an
        already-verified or -rejected receipt back to pending.
        """
        shipment = await self.shipments.get_by_id(rto.shipment_id)
        if shipment is not None:
            await self.shipments.update(shipment, rto_status=rto.status)
        await self.audit.record(
            user=actor,
            action="rto.status_changed",
            entity_type="rto",
            entity_id=str(rto.id),
            previous_value={"status": previous_status.value},
            new_value={"status": rto.status.value},
        )
        is_newly_received = (
            rto.status == RTOStatus.RECEIVED
            and rto.restock_status == RTORestockStatus.NOT_APPLICABLE
        )
        if is_newly_received:
            await self.rtos.update(rto, restock_status=RTORestockStatus.AWAITING_VERIFICATION)

    async def verify_warehouse_receipt(
        self,
        rto_id: uuid.UUID,
        *,
        actor: User | None,
        product_variant_id: uuid.UUID | None = None,
        accepted_quantity: int | None = None,
        rejected_quantity: int | None = None,
        notes: str | None = None,
    ) -> RTO:
        """The ONLY path that ever creates an `RTO_RESTOCK` movement
        (Module 1 requirement 1) -- courier status alone never restocks,
        see `_apply_status_change`. Authorized warehouse staff (gated by
        the `rto.update` permission at the route, same as `update_rto`)
        confirm physical receipt here.

        Idempotent: a receipt not currently AWAITING_VERIFICATION (never
        reached that state, or already verified/rejected) raises
        `ConflictError` rather than restocking again or overwriting a
        prior verification's recorded quantities -- a repeated confirm
        from a double-submitted form is always a no-op, never a double
        restock.

        `product_variant_id`/`accepted_quantity`/`rejected_quantity`
        (all three together, or none): omitted, every order line is
        accepted in full -- the common case, correct for the typical
        single-shipment-per-order flow. Provided, this call is scoped to
        that one order line and restocks at most `accepted_quantity`
        boxes (never `rejected_quantity` -- a rejected/damaged quantity
        never increases physical stock); both are validated against what
        is actually still outstanding for that order+variant (dispatched
        so far, minus whatever earlier receipts on this same order have
        already restocked) so a caller can never claim to accept/reject
        more than was really dispatched and not yet accounted for.
        """
        rto = await self.get_rto(rto_id)
        if rto.restock_status != RTORestockStatus.AWAITING_VERIFICATION:
            raise ConflictError(
                "This RTO is not awaiting warehouse verification.",
                details={"error_type": "rto_not_awaiting_verification"},
            )

        inventory = InventoryService(self.session)
        if product_variant_id is None:
            if accepted_quantity is not None or rejected_quantity is not None:
                raise ValidationError(
                    "product_variant_id is required when specifying quantities."
                )
            restocked = await inventory.apply_rto_restock(order_id=rto.order_id, rto_id=rto.id)
            total_accepted, total_rejected = restocked, 0
        else:
            if accepted_quantity is None or rejected_quantity is None:
                raise ValidationError(
                    "accepted_quantity and rejected_quantity are required with product_variant_id."
                )
            if accepted_quantity < 0 or rejected_quantity < 0:
                raise ValidationError("Quantities cannot be negative.")
            dispatch_movement = await inventory.movements.get_for_order(
                order_id=rto.order_id,
                product_variant_id=product_variant_id,
                movement_type=InventoryMovementType.DISPATCH,
            )
            dispatched_boxes = abs(dispatch_movement.quantity_delta) if dispatch_movement else 0
            already_restocked = await inventory.movements.restocked_boxes_for_order_variant(
                order_id=rto.order_id, product_variant_id=product_variant_id
            )
            outstanding = max(dispatched_boxes - already_restocked, 0)
            if accepted_quantity + rejected_quantity > outstanding:
                raise ConflictError(
                    "Accepted + rejected quantity exceeds the outstanding dispatched quantity "
                    "for this order line.",
                    details={
                        "error_type": "rto_quantity_exceeds_dispatched",
                        "outstanding": outstanding,
                    },
                )
            restocked = await inventory.apply_rto_restock(
                order_id=rto.order_id,
                rto_id=rto.id,
                only_variant_id=product_variant_id,
                max_boxes=accepted_quantity,
            )
            total_accepted, total_rejected = restocked, rejected_quantity

        final_restock_status = (
            RTORestockStatus.REJECTED
            if total_accepted == 0 and total_rejected > 0
            else RTORestockStatus.VERIFIED
        )
        await self.rtos.update(
            rto,
            restock_status=final_restock_status,
            verified_at=datetime.now(UTC),
            verified_by_user_id=actor.id if actor else None,
            accepted_quantity=total_accepted,
            rejected_quantity=total_rejected,
            notes=notes if notes is not None else rto.notes,
        )
        await self.audit.record(
            user=actor,
            action="rto.warehouse_receipt_verified",
            entity_type="rto",
            entity_id=str(rto.id),
            new_value={"accepted_quantity": total_accepted, "rejected_quantity": total_rejected},
        )
        await self.session.commit()
        return rto

    async def upsert_synced_rto(self, **data) -> tuple[RTO, bool]:  # noqa: ANN003
        """Idempotent create-or-update. Unlike NDR (a genuine Shiprocket
        list endpoint), no dedicated RTO listing endpoint could be
        confirmed without a live account (see docs/integrations/shiprocket.md)
        — RTO records are instead derived as a side effect of tracking
        refresh when a tracking event's mapped status is
        `RTO_INITIATED`/`RTO_DELIVERED` (`app.integrations.shiprocket.sync`),
        which already resolves `shipment_id`/`order_id`/`courier_id`.
        """
        source_system = data.pop("source_system")
        external_id = data.pop("external_id")
        shipment_id = data.pop("shipment_id")
        order_id = data.pop("order_id")
        courier_id = data.pop("courier_id", None)

        clean = {k: v for k, v in data.items() if v is not None}
        previous = await self.rtos.get_by_source_external_id(
            source_system=source_system, external_id=external_id
        )
        previous_status = previous.status if previous is not None else None
        rto, created = await self.rtos.upsert_by_external_id(
            source_system=source_system,
            external_id=external_id,
            shipment_id=shipment_id,
            order_id=order_id,
            courier_id=courier_id,
            **clean,
        )
        if "status" in clean and clean["status"] != previous_status:
            await self._apply_status_change(
                rto, previous_status=previous_status or rto.status, actor=None
            )
        else:
            shipment = await self.shipments.get_by_id(shipment_id)
            if shipment is not None:
                await self.shipments.update(shipment, rto_status=rto.status)
        await self.session.commit()
        return rto, created

    async def process_courier_rto_status(
        self,
        *,
        source_system: str,
        external_id: str,
        shipment_id: uuid.UUID,
        order_id: uuid.UUID,
        courier_id: uuid.UUID | None,
        status: RTOStatus,
        external_reason: str | None,
    ) -> RTO:
        """The ONE place `app.integrations.shiprocket.sync.
        apply_tracking_event` (webhook + scheduled pull-sync) and
        `app.services.shiprocket_service.ShiprocketService.
        refresh_tracking_for_shipment` (manual refresh) derive RTO state
        -- Module 1 requirement 2: all three ingestion paths call this
        exact method so they can never diverge on what a courier RTO
        status means. Thin wrapper over `upsert_synced_rto` (which
        already shares `_apply_status_change`'s AWAITING_VERIFICATION
        gate with a manual `update_rto` edit); exists as its own method
        so a tracking-ingestion call site never has to assemble
        `upsert_synced_rto`'s raw kwargs dict by hand.
        """
        rto, _ = await self.upsert_synced_rto(
            source_system=source_system,
            external_id=external_id,
            shipment_id=shipment_id,
            order_id=order_id,
            courier_id=courier_id,
            status=status,
            external_reason=external_reason,
        )
        return rto
