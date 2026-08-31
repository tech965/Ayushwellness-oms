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

from app.core.exceptions import ConflictError, NotFoundError
from app.models.auth import User
from app.models.enums import OrderStatus, PaymentStatus
from app.models.order import Order, OrderEvent
from app.repositories.customer import CustomerRepository
from app.repositories.order import OrderEventRepository, OrderItemRepository, OrderRepository
from app.repositories.payment import PaymentRepository
from app.repositories.product import ProductVariantRepository
from app.schemas.common import PageParams, SortParams
from app.schemas.order import OrderItemCreateRequest
from app.services.audit_service import AuditService
from app.services.export_service import ExportService

ORDER_STATUS_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.CONFIRMED, OrderStatus.CANCELLED},
    OrderStatus.CONFIRMED: {OrderStatus.PROCESSING, OrderStatus.CANCELLED},
    OrderStatus.PROCESSING: {OrderStatus.PACKED, OrderStatus.CANCELLED},
    OrderStatus.PACKED: {OrderStatus.SHIPPED, OrderStatus.CANCELLED},
    OrderStatus.SHIPPED: {OrderStatus.DELIVERED},
    OrderStatus.DELIVERED: set(),
    OrderStatus.CANCELLED: set(),
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
        amount_min: Decimal | None = None,
        amount_max: Decimal | None = None,
        customer_id: uuid.UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
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
            amount_min=amount_min,
            amount_max=amount_max,
            customer_id=customer_id,
            date_from=date_from,
            date_to=date_to,
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
        return await self.get_order(order.id), created
