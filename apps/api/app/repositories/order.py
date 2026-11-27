from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import String, and_, case, exists, func, or_, select
from sqlalchemy.orm import aliased, selectinload

from app.core.config import settings
from app.core.timezone import to_ist
from app.models.customer import Customer
from app.models.enums import FulfillmentStatus, OrderStatus, ShipmentStatus
from app.models.order import Order, OrderEvent, OrderItem
from app.models.shipment import Shipment
from app.repositories.base import AppendOnlyRepository, BaseRepository
from app.repositories.shipment_effective import (
    effective_courier_name,
    effective_delivery_date,
)
from app.schemas.common import PageParams
from app.services.live_orders import apply_live_update, is_live_transient, load_order
from app.services.shipment_status import (
    UNIFIED_STATUSES,
    classify_unified_status,
    unified_status_sql_case,
)


class OrderRepository(BaseRepository[Order]):
    model = Order

    async def get_by_id(self, id_: uuid.UUID) -> Order | None:
        # Live mode: Shopify is the source of truth for order content; a
        # local historical row is only the fallback (e.g. a manual order,
        # or one Shopify no longer returns).
        if settings.SHOPIFY_LIVE_MODE:
            live = await load_order(self.session, id_)
            if live is not None:
                return live
        return await super().get_by_id(id_)

    async def get_for_update(self, id_: uuid.UUID) -> Order | None:
        """Row-locked read (`SELECT ... FOR UPDATE`) against the legacy
        `orders` table directly -- deliberately bypasses `get_by_id`'s
        live-mode branch (`load_order`/the dormant API-first shadow
        architecture has no row here to lock, and isn't part of what
        this serializes). Mirrors `ProductVariantRepository.get_for_update`:
        lets a dispatch-confirmation and a cancellation attempt on the
        same order serialize against each other instead of racing.
        """
        stmt = select(Order).where(Order.id == id_).with_for_update()
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update(self, instance: Order, **fields) -> Order:  # noqa: ANN003
        if is_live_transient(instance):
            return await apply_live_update(self.session, instance, fields)
        return await super().update(instance, **fields)

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
        if settings.SHOPIFY_LIVE_MODE:
            live = await load_order(self.session, id_)
            if live is not None:
                return live
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
        confirmed_only: bool = False,
        telecaller_id: uuid.UUID | None = None,
        confirmed_date_from: datetime | None = None,
        confirmed_date_to: datetime | None = None,
        source_systems: list[str] | None = None,
    ):
        """`confirmed_only`/`telecaller_id`/`confirmed_date_from`/
        `confirmed_date_to` back the "Confirmed by Telecaller" page
        (`GET /orders?confirmed_only=true`) — a history/audit view of
        every order a Telecaller has ever confirmed, deliberately with
        NO restriction on current order/fulfillment/shipment status
        (unlike `shipment_queue_query`'s "needs shipment now" business
        rule, which this does not touch or duplicate). All four default
        to their existing no-op values, so every current caller of
        `search_query`/`GET /orders` is unaffected.

        `source_systems` (Marketplace Orders module) restricts to one or
        more `Order.source_system` values — e.g. `GET /marketplace/orders`
        passes `["amazon", "flipkart", "meesho"]` so that workspace never
        shows a Shopify-sourced row. `None` (the default, every existing
        caller) applies no restriction at all.
        """
        stmt = self._base_query()
        if source_systems:
            stmt = stmt.where(Order.source_system.in_(source_systems))
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
        if confirmed_only:
            stmt = stmt.where(Order.confirmed_by_telecaller_id.is_not(None))
        if telecaller_id:
            stmt = stmt.where(Order.confirmed_by_telecaller_id == telecaller_id)
        if confirmed_date_from:
            stmt = stmt.where(Order.confirmed_at >= confirmed_date_from)
        if confirmed_date_to:
            stmt = stmt.where(Order.confirmed_at <= confirmed_date_to)
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
            stmt = stmt.where(func.cast(Order.shopify_tags, String).ilike(f'%"{tag}"%'))
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
            selectinload(Order.confirmed_by_telecaller),
            selectinload(Order.shipments).selectinload(Shipment.courier),
        )
        # `populate_existing`: without it, `selectinload` only populates a
        # relationship that isn't already marked loaded on a given Order
        # instance -- for one already in this session's identity map (e.g.
        # touched earlier in the very same request/session, before
        # `confirmed_by_telecaller_id` was set, the way `OrderService.
        # confirm_order`'s own state-transition path can), it would keep
        # showing a stale empty value instead of the just-committed
        # relationship. Same fix, same reasoning, as `UserRepository.
        # get_with_permissions`. A pure safety net in production (every
        # request already gets its own fresh session/identity map, so this
        # never changes real results there) that only matters for a
        # same-session multi-query sequence.
        stmt = stmt.execution_options(populate_existing=True)
        return stmt

    def for_customer_query(self, customer_id: uuid.UUID):
        return self._base_query().where(Order.customer_id == customer_id)

    async def previous_orders_for_customer(
        self, customer_id: uuid.UUID, *, exclude_order_id: uuid.UUID, limit: int = 20
    ) -> list[Order]:
        """The Telecaller order-detail page's "Customer's previous
        orders" panel. Eager-loads `items` in this same query (one extra
        `IN`-based `SELECT` for the whole page of orders, via
        `selectinload` -- never a per-row query), so the response schema
        never triggers a lazy load / `MissingGreenlet` under
        `AsyncSession`. Ordered by the real order date (`order_datetime`,
        never `created_at` — sync-insert time, which can differ for a
        backfilled/historical order) latest-first, excluding the order
        currently being viewed, and capped so a customer with a long
        history can't turn this into an unbounded query.
        """
        stmt = (
            self.for_customer_query(customer_id)
            .where(Order.id != exclude_order_id)
            .options(selectinload(Order.items))
            .order_by(Order.order_datetime.desc())
            .limit(limit)
            .execution_options(populate_existing=True)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def orders_for_customers(
        self, customer_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[Order]]:
        """Every `Order` for this page of customer ids, newest first per
        customer -- the one follow-up query `CustomerRepository.
        search_repeat_customers` needs for `order_numbers`/the latest
        order's status/payment status, kept to exactly one query for the
        whole page rather than one per customer.
        """
        if not customer_ids:
            return {}
        stmt = (
            select(Order)
            .where(Order.customer_id.in_(customer_ids))
            .order_by(Order.customer_id, Order.order_datetime.desc())
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        by_customer: dict[uuid.UUID, list[Order]] = {}
        for order in rows:
            # `.in_(customer_ids)` above already guarantees a non-None
            # `customer_id` on every row -- `Order.customer_id` is only
            # `| None` in the type because a different order can be
            # customer-less, not this filtered set.
            assert order.customer_id is not None
            by_customer.setdefault(order.customer_id, []).append(order)
        return by_customer

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

    def unified_status_base_query(
        self,
        *,
        courier_id: uuid.UUID | None = None,
        payment_type: str | None = None,
        fulfillment_status: str | None = None,
        sku: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        with_eager: bool = False,
        exclude_cancelled: bool = False,
    ):
        """Every `Order` LEFT JOINed to its "controlling" `Shipment` —
        simply the most recently created one, if any — for OMS SOW
        Module 2's unified 12-status classification
        (`app.services.shipment_status`). Deliberately does NOT exclude
        a `CANCELLED` shipment: if the order's one and only booking was
        cancelled and nothing has replaced it, the order's real current
        tracking status IS `cancelled` (see that module's judgment call
        (1)); the moment a reshipment creates a newer, non-cancelled row,
        it naturally becomes the "most recent" one instead, with zero
        special-casing. Returns `select(Order, active_shipment)`;
        callers build on top of it for counts, daily trend buckets, or
        the per-status drill-down list. Filtered on `Order.order_datetime`
        (not `confirmed_at`, which is `NULL` for the `order_placed`
        bucket) so date-range filtering works across every bucket, not
        just post-confirmation ones.

        `with_eager=True` (the drill-down list, which renders full order/
        shipment rows) adds the `selectinload`s needed to avoid an N+1
        per row. Counting/bucketing callers leave it `False` — they
        narrow the `SELECT` to just a few columns via `with_only_columns`
        afterwards, and an eager-load option for an entity no longer in
        the `SELECT` list is itself an error.

        `exclude_cancelled=True` drops `Order.status == CANCELLED` rows
        from this population entirely (not just the "cancelled" unified-
        status label) -- needed by the Fulfilled/Unfulfilled payment
        breakdown's "fulfilled" slice specifically: a cancelled order
        that was fulfilled and already has an active, non-cancelled
        `Shipment` (e.g. shipped-then-cancelled, `current_status ==
        DELIVERED`) classifies into that shipment's real unified status
        (e.g. `delivered`), NOT `cancelled` -- `unified_status_sql_case`
        checks the shipment's own status before the order-level
        `CANCELLED` fallback, which only applies when there is no
        `Shipment` row at all. Without this flag, such an order would be
        excluded from `AnalyticsService.get_fulfillment_payment_
        breakdown`'s "fulfilled" total (which filters `Order.status`
        directly) but still counted into `get_fulfillment_payment_
        classification`'s "fulfilled" slice and still appear in its
        drill-down -- the exact reconciliation mismatch this flag
        closes. Callers pass it only for the `fulfillment_status ==
        "fulfilled"` case; Module 2's own Shipment Tracking page never
        sets `fulfillment_status` at all, so it is never affected by
        this flag and keeps showing cancelled orders under their own
        `cancelled` bucket as before.
        """
        active_shipment = aliased(Shipment)
        latest_shipment_id = (
            select(Shipment.id)
            .where(Shipment.order_id == Order.id)
            .order_by(Shipment.created_at.desc())
            .limit(1)
            .correlate(Order)
            .scalar_subquery()
        )
        stmt = select(Order, active_shipment).outerjoin(
            active_shipment, active_shipment.id == latest_shipment_id
        )
        if with_eager:
            stmt = stmt.options(
                selectinload(Order.customer),
                selectinload(Order.items),
                selectinload(active_shipment.courier),
            )
        if courier_id:
            stmt = stmt.where(active_shipment.courier_id == courier_id)
        if payment_type:
            stmt = stmt.where(Order.payment_type == payment_type)
        if fulfillment_status:
            # Shopify's own `Order.fulfillment_status` (FULFILLED/
            # UNFULFILLED/PARTIAL) -- a different axis from the unified
            # 12-status classification above (e.g. a Shopify-FULFILLED
            # order can still be `ready_to_ship` here if it has no local
            # Shipment row -- see `fulfilled_tracking_unavailable` in
            # `app.services.shipment_status`). Added for the Fulfilled/
            # Unfulfilled dashboard, which filters on the Shopify
            # definition first and then shows the unified-status
            # classification *within* that slice.
            stmt = stmt.where(Order.fulfillment_status == fulfillment_status)
        if sku:
            stmt = stmt.where(
                exists(
                    select(1).where(
                        and_(OrderItem.order_id == Order.id, OrderItem.sku.ilike(f"%{sku}%"))
                    )
                )
            )
        if date_from:
            stmt = stmt.where(Order.order_datetime >= date_from)
        if date_to:
            stmt = stmt.where(Order.order_datetime <= date_to)
        if exclude_cancelled:
            stmt = stmt.where(Order.status != OrderStatus.CANCELLED)
        return stmt, active_shipment

    async def unified_status_counts(
        self,
        *,
        courier_id: uuid.UUID | None = None,
        payment_type: str | None = None,
        fulfillment_status: str | None = None,
        sku: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> dict[str, int]:
        """Whole-funnel counts across all 12 `UNIFIED_STATUSES`, scoped
        by the same filters as `unified_status_base_query` — backs the
        Module 2 analytics endpoint's percentage math.
        """
        base_stmt, active_shipment = self.unified_status_base_query(
            courier_id=courier_id,
            payment_type=payment_type,
            fulfillment_status=fulfillment_status,
            sku=sku,
            date_from=date_from,
            date_to=date_to,
        )
        bucket = unified_status_sql_case(
            order_status_col=Order.status,
            fulfillment_status_col=Order.fulfillment_status,
            shipment_current_status_col=active_shipment.current_status,
            shipment_rto_status_col=active_shipment.rto_status,
        )
        # `with_only_columns` keeps the same FROM/WHERE (the join and
        # filters above) but narrows the SELECT to just the
        # classification itself; wrapping THAT in a subquery exports a
        # single plain `unified_status` column the outer query can
        # group by, without re-referencing `Order`/`active_shipment`
        # columns outside the join's scope.
        classified = base_stmt.order_by(None).with_only_columns(bucket.label("unified_status")).subquery()
        stmt = select(classified.c.unified_status, func.count()).group_by(classified.c.unified_status)
        rows = (await self.session.execute(stmt)).all()
        counts = dict.fromkeys(UNIFIED_STATUSES, 0)
        for status, count in rows:
            counts[status] = count
        return counts

    async def unified_status_amounts(
        self,
        *,
        courier_id: uuid.UUID | None = None,
        payment_type: str | None = None,
        fulfillment_status: str | None = None,
        sku: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        exclude_cancelled: bool = False,
    ) -> dict[str, dict[str, object]]:
        """Like `unified_status_counts`, but also sums `Order.total_amount`
        per bucket -- the Fulfilled/Unfulfilled analytics and COD Orders
        dashboard both need amount, not just count, per unified-status
        classification. Purely additive (new method, new return shape);
        does not change `unified_status_counts`'s existing `dict[str,
        int]` contract or any of its callers (Modules 2/3/4). Each order
        contributes its `total_amount` exactly once -- same
        most-recent-shipment join as every other unified-status query,
        so a reshipped order is never double-summed. `exclude_cancelled`
        -- see `unified_status_base_query`'s docstring.
        """
        base_stmt, active_shipment = self.unified_status_base_query(
            courier_id=courier_id,
            payment_type=payment_type,
            fulfillment_status=fulfillment_status,
            sku=sku,
            date_from=date_from,
            date_to=date_to,
            exclude_cancelled=exclude_cancelled,
        )
        bucket = unified_status_sql_case(
            order_status_col=Order.status,
            fulfillment_status_col=Order.fulfillment_status,
            shipment_current_status_col=active_shipment.current_status,
            shipment_rto_status_col=active_shipment.rto_status,
        )
        classified = (
            base_stmt.order_by(None)
            .with_only_columns(
                bucket.label("unified_status"), Order.total_amount.label("total_amount")
            )
            .subquery()
        )
        stmt = select(
            classified.c.unified_status,
            func.count(),
            func.coalesce(func.sum(classified.c.total_amount), 0),
        ).group_by(classified.c.unified_status)
        rows = (await self.session.execute(stmt)).all()
        result: dict[str, dict[str, object]] = {
            status: {"count": 0, "amount": Decimal("0")} for status in UNIFIED_STATUSES
        }
        for status, count, amount in rows:
            result[status] = {"count": count, "amount": Decimal(amount)}
        return result

    async def unified_status_shipment_coverage(
        self,
        *,
        courier_id: uuid.UUID | None = None,
        payment_type: str | None = None,
        fulfillment_status: str | None = None,
        sku: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> dict[str, int | Decimal]:
        """Orders split by whether they have a "controlling" `Shipment`
        row at all (the same most-recent-shipment join every other
        `unified_status_*` method uses), independent of what unified
        status that shipment (or its absence) classifies into. This is
        the Module 2 dashboard's own population vs. a courier's
        shipment-level export: an order can land in `order_placed`/
        `ready_to_ship`/`fulfilled_tracking_unavailable`/`cancelled`
        with zero local `Shipment` row (never pushed to Shiprocket, or
        fulfilled directly through Shopify -- see `unified_status_base_
        query`'s own docstring), and none of those orders will ever
        appear in Shiprocket's own counts, correctly. Surfacing the
        split explicitly here is what lets the dashboard self-explain a
        gap like "7,491 OMS orders vs. 4,997 Shiprocket shipments"
        instead of that gap looking like missing/wrong data.
        `with_count + without_count` always equals the matching
        `unified_status_counts` call's `total_orders`.
        """
        base_stmt, active_shipment = self.unified_status_base_query(
            courier_id=courier_id,
            payment_type=payment_type,
            fulfillment_status=fulfillment_status,
            sku=sku,
            date_from=date_from,
            date_to=date_to,
        )
        has_shipment = active_shipment.id.is_not(None)
        narrowed = base_stmt.order_by(None).with_only_columns(
            has_shipment.label("has_shipment"), Order.total_amount.label("total_amount")
        )
        subquery = narrowed.subquery()
        stmt = select(
            subquery.c.has_shipment,
            func.count(),
            func.coalesce(func.sum(subquery.c.total_amount), 0),
        ).group_by(subquery.c.has_shipment)
        rows = (await self.session.execute(stmt)).all()
        with_count = with_amount = without_count = without_amount = 0
        for has_shipment_value, count, amount in rows:
            if has_shipment_value:
                with_count, with_amount = count, Decimal(amount)
            else:
                without_count, without_amount = count, Decimal(amount)
        return {
            "orders_with_shipment_count": with_count,
            "orders_with_shipment_amount": Decimal(with_amount),
            "orders_without_shipment_count": without_count,
            "orders_without_shipment_amount": Decimal(without_amount),
        }

    async def unified_status_counts_by_courier(
        self,
        *,
        courier_id: uuid.UUID | None = None,
        payment_type: str | None = None,
        sku: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[tuple[uuid.UUID | None, str | None, str, str, int]]:
        """Like `unified_status_counts`, but broken out by `(courier_id,
        payment_type, unified_status)` instead of collapsed to a single
        total -- OMS SOW Module 4's per-partner allocation/comparison
        needs both the courier split (who is this order's allocated
        shipment with) and the payment-type split (COD vs Prepaid success
        rate per partner) at once, from one query. `courier_id`/
        `payment_type` narrow the order population the same as
        `unified_status_counts` (e.g. "COD orders only"); they do not
        collapse the per-courier/per-payment-type breakdown itself, which
        is always returned in full so Module 4 can compare every partner
        side-by-side.
        """
        base_stmt, active_shipment = self.unified_status_base_query(
            courier_id=courier_id,
            payment_type=payment_type,
            sku=sku,
            date_from=date_from,
            date_to=date_to,
        )
        bucket = unified_status_sql_case(
            order_status_col=Order.status,
            fulfillment_status_col=Order.fulfillment_status,
            shipment_current_status_col=active_shipment.current_status,
            shipment_rto_status_col=active_shipment.rto_status,
        )
        classified = (
            base_stmt.order_by(None)
            .with_only_columns(
                active_shipment.courier_id.label("courier_id"),
                effective_courier_name(active_shipment).label("courier_name"),
                Order.payment_type.label("payment_type"),
                bucket.label("unified_status"),
            )
            .subquery()
        )
        stmt = select(
            classified.c.courier_id,
            classified.c.courier_name,
            classified.c.payment_type,
            classified.c.unified_status,
            func.count(),
        ).group_by(
            classified.c.courier_id,
            classified.c.courier_name,
            classified.c.payment_type,
            classified.c.unified_status,
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            (courier_id, courier_name, payment_type.value, status, count)
            for courier_id, courier_name, payment_type, status, count in rows
        ]

    async def fulfilment_to_delivery_rows(
        self,
        *,
        courier_id: uuid.UUID | None = None,
        payment_type: str | None = None,
        sku: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[tuple[uuid.UUID | None, str | None, str, str, datetime, datetime | None]]:
        """`(courier_id, payment_type, unified_status, fulfilment_start,
        actual_delivery_date)` per order -- OMS SOW Module 4 item B.7
        ("average fulfilment-to-delivery time"), the full order-creation/
        confirmation -> actual-delivery cycle, as distinct from Module
        3's pickup -> delivery courier-only leg. Reuses the exact same
        `fulfilment_start` choice as `FulfilmentPerformanceService`
        (`confirmed_at` when set, else `order_datetime`) for consistency
        across the two modules' numbers.
        """
        base_stmt, active_shipment = self.unified_status_base_query(
            courier_id=courier_id,
            payment_type=payment_type,
            sku=sku,
            date_from=date_from,
            date_to=date_to,
        )
        bucket = unified_status_sql_case(
            order_status_col=Order.status,
            fulfillment_status_col=Order.fulfillment_status,
            shipment_current_status_col=active_shipment.current_status,
            shipment_rto_status_col=active_shipment.rto_status,
        )
        stmt = base_stmt.with_only_columns(
            active_shipment.courier_id,
            effective_courier_name(active_shipment).label("courier_name"),
            Order.payment_type,
            bucket.label("unified_status"),
            func.coalesce(Order.confirmed_at, Order.order_datetime).label("fulfilment_start"),
            effective_delivery_date(active_shipment).label("actual_delivery_date"),
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            (courier_id, courier_name, payment_type.value, status, fulfilment_start, actual_delivery_date)
            for courier_id, courier_name, payment_type, status, fulfilment_start, actual_delivery_date in rows
        ]

    async def unified_status_daily_counts(
        self,
        *,
        date_from: datetime,
        date_to: datetime,
        courier_id: uuid.UUID | None = None,
        payment_type: str | None = None,
        sku: str | None = None,
    ) -> list[dict[str, object]]:
        """Day-bucketed (IST) unified-status distribution over a date
        range — the SOW's "date-wise shipment status tracking" /
        "historical shipment status reports". Fetches `(order_datetime,
        status, fulfillment_status, shipment current_status, shipment
        rto_status)` rows and buckets/classifies in Python — same
        dialect-portability rationale as
        `ShipmentRepository.daily_created_and_delivered` (small result
        set for a dashboard trend, no Postgres-only `date_trunc`).
        """
        base_stmt, active_shipment = self.unified_status_base_query(
            courier_id=courier_id,
            payment_type=payment_type,
            sku=sku,
            date_from=date_from,
            date_to=date_to,
        )
        stmt = base_stmt.with_only_columns(
            Order.order_datetime,
            Order.status,
            Order.fulfillment_status,
            active_shipment.current_status,
            active_shipment.rto_status,
        )
        rows = (await self.session.execute(stmt)).all()
        buckets: dict[str, dict[str, int]] = {}
        for order_datetime, order_status, fulfillment_status, shipment_status, rto_status in rows:
            day = to_ist(order_datetime).date().isoformat()
            unified = classify_unified_status(
                order_status=order_status.value,
                fulfillment_status=fulfillment_status.value,
                shipment_current_status=shipment_status.value if shipment_status else None,
                shipment_rto_status=rto_status.value if rto_status else None,
            )
            day_counts = buckets.setdefault(day, dict.fromkeys(UNIFIED_STATUSES, 0))
            day_counts[unified] += 1
        return [{"date": day, **counts} for day, counts in sorted(buckets.items())]

    def unified_status_drilldown_query(
        self,
        *,
        unified_status: str | None = None,
        unified_statuses: Sequence[str] | None = None,
        courier_id: uuid.UUID | None = None,
        payment_type: str | None = None,
        fulfillment_status: str | None = None,
        sku: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        exclude_cancelled: bool = False,
    ):
        """Every order whose derived unified status equals
        `unified_status` — the SOW's "ability to view individual orders
        under each status". Same filters/join as the aggregate counts,
        just with an extra `WHERE` on the classification expression
        itself (a `case()` is a normal SQL column expression, usable in
        `WHERE` exactly like any other). `exclude_cancelled` -- see
        `unified_status_base_query`'s docstring.
        """
        base_stmt, active_shipment = self.unified_status_base_query(
            courier_id=courier_id,
            payment_type=payment_type,
            fulfillment_status=fulfillment_status,
            sku=sku,
            date_from=date_from,
            date_to=date_to,
            with_eager=True,
            exclude_cancelled=exclude_cancelled,
        )
        bucket = unified_status_sql_case(
            order_status_col=Order.status,
            fulfillment_status_col=Order.fulfillment_status,
            shipment_current_status_col=active_shipment.current_status,
            shipment_rto_status_col=active_shipment.rto_status,
        )
        if unified_status is not None:
            base_stmt = base_stmt.where(bucket == unified_status)
        elif unified_statuses is not None:
            base_stmt = base_stmt.where(bucket.in_(list(unified_statuses)))
        return base_stmt, active_shipment

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
