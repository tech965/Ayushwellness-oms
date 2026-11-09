"""Stock reservations and provider-variant mappings (OMS-owned).

Concurrency model (PostgreSQL): every stock decision happens under a row
lock on the affected `product_variants` row (`SELECT ... FOR UPDATE`),
acquired in a fixed (id) order so two multi-line reservations cannot
deadlock. The unique constraint on (variant, order) is the second line of
defence: a duplicate reserve for the same order can never hold stock twice,
whichever request wins. Dispatch/RTO movements keep their own unique
(variant, order, type) constraint, so a replayed event cannot adjust stock
twice either.

Units are boxes, as everywhere in inventory:
  physical            = ProductVariant.available_quantity (on hand)
  reserved            = SUM(active reservations)
  available_to_promise = physical - reserved
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.inventory_architecture import (
    DEFAULT_WAREHOUSE_CODE,
    RESERVATION_ACTIVE,
    RESERVATION_CONSUMED,
    RESERVATION_RELEASED,
    InventoryReservation,
    ProviderVariantMapping,
    Warehouse,
)
from app.models.product import ProductVariant


@dataclass(frozen=True)
class StockPosition:
    variant_id: uuid.UUID
    physical: int
    reserved: int

    @property
    def available_to_promise(self) -> int:
        return self.physical - self.reserved


def boxes_for(quantity: int, variant: ProductVariant) -> int:
    """Same ceiling formula `InventoryService.apply_dispatch` deducts with."""
    return math.ceil((quantity * variant.pack_size) / max(variant.packets_per_box, 1))


async def default_warehouse_id(session: AsyncSession) -> uuid.UUID:
    """The default warehouse's id, creating `MAIN` if a database has none
    (the migration inserts it; this keeps a hand-built or test database
    working). Race-safe: the unique `code` makes a concurrent creator lose."""
    found = (
        await session.execute(select(Warehouse.id).where(Warehouse.is_default.is_(True)).limit(1))
    ).scalar_one_or_none()
    if found is not None:
        return found
    try:
        async with session.begin_nested():
            warehouse = Warehouse(
                code=DEFAULT_WAREHOUSE_CODE, name="Main warehouse", is_default=True
            )
            session.add(warehouse)
            await session.flush()
            return warehouse.id
    except IntegrityError:
        return (
            await session.execute(
                select(Warehouse.id).where(Warehouse.code == DEFAULT_WAREHOUSE_CODE)
            )
        ).scalar_one()


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
            InventoryReservation.variant_id,
            func.coalesce(func.sum(InventoryReservation.quantity), 0),
        )
        .where(
            InventoryReservation.variant_id.in_(variant_ids),
            InventoryReservation.status == RESERVATION_ACTIVE,
        )
        .group_by(InventoryReservation.variant_id)
    )
    if exclude_order is not None:
        stmt = stmt.where(InventoryReservation.order_id != exclude_order)
    return {vid: int(total) for vid, total in (await session.execute(stmt)).all()}


async def stock_position(session: AsyncSession, variant_id: uuid.UUID) -> StockPosition:
    variant = await session.get(ProductVariant, variant_id)
    if variant is None:
        raise NotFoundError("Product variant not found.")
    reserved = (await reserved_quantities(session, [variant_id])).get(variant_id, 0)
    return StockPosition(variant_id, variant.available_quantity, reserved)


class InventoryReservationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def reserve(
        self, order_id: uuid.UUID, lines: list[tuple[uuid.UUID, int]]
    ) -> list[InventoryReservation]:
        """Reserve `lines` = [(variant_id, packet quantity)] for an order,
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
        held: list[InventoryReservation] = []
        for variant_id in sorted(needed):
            existing = (
                await self.session.execute(
                    select(InventoryReservation).where(
                        InventoryReservation.variant_id == variant_id,
                        InventoryReservation.order_id == order_id,
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
            reservation = InventoryReservation(
                variant_id=variant_id,
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
            update(InventoryReservation)
            .where(
                InventoryReservation.order_id == order_id,
                InventoryReservation.status == RESERVATION_ACTIVE,
            )
            .values(status=RESERVATION_RELEASED)
        )
        return int(getattr(result, 'rowcount', 0) or 0)

    async def consume(self, order_id: uuid.UUID, variant_ids: list[uuid.UUID] | None = None) -> int:
        """Mark the order's ACTIVE reservations consumed (stock has physically
        left). Idempotent; does not commit."""
        stmt = (
            update(InventoryReservation)
            .where(
                InventoryReservation.order_id == order_id,
                InventoryReservation.status == RESERVATION_ACTIVE,
            )
            .values(status=RESERVATION_CONSUMED)
        )
        if variant_ids is not None:
            stmt = stmt.where(InventoryReservation.variant_id.in_(variant_ids))
        return int(getattr(await self.session.execute(stmt), 'rowcount', 0) or 0)


class VariantMappingService:
    """Provider variant <-> internal variant mapping (identifiers only)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def map(
        self,
        *,
        provider: str,
        external_variant_id: str,
        variant_id: uuid.UUID,
        external_product_id: str | None = None,
    ) -> ProviderVariantMapping:
        if await self.session.get(ProductVariant, variant_id) is None:
            raise NotFoundError("Product variant not found.")
        by_external = await self.lookup(provider, external_variant_id)
        if by_external is not None:
            if by_external.variant_id == variant_id:
                return by_external  # idempotent
            raise ConflictError(
                "That provider variant is already mapped to a different OMS variant.",
                details={"error_type": "mapping_conflict"},
            )
        by_variant = (
            await self.session.execute(
                select(ProviderVariantMapping).where(
                    ProviderVariantMapping.provider == provider,
                    ProviderVariantMapping.variant_id == variant_id,
                )
            )
        ).scalar_one_or_none()
        if by_variant is not None:
            raise ConflictError(
                "That OMS variant is already mapped to a different provider variant.",
                details={"error_type": "mapping_conflict"},
            )
        mapping = ProviderVariantMapping(
            provider=provider,
            external_variant_id=external_variant_id,
            external_product_id=external_product_id,
            variant_id=variant_id,
        )
        self.session.add(mapping)
        await self.session.flush()
        return mapping

    async def lookup(
        self, provider: str, external_variant_id: str
    ) -> ProviderVariantMapping | None:
        return (
            await self.session.execute(
                select(ProviderVariantMapping).where(
                    ProviderVariantMapping.provider == provider,
                    ProviderVariantMapping.external_variant_id == external_variant_id,
                )
            )
        ).scalar_one_or_none()

    async def mappings_for(
        self, provider: str, external_variant_ids: list[str]
    ) -> dict[str, ProviderVariantMapping]:
        if not external_variant_ids:
            return {}
        rows = (
            (
                await self.session.execute(
                    select(ProviderVariantMapping).where(
                        ProviderVariantMapping.provider == provider,
                        ProviderVariantMapping.external_variant_id.in_(external_variant_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        return {m.external_variant_id: m for m in rows}

    async def variants_by_sku(self, skus: list[str]) -> dict[str, ProductVariant]:
        clean = [s for s in skus if s]
        if not clean:
            return {}
        rows = (
            (
                await self.session.execute(
                    select(ProductVariant).where(ProductVariant.sku.in_(clean))
                )
            )
            .scalars()
            .all()
        )
        return {v.sku: v for v in rows}

    async def auto_map_by_sku(
        self, provider: str, catalogue: list[dict[str, Any]]
    ) -> dict[str, list[str]]:
        """Maps live catalogue variants whose SKU exactly equals an OMS
        variant's SKU and that are not yet mapped. Reports what it did."""
        mapped: list[str] = []
        skipped: list[str] = []
        existing = await self.mappings_for(provider, [c["external_variant_id"] for c in catalogue])
        by_sku = await self.variants_by_sku([c.get("sku") or "" for c in catalogue])
        for item in catalogue:
            external = item["external_variant_id"]
            if external in existing:
                continue
            variant = by_sku.get(item.get("sku") or "")
            if variant is None:
                skipped.append(external)
                continue
            try:
                await self.map(
                    provider=provider,
                    external_variant_id=external,
                    external_product_id=item.get("external_product_id"),
                    variant_id=variant.id,
                )
                mapped.append(external)
            except ConflictError:
                skipped.append(external)
        return {"mapped": mapped, "unmatched": skipped}
