from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import String, and_, case, exists, func, or_, select
from sqlalchemy.orm import aliased, selectinload

from app.models.customer import Customer
from app.models.enums import FulfillmentStatus, OrderStatus, ShipmentStatus
from app.models.order import Order, OrderEvent, OrderItem
from app.models.shipment import Shipment
from app.repositories.base import AppendOnlyRepository, BaseRepository
from app.schemas.common import PageParams


class OrderRepository(BaseRepository[Order]):
    model = Order

    async def get_by_order_number(self, order_number: str) -> Order | None:
        stmt = select(Order).where(Order.order_number == order_number)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_earliest_order_datetime(self) -> datetime | None:
        """The oldest `order_datetime` this OMS has ever synced — used by
        `entity_sync._upsert_shipment` to skip an expensive live
        Shiprocket order-detail lookup for a shipment that predates the
        OMS's own order-sync coverage entirely (confirmed live this
        engagement: such a shipment can never resolve to a real OMS
        `Order`, no matter what Shiprocket returns). Purely a performance
        boundary, never a matching decision — `None` (no orders synced
        yet) means "don't skip anything."
        """
        result = await self.session.execute(select(func.min(Order.order_datetime)))
        return result.scalar_one_or_none()

    async def get_by_id_with_items(self, id_: uuid.UUID) -> Order | None:
        stmt = select(Order).where(Order.id == id_).options(selectinload(Order.items))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id_with_items_and_customer(self, id_: uuid.UUID) -> Order | None:
        """Eager-loads `items` (with each item's `product_variant`, for
        the order detail page's per-line "available stock" column),
        `customer`, and `confirmed_by_telecaller` — needed by any caller
        that touches these relationships (e.g.
        `ShiprocketOperationsService.create_shipment_for_order`, the
        order detail endpoints below), since a lazy load on an un-loaded
        relationship raises `MissingGreenlet` under `AsyncSession`.
        """
        stmt = (
            select(Order)
            .where(Order.id == id_)
            .options(
                selectinload(Order.items).selectinload(OrderItem.product_variant),
                selectinload(Order.customer),
                selectinload(Order.confirmed_by_telecaller),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    def search_query(
        self,
        *,
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
    ):
        stmt = self._base_query()
        if q:
            like = f"%{q}%"
            # `q` matches order number/Shopify id directly, or (via EXISTS,
            # so an order with multiple shipments never produces duplicate
            # rows) its linked customer's name/phone/email or any of its
            # shipments' AWB — matches spec's "search by order number,
            # customer name, phone, email, tracking number".
            customer_match = exists(
                select(1).where(
                    and_(
                        Customer.id == Order.customer_id,
                        or_(
                            Customer.full_name.ilike(like),
                            Customer.phone.ilike(like),
                            Customer.email.ilike(like),
                        ),
                    )
                )
            )
            shipment_awb_match = exists(
                select(1).where(and_(Shipment.order_id == Order.id, Shipment.awb.ilike(like)))
            )
            stmt = stmt.where(
                or_(
                    Order.order_number.ilike(like),
                    Order.shopify_order_id.ilike(like),
                    customer_match,
                    shipment_awb_match,
                )
            )
        if status:
            stmt = stmt.where(Order.status == status)
        if payment_status:
            stmt = stmt.where(Order.payment_status == payment_status)
        if payment_type:
            stmt = stmt.where(Order.payment_type == payment_type)
        if fulfillment_status:
            stmt = stmt.where(Order.fulfillment_status == fulfillment_status)
        if customer_id:
            stmt = stmt.where(Order.customer_id == customer_id)
        if date_from:
            stmt = stmt.where(Order.order_datetime >= date_from)
        if date_to:
            stmt = stmt.where(Order.order_datetime <= date_to)
        if amount_min is not None:
            stmt = stmt.where(Order.total_amount >= amount_min)
        if amount_max is not None:
            stmt = stmt.where(Order.total_amount <= amount_max)
        if shipment_status or courier_id:
            conditions = [Shipment.order_id == Order.id]
            if shipment_status:
                conditions.append(Shipment.current_status == shipment_status)
            if courier_id:
                conditions.append(Shipment.courier_id == courier_id)
            stmt = stmt.where(exists(select(1).where(and_(*conditions))))
        if sku:
            stmt = stmt.where(
                exists(
                    select(1).where(
                        and_(OrderItem.order_id == Order.id, OrderItem.sku.ilike(f"%{sku}%"))
                    )
                )
            )
        if tag:
            # `shopify_tags` is a JSON array (`app/models/order.py`); no
            # dialect-specific containment operator (Postgres `@>`, etc.)
            # is used so this stays portable to SQLite (the test suite's
            # dialect) — casting to text and matching the quoted element
            # against a JSON-serialized array (`["VIP", "COD"]`) is a
            # substring match, same unindexed-ILIKE performance profile
            # as the `sku` filter just above, deliberately not a new
            # indexed/expensive search path (small per-order tag lists).
            stmt = stmt.where(
                func.cast(Order.shopify_tags, String).ilike(f'%"{tag}"%')
            )
        # Every caller of `search_query` eventually serializes through a
        # response that touches `customer`/`items`/`shipments` (list rows
        # need customer/product/shipment columns; export needs all three;
        # even a plain `OrderResponse` caller just harmlessly loads and
        # discards them) — eager-loading once here means no caller has to
        # remember to, and avoids a `MissingGreenlet` from an un-loaded
        # relationship being touched later under `AsyncSession`.
        stmt = stmt.options(
            selectinload(Order.customer),
            selectinload(Order.items),
            selectinload(Order.shipments).selectinload(Shipment.courier),
        )
        return stmt

    def for_customer_query(self, customer_id: uuid.UUID):
        return self._base_query().where(Order.customer_id == customer_id)

    def shipment_queue_query(
        self,
        *,
        q: str | None = None,
        payment_type: str | None = None,
        telecaller_id: uuid.UUID | None = None,
        courier_id: uuid.UUID | None = None,
        sku: str | None = None,
        shipment_status: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        telecaller_ids: list[uuid.UUID] | None = None,
    ):
        """Confirmed orders "awaiting shipment processing" — `Order.status
        == CONFIRMED`, `Order.fulfillment_status != FULFILLED` (Shopify's
        own record of whether this order was actually shipped — many
        historical orders were fulfilled directly through Shopify and
        never got a `Shipment` row created in this OMS at all, so
        checking only for "no advanced Shipment row" hugely over-counted;
        confirmed live against production, where it inflated this count
        into the tens of thousands. Same signal
        `OrderAssignmentRepository.list_unfulfilled_pool` already uses for
        the identical reason), and every `Shipment` row it has (if any) is
        still CANCELLED or PENDING; the moment any shipment reaches
        PICKED_UP or later it's genuinely shipping and drops out of the
        queue. Mirrors `list_unfulfilled_pool`'s LEFT JOIN shape — a NULL
        shipment side just means "not created yet", never "dropped".
        Does not touch/duplicate `search_query` (the general Orders list)
        or `ShipmentRepository.search_query` (the general Shipments
        list) — this is a third, narrower view.

        `telecaller_ids`, unlike `telecaller_id`, is a mandatory SECURITY
        SCOPE, not an optional UI filter — `ShipmentStaffService` passes
        the caller's own permitted Telecaller set here so a Shipment
        Staff user's queue is restricted at the query level (never just
        hidden in the UI). `telecaller_id` (a single admin/fulfillment
        filter-dropdown choice) can be combined with it freely; both
        apply with AND semantics.
        """
        active_shipment = aliased(Shipment)
        stmt = (
            select(Order, active_shipment)
            .outerjoin(
                active_shipment,
                and_(
                    active_shipment.order_id == Order.id,
                    active_shipment.current_status == ShipmentStatus.PENDING,
                ),
            )
            .where(
                Order.status == OrderStatus.CONFIRMED,
                Order.fulfillment_status != FulfillmentStatus.FULFILLED,
                ~exists(
                    select(1).where(
                        Shipment.order_id == Order.id,
                        Shipment.current_status.not_in(
                            [ShipmentStatus.CANCELLED, ShipmentStatus.PENDING]
                        ),
                    )
                ),
            )
            .options(
                selectinload(Order.customer),
                selectinload(Order.items),
                selectinload(Order.confirmed_by_telecaller),
                selectinload(active_shipment.courier),
            )
        )
        if q:
            like = f"%{q}%"
            customer_match = exists(
                select(1).where(
                    and_(
                        Customer.id == Order.customer_id,
                        or_(Customer.full_name.ilike(like), Customer.phone.ilike(like)),
                    )
                )
            )
            stmt = stmt.where(or_(Order.order_number.ilike(like), customer_match))
        if payment_type:
            stmt = stmt.where(Order.payment_type == payment_type)
        if telecaller_id:
            stmt = stmt.where(Order.confirmed_by_telecaller_id == telecaller_id)
        if telecaller_ids is not None:
            stmt = stmt.where(Order.confirmed_by_telecaller_id.in_(telecaller_ids))
        if courier_id:
            stmt = stmt.where(active_shipment.courier_id == courier_id)
        if shipment_status:
            stmt = stmt.where(active_shipment.current_status == shipment_status)
        if sku:
            stmt = stmt.where(
                exists(
                    select(1).where(
                        and_(OrderItem.order_id == Order.id, OrderItem.sku.ilike(f"%{sku}%"))
                    )
                )
            )
        if date_from:
            stmt = stmt.where(Order.confirmed_at >= date_from)
        if date_to:
            stmt = stmt.where(Order.confirmed_at <= date_to)
        return stmt

    async def list_shipment_queue(
        self, query, *, page_params: PageParams
    ) -> tuple[list[tuple[Order, Shipment | None]], int]:
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        stmt = (
            query.order_by(Order.confirmed_at.desc().nulls_last(), Order.order_datetime.desc())
            .offset(page_params.offset)
            .limit(page_params.page_size)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(row[0], row[1]) for row in rows], total or 0

    async def confirmation_to_shipment_stats(
        self, *, telecaller_ids: list[uuid.UUID] | None = None
    ) -> tuple[int, int]:
        """`(ever_confirmed, shipped_or_later)`, scoped to orders that
        were ever actually *eligible* for this OMS's own shipment
        pipeline — `fulfillment_status != FULFILLED` (excludes orders
        fulfilled directly through Shopify, which never touch this OMS's
        `Shipment` table at all; same reasoning as
        `shipment_queue_query`, and the same fix — confirmed live in
        production, where omitting this filter inflated the historical
        "ever confirmed" denominator into the tens of thousands and
        made the resulting rate ~1%, meaningless noise). "Ever confirmed"
        is `status` currently at CONFIRMED or anything after it in the
        strictly-monotonic state machine (`ORDER_STATUS_TRANSITIONS`) —
        PROCESSING/PACKED/SHIPPED/DELIVERED all imply the order passed
        through CONFIRMED at some point, so a plain `IN (...)` on the
        current column is exact, no `OrderEvent` history scan needed.
        `shipped_or_later` reuses the same PICKED_UP-or-later definition
        `shipment_queue_query` and `telecaller_confirmation_counts`
        already use for "genuinely shipping, not just queued".
        """
        ever_confirmed_statuses = [
            OrderStatus.CONFIRMED,
            OrderStatus.PROCESSING,
            OrderStatus.PACKED,
            OrderStatus.SHIPPED,
            OrderStatus.DELIVERED,
        ]
        shipped_statuses = [
            ShipmentStatus.PICKED_UP,
            ShipmentStatus.IN_TRANSIT,
            ShipmentStatus.OUT_FOR_DELIVERY,
            ShipmentStatus.DELIVERED,
            ShipmentStatus.RTO_INITIATED,
            ShipmentStatus.RTO_DELIVERED,
        ]
        has_shipped = exists(
            select(1).where(
                Shipment.order_id == Order.id, Shipment.current_status.in_(shipped_statuses)
            )
        )
        stmt = select(func.count(), func.count(case((has_shipped, 1)))).where(
            Order.status.in_(ever_confirmed_statuses),
            Order.fulfillment_status != FulfillmentStatus.FULFILLED,
        )
        if telecaller_ids is not None:
            stmt = stmt.where(Order.confirmed_by_telecaller_id.in_(telecaller_ids))
        ever_confirmed, shipped_or_later = (await self.session.execute(stmt)).one()
        return int(ever_confirmed or 0), int(shipped_or_later or 0)

    async def telecaller_confirmation_counts(
        self,
        *,
        telecaller_id: uuid.UUID | None = None,
        telecaller_ids: list[uuid.UUID] | None = None,
    ) -> dict[uuid.UUID, dict[str, int]]:
        """Per-telecaller counts of orders they personally confirmed
        (`Order.confirmed_by_telecaller_id`), broken down by whether a
        shipment now exists and its outcome — a live snapshot from
        `Order`/`Shipment`'s current state, not a day-bucketed history
        (no reliable "shipped at"/"delivered at" event column exists
        outside `Shipment.actual_delivery_date`, which only covers
        delivery — see `docs` note in `telecalling_service.py`). Counts a
        confirmed order as "shipped" once any of its shipments has
        reached PICKED_UP or later (mirrors `shipment_queue_query`'s own
        definition of "still just queued"), and "delivered"/NDR/RTO from
        `Shipment.current_status` directly.
        """
        shipped_statuses = [
            ShipmentStatus.PICKED_UP,
            ShipmentStatus.IN_TRANSIT,
            ShipmentStatus.OUT_FOR_DELIVERY,
            ShipmentStatus.DELIVERED,
            ShipmentStatus.RTO_INITIATED,
            ShipmentStatus.RTO_DELIVERED,
        ]
        has_shipped = exists(
            select(1).where(
                Shipment.order_id == Order.id, Shipment.current_status.in_(shipped_statuses)
            )
        )
        has_delivered = exists(
            select(1).where(
                Shipment.order_id == Order.id,
                Shipment.current_status == ShipmentStatus.DELIVERED,
            )
        )
        has_ndr = exists(
            select(1).where(
                Shipment.order_id == Order.id, Shipment.current_status == ShipmentStatus.NDR
            )
        )
        has_rto = exists(
            select(1).where(
                Shipment.order_id == Order.id,
                Shipment.current_status.in_(
                    [ShipmentStatus.RTO_INITIATED, ShipmentStatus.RTO_DELIVERED]
                ),
            )
        )
        stmt = select(
            Order.confirmed_by_telecaller_id,
            func.count(),
            func.count(case((has_shipped, 1))),
            func.count(case((has_delivered, 1))),
            func.count(case((has_ndr, 1))),
            func.count(case((has_rto, 1))),
        ).where(Order.confirmed_by_telecaller_id.is_not(None))
        if telecaller_id is not None:
            stmt = stmt.where(Order.confirmed_by_telecaller_id == telecaller_id)
        if telecaller_ids is not None:
            stmt = stmt.where(Order.confirmed_by_telecaller_id.in_(telecaller_ids))
        stmt = stmt.group_by(Order.confirmed_by_telecaller_id)
        rows = (await self.session.execute(stmt)).all()
        return {
            tc_id: {
                "confirmed": confirmed,
                "shipped": shipped,
                "delivered": delivered,
                "ndr": ndr,
                "rto": rto,
            }
            for tc_id, confirmed, shipped, delivered, ndr, rto in rows
        }

    async def list_for_export(self, query, *, limit: int) -> list[Order]:
        """Runs `query` (from `search_query()`, already eager-loading
        `customer`/`items`/`shipments`) unpaginated, capped at `limit` rows
        — used by the orders export endpoint, which has no `page`/
        `page_size` since it streams every filtered row at once.
        `PageParams.page_size` caps at 200 (fine for the UI), too low for a
        multi-thousand-row export, hence this separate path.
        """
        stmt = query.order_by(Order.order_datetime.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class OrderItemRepository(BaseRepository[OrderItem]):
    model = OrderItem

    async def list_for_order(self, order_id: uuid.UUID) -> list[OrderItem]:
        stmt = select(OrderItem).where(OrderItem.order_id == order_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class OrderEventRepository(AppendOnlyRepository[OrderEvent]):
    model = OrderEvent

    async def list_for_order(self, order_id: uuid.UUID) -> list[OrderEvent]:
        stmt = (
            select(OrderEvent)
            .where(OrderEvent.order_id == order_id)
            .order_by(OrderEvent.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
