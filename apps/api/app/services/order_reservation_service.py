"""Legacy order-placement inventory reservation (active `Order`s only).

Same algorithm and concurrency model as `app.services.
inventory_reservation_service.InventoryReservationService` (row-locks the
affected `product_variants` rows in a fixed id order, unique-constraint
as the second line of defence against a concurrent duplicate reserve) --
deliberately re-implemented against `app.models.order_reservation.
OrderInventoryReservation` instead of reusing that service, since the
dormant one's table is FK'd to `order_workflow.id` (the API-first shadow
architecture) and this module must stay off that architecture entirely.

Reused from the dormant service: `app.services.inventory_reservation_
service.default_warehouse_id` (a plain `Warehouse` lookup/creation helper,
not coupled to any order architecture) and `boxes_for` (the same
`pack_size`/`packets_per_box` ceiling conversion `InventoryService.
apply_dispatch` uses, so a reservation and the dispatch that later
consumes it always agree on the box-equivalent of an `OrderItem.quantity`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.order_reservation import (
    RESERVATION_ACTIVE,
    RESERVATION_CONSUMED,
    RESERVATION_RELEASED,
    OrderInventoryReservation,
)
from app.models.product import ProductVariant
from app.services.inventory_reservation_service import boxes_for, default_warehouse_id


@dataclass(frozen=True)
class LegacyStockPosition:
    variant_id: uuid.UUID
    physical: int
    reserved: int

    @property
    def available_to_promise(self) -> int:
        return self.physical - self.reserved


async def lock_variants(
    session: AsyncSession, variant_ids: list[uuid.UUID]
) -> dict[uuid.UUID, ProductVariant]:
    """Row-locks the variants in a stable order. (SQLite ignores FOR UPDATE.)"""
    rows = (
        (
            await session.execute(
                select(ProductVariant)
                .where(ProductVariant.id.in_(sorted(set(variant_ids))))
                .order_by(ProductVariant.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    return {v.id: v for v in rows}


async def reserved_quantities(
    session: AsyncSession, variant_ids: list[uuid.UUID], *, exclude_order: uuid.UUID | None = None
) -> dict[uuid.UUID, int]:
    stmt = (
        select(
            OrderInventoryReservation.product_variant_id,
            func.coalesce(func.sum(OrderInventoryReservation.quantity), 0),
        )
        .where(
            OrderInventoryReservation.product_variant_id.in_(variant_ids),
            OrderInventoryReservation.status == RESERVATION_ACTIVE,
        )
        .group_by(OrderInventoryReservation.product_variant_id)
    )
    if exclude_order is not None:
        stmt = stmt.where(OrderInventoryReservation.order_id != exclude_order)
    return {vid: int(total) for vid, total in (await session.execute(stmt)).all()}


async def total_reserved_boxes(session: AsyncSession) -> int:
    """SUM of every ACTIVE legacy reservation, across every variant --
    the dashboard's `reserved_boxes` figure."""
    total = await session.scalar(
        select(func.coalesce(func.sum(OrderInventoryReservation.quantity), 0)).where(
            OrderInventoryReservation.status == RESERVATION_ACTIVE
        )
    )
    return int(total or 0)


async def legacy_stock_position(
    session: AsyncSession, variant_id: uuid.UUID
) -> LegacyStockPosition:
    variant = await session.get(ProductVariant, variant_id)
    if variant is None:
        raise NotFoundError("Product variant not found.")
    reserved = (await reserved_quantities(session, [variant_id])).get(variant_id, 0)
    return LegacyStockPosition(variant_id, variant.available_quantity, reserved)


class LegacyOrderReservationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def reserve(
        self, order_id: uuid.UUID, lines: list[tuple[uuid.UUID, int]]
    ) -> list[OrderInventoryReservation]:
        """Reserve `lines` = [(variant_id, unit quantity)] for an order,
        all-or-nothing and idempotent per (variant, order). Raises
        `ConflictError` (nothing reserved) if any line would exceed
        available-to-promise. Does not commit."""
        if not lines:
            return []
        merged: dict[uuid.UUID, int] = {}
        for variant_id, quantity in lines:
            if quantity <= 0:
                raise ValidationError("Reserved quantity must be positive.")
            merged[variant_id] = merged.get(variant_id, 0) + quantity

        variants = await lock_variants(self.session, list(merged))
        missing = [str(v) for v in merged if v not in variants]
        if missing:
            raise NotFoundError("Product variant not found: " + ", ".join(missing))
        others = await reserved_quantities(self.session, list(merged), exclude_order=order_id)

        shortages = []
        needed: dict[uuid.UUID, int] = {}
        for variant_id, quantity in merged.items():
            variant = variants[variant_id]
            boxes = boxes_for(quantity, variant)
            needed[variant_id] = boxes
            available = variant.available_quantity - others.get(variant_id, 0)
            if boxes > available:
                shortages.append(
                    {
                        "variant_id": str(variant_id),
                        "sku": variant.sku,
                        "available_to_promise": available,
                        "required": boxes,
                    }
                )
        if shortages:
            raise ConflictError(
                "Cannot reserve stock — insufficient available stock for one or more items.",
                details={"error_type": "insufficient_stock", "items": shortages},
            )

        warehouse_id = await default_warehouse_id(self.session)
        held: list[OrderInventoryReservation] = []
        for variant_id in sorted(needed):
            existing = (
                await self.session.execute(
                    select(OrderInventoryReservation).where(
                        OrderInventoryReservation.product_variant_id == variant_id,
                        OrderInventoryReservation.order_id == order_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.status == RESERVATION_CONSUMED:
                    held.append(existing)  # already dispatched: nothing more to hold
                    continue
                existing.quantity = needed[variant_id]
                existing.status = RESERVATION_ACTIVE
                held.append(existing)
                continue
            reservation = OrderInventoryReservation(
                product_variant_id=variant_id,
                order_id=order_id,
                warehouse_id=warehouse_id,
                quantity=needed[variant_id],
                status=RESERVATION_ACTIVE,
            )
            self.session.add(reservation)
            held.append(reservation)
        try:
            await self.session.flush()
        except IntegrityError as exc:  # a concurrent reserve for the same order won the race
            await self.session.rollback()
            raise ConflictError(
                "These items are already being reserved for this order.",
                details={"error_type": "reservation_conflict"},
            ) from exc
        return held

    async def release(self, order_id: uuid.UUID) -> int:
        """Release every ACTIVE reservation of an order (idempotent). Does
        not commit; returns how many were released."""
        result = await self.session.execute(
            update(OrderInventoryReservation)
            .where(
                OrderInventoryReservation.order_id == order_id,
                OrderInventoryReservation.status == RESERVATION_ACTIVE,
            )
            .values(status=RESERVATION_RELEASED)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def consume(
        self, order_id: uuid.UUID, variant_ids: list[uuid.UUID] | None = None
    ) -> int:
        """Mark the order's ACTIVE reservations consumed (stock has
        physically left). Idempotent; does not commit."""
        stmt = (
            update(OrderInventoryReservation)
            .where(
                OrderInventoryReservation.order_id == order_id,
                OrderInventoryReservation.status == RESERVATION_ACTIVE,
            )
            .values(status=RESERVATION_CONSUMED)
        )
        if variant_ids is not None:
            stmt = stmt.where(OrderInventoryReservation.product_variant_id.in_(variant_ids))
        return int(getattr(await self.session.execute(stmt), "rowcount", 0) or 0)
