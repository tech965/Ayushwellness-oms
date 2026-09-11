"""Order lifecycle: atomic creation, controlled status transitions, and
the append-only order timeline.

`ORDER_STATUS_TRANSITIONS` is the only place order status transition
rules live — routes and repositories never decide whether a transition
is valid. Every transition writes an `OrderEvent`; none ever mutates or
deletes a prior event.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, IntegrationError, NotFoundError
from app.models.auth import User
from app.models.enums import (
    FulfillmentStatus,
    OrderStatus,
    PaymentStatus,
    ShipmentStatus,
    ShopifySyncStatus,
)
from app.models.order import Order, OrderEvent
from app.repositories.customer import CustomerRepository
from app.repositories.order import OrderEventRepository, OrderItemRepository, OrderRepository
from app.repositories.payment import PaymentRepository
from app.repositories.product import ProductVariantRepository
from app.repositories.shipment import ShipmentRepository
from app.schemas.common import PageParams, SortParams
from app.schemas.order import OrderItemCreateRequest
from app.services.address_validation_service import AddressValidationService
from app.services.audit_service import AuditService
from app.services.export_service import ExportService

ORDER_STATUS_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.CONFIRMED, OrderStatus.CANCELLED},
    # CONFIRMED -> PENDING is the telecaller "undo a mistaken confirmation"
    # path (`unconfirm_order` below) — the only reverse transition in this
    # table. `unconfirm_order` adds its own business-rule guard on top
    # (blocked once shipping has actually progressed — see
    # `_SHIPMENT_PROGRESS_BLOCK_MESSAGES` — never merely "a Shipment row
    # exists"); this table only says the transition is structurally
    # possible.
    OrderStatus.CONFIRMED: {OrderStatus.PROCESSING, OrderStatus.CANCELLED, OrderStatus.PENDING},
    OrderStatus.PROCESSING: {OrderStatus.PACKED, OrderStatus.CANCELLED},
    OrderStatus.PACKED: {OrderStatus.SHIPPED, OrderStatus.CANCELLED},
    OrderStatus.SHIPPED: {OrderStatus.DELIVERED},
    OrderStatus.DELIVERED: set(),
    OrderStatus.CANCELLED: set(),
}

# `unconfirm_order`'s per-status reasons once a shipment has progressed
# past PENDING -- deliberately specific (never the old generic "a shipment
# already exists for this order", which was true for every CONFIRMED order
# that had ever even started shipping and gave the caller no way to tell a
# genuinely-blocked revert from a bug). PENDING and CANCELLED are absent on
# purpose: `unconfirm_order` never blocks on either of those.
_SHIPMENT_PROGRESS_BLOCK_MESSAGES: dict[ShipmentStatus, str] = {
    ShipmentStatus.PICKED_UP: (
        "Cannot revert this order because the shipment has already been picked up."
    ),
    ShipmentStatus.IN_TRANSIT: (
        "Cannot revert this order because the shipment is already in transit."
    ),
    ShipmentStatus.OUT_FOR_DELIVERY: (
        "Cannot revert this order because the shipment is already out for delivery."
    ),
    ShipmentStatus.DELIVERED: (
        "Cannot revert this order because the shipment has already been delivered."
    ),
    ShipmentStatus.NDR: (
        "Cannot revert this order because the shipment has an active delivery "
        "exception (NDR)."
    ),
    ShipmentStatus.RTO_INITIATED: (
        "Cannot revert this order because the shipment is already in an RTO (return) flow."
    ),
    ShipmentStatus.RTO_DELIVERED: (
        "Cannot revert this order because the shipment is already in an RTO (return) flow."
    ),
}


class OrderService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.orders = OrderRepository(session)
        self.order_items = OrderItemRepository(session)
        self.order_events = OrderEventRepository(session)
        self.payments = PaymentRepository(session)
        self.customers = CustomerRepository(session)
        self.variants = ProductVariantRepository(session)
        self.shipments = ShipmentRepository(session)
        self.audit = AuditService(session)

    async def list_orders(
        self,
        *,
        page_params: PageParams,
        sort_params: SortParams,
        q: str | None = None,
        status: str | None = None,
        payment_status: str | None = None,
        payment_type: str | None = None,
        fulfillment_status: str | None = None,
        shipment_status: str | None = None,
        courier_id: uuid.UUID | None = None,
        sku: str | None = None,
        tag: str | None = None,
        amount_min: Decimal | None = None,
        amount_max: Decimal | None = None,
        customer_id: uuid.UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        confirmed_only: bool = False,
        telecaller_id: uuid.UUID | None = None,
        confirmed_date_from: datetime | None = None,
        confirmed_date_to: datetime | None = None,
    ) -> tuple[list[Order], int]:
        query = self.orders.search_query(
            q=q,
            status=status,
            payment_status=payment_status,
            payment_type=payment_type,
            fulfillment_status=fulfillment_status,
            shipment_status=shipment_status,
            courier_id=courier_id,
            sku=sku,
            tag=tag,
            amount_min=amount_min,
            amount_max=amount_max,
            customer_id=customer_id,
            date_from=date_from,
            date_to=date_to,
            confirmed_only=confirmed_only,
            telecaller_id=telecaller_id,
            confirmed_date_from=confirmed_date_from,
            confirmed_date_to=confirmed_date_to,
        )
        items, total = await self.orders.list(
            page_params=page_params, sort_params=sort_params, query=query
        )
        return list(items), total

    async def export_orders(self, filters: dict) -> bytes:
        query = self.orders.search_query(**filters)
        orders = await self.orders.list_for_export(query, limit=ExportService.MAX_ROWS)
        return ExportService().orders_to_xlsx(orders)

    async def get_order(self, order_id: uuid.UUID) -> Order:
        # Eager-loads `customer` too (not just `items`) so
        # `OrderDetailResponse.customer` can always be populated without a
        # second round trip — every caller of `get_order` eventually
        # serializes through `OrderDetailResponse`.
        order = await self.orders.get_by_id_with_items_and_customer(order_id)
        if order is None:
            raise NotFoundError("Order not found.")
        return order

    async def get_timeline(self, order_id: uuid.UUID) -> list[OrderEvent]:
        await self.get_order(order_id)
        return await self.order_events.list_for_order(order_id)

    async def create_order(
        self,
        *,
        actor: User | None,
        order_number: str,
        customer_id: uuid.UUID | None,
        order_datetime: datetime | None,
        currency: str,
        payment_type,  # noqa: ANN001
        shipping_charge: Decimal,
        notes: str | None,
        items: list[OrderItemCreateRequest],
    ) -> Order:
        if await self.orders.get_by_order_number(order_number) is not None:
            raise ConflictError(f"Order number '{order_number}' already exists.")

        subtotal = sum((item.unit_price * item.quantity for item in items), Decimal("0"))
        discount_amount = sum((item.discount_amount for item in items), Decimal("0"))
        tax_amount = sum((item.tax_amount for item in items), Decimal("0"))
        total_amount = subtotal - discount_amount + tax_amount + shipping_charge

        order = await self.orders.create(
            order_number=order_number,
            customer_id=customer_id,
            order_datetime=order_datetime or datetime.now(UTC),
            currency=currency,
            subtotal=subtotal,
            discount_amount=discount_amount,
            tax_amount=tax_amount,
            shipping_charge=shipping_charge,
            total_amount=total_amount,
            payment_type=payment_type,
            payment_status=PaymentStatus.PENDING,
            status=OrderStatus.PENDING,
            notes=notes,
            source_system="manual",
        )

        for item in items:
            item_total = item.unit_price * item.quantity - item.discount_amount + item.tax_amount
            await self.order_items.create(
                order_id=order.id,
                product_variant_id=item.product_variant_id,
                sku=item.sku,
                product_name=item.product_name,
                quantity=item.quantity,
                unit_price=item.unit_price,
                discount_amount=item.discount_amount,
                tax_amount=item.tax_amount,
                total_amount=item_total,
            )

        await self.payments.create(
            order_id=order.id,
            payment_type=payment_type,
            status=PaymentStatus.PENDING,
            amount=total_amount,
            currency=currency,
            source_system="manual",
        )

        await self.order_events.create(
            order_id=order.id,
            event_type="order_created",
            status=OrderStatus.PENDING.value,
            description="Order created.",
            source="system",
            actor_user_id=actor.id if actor else None,
        )

        await self.audit.record(
            user=actor,
            action="order.created",
            entity_type="order",
            entity_id=str(order.id),
            new_value={"order_number": order_number, "total_amount": str(total_amount)},
        )

        await self.session.commit()
        return await self.get_order(order.id)

    async def transition_status(
        self,
        order_id: uuid.UUID,
        *,
        new_status: OrderStatus,
        actor: User | None,
        description: str | None,
    ) -> Order:
        order = await self.get_order(order_id)
        allowed = ORDER_STATUS_TRANSITIONS.get(order.status, set())
        if new_status not in allowed:
            raise ConflictError(
                f"Cannot transition order from '{order.status.value}' to '{new_status.value}'."
            )

        previous_status = order.status
        await self.orders.update(order, status=new_status)

        await self.order_events.create(
            order_id=order.id,
            event_type="status_changed",
            status=new_status.value,
            description=description or f"Status changed to {new_status.value}.",
            source="system",
            actor_user_id=actor.id if actor else None,
        )

        await self.audit.record(
            user=actor,
            action="order.status_changed",
            entity_type="order",
            entity_id=str(order.id),
            previous_value={"status": previous_status.value},
            new_value={"status": new_status.value},
        )

        await self.session.commit()
        return await self.get_order(order_id)

    async def confirm_order(self, order_id: uuid.UUID, *, actor: User) -> Order:
        """PENDING -> CONFIRMED specifically, stamping telecaller
        attribution in the same transaction as the status change — the
        only caller of this is the telecaller confirm/bulk-confirm flow
        (`TelecallingService.confirm_assigned_order`), which does its own
        ownership check *before* calling this; this method itself has no
        opinion on who's allowed to confirm, same separation
        `transition_status` already has from its caller's permission
        check. Reuses `transition_status` for the actual state-machine
        validation/event/audit — never a second transition-rules table.
        `confirmed_by_telecaller_id`/`confirmed_at` are set once and
        `transition_status` never touches them again on any later
        transition (PROCESSING/PACKED/...), so they survive the rest of
        the order's lifecycle untouched.

        Also adds the `CONFIRMATION_TAG` marker to the Shopify order
        (`ShopifyFulfillmentService.sync_confirmation_tag`) once the OMS
        side above has fully committed -- proof the OMS is driving
        confirmation. That tag is the ONLY Shopify-side effect: no
        `fulfillmentCreate`, no fulfillment-status change (Shopify stays
        Unfulfilled until real shipping happens on AWB assignment), and
        no Shiprocket call (a shipment is only ever created by an
        explicit Ship Order/Bulk Ship action). Best effort: a tag-push
        failure is logged and never undoes or fails the OMS confirmation
        that already succeeded (`POST /orders/{id}/shopify/
        retry-confirmation-sync` re-pushes it).
        """
        order = await self.transition_status(
            order_id,
            new_status=OrderStatus.CONFIRMED,
            actor=actor,
            description="Confirmed by telecaller.",
        )
        await self.orders.update(
            order, confirmed_by_telecaller_id=actor.id, confirmed_at=datetime.now(UTC)
        )
        await self.session.commit()

        # Local import: avoids a module-load-order dependency between
        # `order_service` and `shopify_fulfillment_service` for the one
        # code path that needs it, matching this codebase's existing
        # convention for occasional cross-service calls (see the same
        # pattern in `unconfirm_order` for `shiprocket_service`).
        from app.services.shopify_fulfillment_service import ShopifyFulfillmentService

        await ShopifyFulfillmentService(self.session).sync_confirmation_tag(order_id, actor=actor)
        return await self.get_order(order_id)

    async def unconfirm_order(self, order_id: uuid.UUID, *, actor: User) -> Order:
        """Reverts a mistaken telecaller confirmation — CONFIRMED ->
        PENDING only, the exact inverse of `confirm_order`. The only
        caller is `TelecallingService.unconfirm_assigned_order`, which
        does its own ownership check first, same separation `confirm_order`
        already has from its caller.

        The gate is on shipment PROGRESS, never on mere row existence:

          - No `Shipment` row at all -- including one that never got
            created because Shiprocket rejected the attempt (see
            `ShiprocketOperationsService.create_shipment_for_order`,
            which never persists a row on failure) -- reverts freely.
          - A row still `PENDING` (created, but Shiprocket hasn't picked
            it up) or already `CANCELLED` -- also reverts freely, but a
            `PENDING` one is cancelled first (reusing
            `ShiprocketOperationsService.cancel_shipment`, the same
            operation the shipment detail page's own "Cancel Shipment"
            button calls -- never a second, local-only copy of that
            Shiprocket call) so Shiprocket is never left holding a live
            shipment for an order the OMS now calls unconfirmed/PENDING.
          - Anything that has actually progressed (`PICKED_UP` or later
            -- in transit, out for delivery, delivered, NDR, RTO) blocks
            the revert outright (`ConflictError`, 409, with the specific
            `_SHIPMENT_PROGRESS_BLOCK_MESSAGES` reason): fulfillment may
            already be physically handling the order, and Shiprocket
            itself will not accept a cancellation at that point either.
          - A `shopify_sync_status` of `SYNCED` on any shipment -- a real
            shipping-time Shopify `Fulfillment` exists for this order
            (pushed on AWB assignment, see `sync_fulfillment_for_
            shipment`) -- also blocks outright, regardless of that
            shipment's Shiprocket state: that Fulfillment carries real
            tracking info a customer may already be watching, and is
            never something this method reverses.
          - `Order.fulfillment_status == FULFILLED` blocks UNLESS
            `Order.shopify_confirmation_fulfillment_id` is set. That
            column is only ever set for an order fulfilled under the
            previous confirmation behaviour (confirmation no longer
            creates a Fulfillment -- it only tags), so in practice this
            now reduces to "any FULFILLED order blocks": a real shipping
            Fulfillment, or one made directly through Shopify, is never
            something this revert undoes. `fulfillment_status` alone is
            Shopify's coarse inbound summary, never trusted here as proof
            of what's safe to undo.

        Both blocks above are checked for every shipment BEFORE any
        Shiprocket cancellation is attempted for any of them, so an order
        with more than one `Shipment` row never ends up with one
        successfully cancelled while a sibling then blocks the actual
        revert -- either the whole thing proceeds, or nothing does.

        The `CONFIRMATION_TAG` is removed from the Shopify order next,
        via `ShopifyFulfillmentService.reverse_confirmation_tag` -- the
        ONLY Shopify-side effect of an unconfirm. Best-effort (a failure
        is logged, never blocks the revert) and never calls
        `fulfillmentCancel` or changes the order's fulfillment status.

        `transition_status` still independently blocks reverting anything
        that isn't currently CONFIRMED (not in
        `ORDER_STATUS_TRANSITIONS[order.status]`) -- calling this twice in
        a row fails cleanly on the second call for that reason alone
        (the Shopify/Shiprocket reversal steps above are themselves
        idempotent no-ops by then, so nothing duplicates or corrupts
        either).

        Clears `confirmed_by_telecaller_id`/`confirmed_at` back to `None`
        so the order stops showing as telecaller-confirmed anywhere (the
        Fulfillment Queue, the confirmation analytics) — the permanent
        record of "confirmed, then reverted" (and, when applicable, the
        Shopify/Shiprocket cancellations just above) still lives in the
        append-only `OrderEvent` timeline / audit log; nothing is deleted.
        """
        order = await self.get_order(order_id)
        if (
            order.fulfillment_status == FulfillmentStatus.FULFILLED
            and not order.shopify_confirmation_fulfillment_id
        ):
            raise ConflictError(
                "Cannot revert this order because Shopify already shows it as fulfilled.",
                details={"error_type": "shopify_already_fulfilled"},
            )

        shipments = await self.shipments.list_for_order(order_id)

        for shipment in shipments:
            block_message = _SHIPMENT_PROGRESS_BLOCK_MESSAGES.get(shipment.current_status)
            if block_message:
                raise ConflictError(block_message, details={"error_type": "shipment_progressed"})
            if shipment.shopify_sync_status == ShopifySyncStatus.SYNCED:
                raise ConflictError(
                    "Cannot revert this order because a Shopify fulfillment has already "
                    "been created for its shipment and cannot be safely cancelled.",
                    details={"error_type": "shopify_fulfillment_synced"},
                )

        # Local imports: avoids a module-load-order dependency between
        # `order_service` and its peer services for the one code path
        # that needs each, matching this codebase's existing convention
        # for occasional cross-service calls.
        from app.services.shiprocket_service import ShiprocketOperationsService
        from app.services.shopify_fulfillment_service import ShopifyFulfillmentService

        await ShopifyFulfillmentService(self.session).reverse_confirmation_tag(
            order_id, actor=actor
        )

        shiprocket_ops = ShiprocketOperationsService(self.session)
        for shipment in shipments:
            if shipment.current_status != ShipmentStatus.PENDING:
                continue  # already CANCELLED -- nothing to do
            if shipment.shiprocket_shipment_id:
                # Real external cancellation first; only ever proceeds to
                # the PENDING transition below once this has actually
                # succeeded (a failure here is re-raised below as a plain
                # `ConflictError` and the order stays CONFIRMED, exactly
                # as if this method were never called -- never leaves
                # Shiprocket with a live shipment while OMS silently
                # reverts underneath it).
                try:
                    await shiprocket_ops.cancel_shipment(shipment.id, actor=actor)
                except IntegrationError as exc:
                    raise ConflictError(
                        "Shiprocket cancellation failed. The order was not reverted.",
                        details={"error_type": "shiprocket_cancellation_failed"},
                    ) from exc
            else:
                # A shipment row that was never actually registered with
                # Shiprocket (e.g. created manually, `POST /shipments`) --
                # nothing external to cancel; mark it CANCELLED directly
                # so it never counts as "active" again, and keep the
                # ledger record rather than deleting the row.
                previous_status = shipment.current_status
                await self.shipments.update(shipment, current_status=ShipmentStatus.CANCELLED)
                await self.audit.record(
                    user=actor,
                    action="shipment.cancelled_for_unconfirm",
                    entity_type="shipment",
                    entity_id=str(shipment.id),
                    previous_value={"status": previous_status.value},
                    new_value={"status": ShipmentStatus.CANCELLED.value},
                )
                await self.session.commit()

        order = await self.transition_status(
            order_id,
            new_status=OrderStatus.PENDING,
            actor=actor,
            description="Reverted to pending (unconfirmed).",
        )
        await self.orders.update(order, confirmed_by_telecaller_id=None, confirmed_at=None)
        await self.session.commit()
        return await self.get_order(order_id)

    async def update_shipping_address(
        self, order_id: uuid.UUID, *, actor: User, address: dict
    ) -> Order:
        """Edits `Order.shipping_address` only -- the one place this OMS
        stores an order's shipping address (a point-in-time snapshot, not
        a `CustomerAddress` FK; see that column's docstring). Only
        caller is `TelecallingService.update_assigned_order_address`,
        which does its own ownership check first, same separation
        `confirm_order`/`unconfirm_order` already have from their caller.

        Deliberately narrow: never touches `Order.status` (no
        `transition_status` call), `confirmed_by_telecaller_id`/
        `confirmed_at`, `fulfillment_status`, inventory, or Shiprocket --
        an address correction is not a confirmation/shipping event, and
        must never be mistaken for one by any of those systems. Call
        attempts/history are untouched for the same reason.

        The OMS write commits first and unconditionally; the Shopify push
        (`ShopifyFulfillmentService.sync_shipping_address`) only ever
        runs after that, and its own failure-isolation means it can never
        undo or roll back this commit -- see that method's docstring for
        why a plain re-save is already this operation's retry mechanism,
        with no separate retry endpoint needed.

        Audited (`AuditService.record`, previous/new address) and written
        to the append-only `OrderEvent` timeline (`event_type=
        "address_updated"`) -- the existing pattern for "what changed and
        who changed it," reused unchanged rather than inventing a second
        mechanism.
        """
        order = await self.get_order(order_id)
        previous_address = order.shipping_address
        await self.orders.update(order, shipping_address=address)
        await self.order_events.create(
            order_id=order_id,
            event_type="address_updated",
            status=None,
            description="Shipping address updated.",
            source="user",
            actor_user_id=actor.id,
        )
        await self.audit.record(
            user=actor,
            action="order.shipping_address_updated",
            entity_type="order",
            entity_id=str(order_id),
            previous_value={"shipping_address": previous_address},
            new_value={"shipping_address": address},
        )
        await self.session.commit()

        # Address validation -- the edited address always differs from
        # whatever was last validated (a hash mismatch, per
        # `AddressValidationService.needs_validation`), so this always
        # actually re-validates here, never a no-op. Runs before the
        # Shopify push below so `AuditLog`/`OrderEvent` order matches the
        # order these actually happen in.
        await AddressValidationService(self.session).validate_order(order)

        # Local import: avoids a module-load-order dependency between
        # `order_service` and `shopify_fulfillment_service` for the one
        # code path that needs it, matching this codebase's existing
        # convention for occasional cross-service calls.
        from app.services.shopify_fulfillment_service import ShopifyFulfillmentService

        await ShopifyFulfillmentService(self.session).sync_shipping_address(order_id, actor=actor)
        return await self.get_order(order_id)

    async def add_event(
        self,
        order_id: uuid.UUID,
        *,
        actor: User | None,
        event_type: str,
        status: str | None,
        description: str | None,
        event_metadata: dict | None,
    ) -> OrderEvent:
        await self.get_order(order_id)
        event = await self.order_events.create(
            order_id=order_id,
            event_type=event_type,
            status=status,
            description=description,
            source="user" if actor else "system",
            actor_user_id=actor.id if actor else None,
            event_metadata=event_metadata,
        )
        await self.session.commit()
        return event

    async def upsert_synced_order(self, **data) -> tuple[Order, bool]:  # noqa: ANN003
        """Idempotent create-or-update from a sync adapter's normalized
        order dict. Field ownership (spec §27): Shopify owns every
        financial/status field passed in here and they're always
        overwritten on update; `Order.status` (the OMS-internal
        pack/ship *operational* workflow — see `ORDER_STATUS_TRANSITIONS`)
        is OMS-owned and is only set once, on creation — a resync never
        rewinds or fast-forwards it, except that an order Shopify reports
        as cancelled is transitioned to CANCELLED if that transition is
        currently valid (Shopify is authoritative for cancellation).

        Known limitation: a line item removed from the order in Shopify
        between two syncs is not deleted here — only present items are
        upserted. Full reconciliation (diff + delete stale items) is
        deferred; see docs/architecture/integrations.md.
        """
        source_system = data.pop("source_system")
        external_id = data.pop("external_id")
        items_data = data.pop("items", [])
        customer_external_id = data.pop("customer_external_id", None)
        is_cancelled = data.pop("is_cancelled", False)
        shipping_address = data.pop("shipping_address", None)
        billing_address = data.pop("billing_address", None)

        customer_id = None
        if customer_external_id:
            customer = await self.customers.get_by_source_external_id(
                source_system=source_system, external_id=customer_external_id
            )
            customer_id = customer.id if customer else None

        existing = await self.orders.get_by_source_external_id(
            source_system=source_system, external_id=external_id
        )

        if existing is not None:
            incoming_updated_at = data.get("external_updated_at")
            # Webhooks are not guaranteed to arrive in the order Shopify
            # generated them (e.g. a delayed retry of an older
            # orders/updated landing after a newer delivery already
            # applied). The provider's own `updated_at` is the one signal
            # that's monotonic per order regardless of delivery order, so a
            # delivery strictly older than what's already stored is
            # dropped as stale instead of overwriting newer data with
            # older data. `None` on either side (a payload with no
            # timestamp, or a row synced before this field existed) always
            # means "apply it" — this can only ever skip a delivery proven
            # older, never skip one that might actually be newer.
            if (
                incoming_updated_at is not None
                and existing.external_updated_at is not None
                and incoming_updated_at < existing.external_updated_at
            ):
                return existing, False

        if existing is None:
            initial_status = (
                OrderStatus.CANCELLED
                if is_cancelled
                else (
                    OrderStatus.CONFIRMED
                    if data.get("payment_status") == PaymentStatus.PAID
                    else OrderStatus.PENDING
                )
            )
            order = await self.orders.create(
                source_system=source_system,
                external_id=external_id,
                customer_id=customer_id,
                status=initial_status,
                shipping_address=shipping_address,
                billing_address=billing_address,
                **data,
            )
            created = True
        else:
            order = existing
            await self.orders.update(
                order,
                customer_id=customer_id or order.customer_id,
                shipping_address=shipping_address,
                billing_address=billing_address,
                **data,
            )
            if is_cancelled and order.status != OrderStatus.CANCELLED:
                allowed = ORDER_STATUS_TRANSITIONS.get(order.status, set())
                if OrderStatus.CANCELLED in allowed:
                    await self.orders.update(order, status=OrderStatus.CANCELLED)
            created = False

        for item in items_data:
            item = dict(item)
            item_external_id = item.pop("external_id")
            variant_external_id = item.pop("shopify_variant_id", None)
            product_variant_id = None
            if variant_external_id:
                variant = await self.variants.get_by_source_external_id(
                    source_system=source_system, external_id=variant_external_id
                )
                product_variant_id = variant.id if variant else None
            await self.order_items.upsert_by_external_id(
                source_system=source_system,
                external_id=item_external_id,
                order_id=order.id,
                product_variant_id=product_variant_id,
                **item,
            )

        await self.payments.upsert_by_external_id(
            source_system=source_system,
            external_id=external_id,
            order_id=order.id,
            payment_type=data["payment_type"],
            status=data["payment_status"],
            amount=data["total_amount"],
            currency=data["currency"],
            provider="shopify",
            external_transaction_id=external_id,
        )

        await self.order_events.create(
            order_id=order.id,
            event_type="order_created" if created else "order_synced",
            status=order.status.value,
            description=(
                "Order created from Shopify sync." if created else "Order re-synced from Shopify."
            ),
            source="shopify",
        )
        await self.audit.record(
            user=None,
            action="order.synced" if not created else "order.created",
            entity_type="order",
            entity_id=str(order.id),
            new_value={
                "order_number": data.get("order_number"),
                "total_amount": str(data.get("total_amount")),
            },
            metadata={"source_system": source_system, "external_id": external_id},
        )

        await self.session.commit()

        # Address validation (spec: Shiprocket-style confidence check) --
        # a safe no-op whenever this sync didn't actually change
        # `shipping_address` from what was last validated (see
        # `AddressValidationService.needs_validation`), so a routine
        # resync of an unchanged address never re-calls the provider.
        # Never raises -- a provider failure is recorded as `UNKNOWN`,
        # never allowed to fail this already-committed sync.
        await AddressValidationService(self.session).validate_order(order)

        return await self.get_order(order.id), created
