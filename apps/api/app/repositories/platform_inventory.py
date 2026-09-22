"""Repository for `PlatformStockMovement` — the manual, date-wise
marketplace stock ledger. Mirrors `InventoryMovementRepository`'s shape
(`app.repositories.inventory`) wherever the two ledgers' concerns line
up, but adds the platform/date-bucketed reads that ledger has no reason
to support.
"""

from __future__ import annotations

import uuid
from datetime import date as date_type

from sqlalchemy import func, select
from sqlalchemy.orm import aliased, selectinload

from app.models.enums import ProductMarketplaceMovementType
from app.models.platform_inventory import PlatformStockMovement, ProductMarketplaceMovement
from app.models.product import ProductVariant
from app.repositories.base import AppendOnlyRepository


class PlatformStockMovementRepository(AppendOnlyRepository[PlatformStockMovement]):
    model = PlatformStockMovement

    async def get_by_id_with_relations(self, id_: uuid.UUID) -> PlatformStockMovement | None:
        """Re-fetch with `actor` eager-loaded — `create()` (plain
        `session.add`+flush) never populates that relationship, so every
        write path re-fetches through this before building a response,
        exactly like `InventoryMovementRepository.get_by_id_with_relations`.
        """
        stmt = (
            select(PlatformStockMovement)
            .where(PlatformStockMovement.id == id_)
            .options(selectinload(PlatformStockMovement.actor))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_latest_as_of(
        self, *, product_variant_id: uuid.UUID, platform: str, as_of: date_type
    ) -> PlatformStockMovement | None:
        """The running balance for one (variant, platform) as of the end
        of `as_of` — i.e. the closing stock for that calendar date. This
        IS "stock as of the selected date": the latest row whose
        `stock_date` is on or before `as_of`, never a row from a LATER
        date (a future backfilled entry must never leak into a past
        date's balance).
        """
        stmt = (
            select(PlatformStockMovement)
            .where(
                PlatformStockMovement.product_variant_id == product_variant_id,
                PlatformStockMovement.platform == platform,
                PlatformStockMovement.stock_date <= as_of,
            )
            .order_by(
                PlatformStockMovement.stock_date.desc(),
                PlatformStockMovement.created_at.desc(),
                PlatformStockMovement.id.desc(),
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_latest_as_of_bulk(
        self, *, product_variant_ids: list[uuid.UUID], platform: str, as_of: date_type
    ) -> dict[uuid.UUID, PlatformStockMovement]:
        """The same lookup as `get_latest_as_of`, for every variant in
        `product_variant_ids` in one query (no N+1 across a product's
        several SKUs) — reduced to "latest row per variant" in Python
        rather than a dialect-specific `DISTINCT ON`, since this table's
        expected per-variant row count is small and the app runs both
        SQLite (tests) and Postgres (prod).
        """
        if not product_variant_ids:
            return {}
        stmt = (
            select(PlatformStockMovement)
            .where(
                PlatformStockMovement.product_variant_id.in_(product_variant_ids),
                PlatformStockMovement.platform == platform,
                PlatformStockMovement.stock_date <= as_of,
            )
            .order_by(
                PlatformStockMovement.product_variant_id,
                PlatformStockMovement.stock_date.desc(),
                PlatformStockMovement.created_at.desc(),
                PlatformStockMovement.id.desc(),
            )
        )
        result = await self.session.execute(stmt)
        latest: dict[uuid.UUID, PlatformStockMovement] = {}
        for row in result.scalars().all():
            if row.product_variant_id not in latest:
                latest[row.product_variant_id] = row
        return latest

    async def sum_for_date(
        self, *, product_variant_id: uuid.UUID, platform: str, stock_date: date_type
    ) -> tuple[int, int]:
        """`(stock_added, stock_deducted)` — both positive integers — for
        movements attributed to exactly `stock_date` (never "as of", see
        `get_latest_as_of` for that). This is "movements DURING the
        selected date", the other half of the balance/movements
        distinction the date filter must preserve.
        """
        stmt = select(
            PlatformStockMovement.movement_type, PlatformStockMovement.quantity_delta
        ).where(
            PlatformStockMovement.product_variant_id == product_variant_id,
            PlatformStockMovement.platform == platform,
            PlatformStockMovement.stock_date == stock_date,
        )
        result = await self.session.execute(stmt)
        added = 0
        deducted = 0
        for _movement_type, delta in result.all():
            if delta >= 0:
                added += delta
            else:
                deducted += -delta
        return added, deducted

    async def sum_for_date_bulk(
        self, *, product_variant_ids: list[uuid.UUID], platform: str, stock_date: date_type
    ) -> dict[uuid.UUID, tuple[int, int]]:
        """`{variant_id: (stock_added, stock_deducted)}` for `stock_date`
        across every variant in `product_variant_ids` in one query — the
        bulk counterpart to `sum_for_date`, used by the product-level
        platform-stock summary so it never issues one query per SKU.
        """
        if not product_variant_ids:
            return {}
        stmt = select(
            PlatformStockMovement.product_variant_id,
            PlatformStockMovement.quantity_delta,
        ).where(
            PlatformStockMovement.product_variant_id.in_(product_variant_ids),
            PlatformStockMovement.platform == platform,
            PlatformStockMovement.stock_date == stock_date,
        )
        result = await self.session.execute(stmt)
        totals: dict[uuid.UUID, tuple[int, int]] = {}
        for variant_id, delta in result.all():
            added, deducted = totals.get(variant_id, (0, 0))
            if delta >= 0:
                added += delta
            else:
                deducted += -delta
            totals[variant_id] = (added, deducted)
        return totals

    def search_query(
        self,
        *,
        product_variant_id: uuid.UUID,
        platform: str | None = None,
        date_from: date_type | None = None,
        date_to: date_type | None = None,
    ):
        stmt = (
            self._base_query()
            .where(PlatformStockMovement.product_variant_id == product_variant_id)
            .options(
                selectinload(PlatformStockMovement.product_variant).selectinload(
                    ProductVariant.product
                ),
                selectinload(PlatformStockMovement.actor),
            )
        )
        if platform:
            stmt = stmt.where(PlatformStockMovement.platform == platform)
        if date_from:
            stmt = stmt.where(PlatformStockMovement.stock_date >= date_from)
        if date_to:
            stmt = stmt.where(PlatformStockMovement.stock_date <= date_to)
        return stmt

    async def list_for_date_range(
        self,
        *,
        product_variant_id: uuid.UUID,
        platform: str | None = None,
        date_from: date_type | None = None,
        date_to: date_type | None = None,
    ) -> list[PlatformStockMovement]:
        """Unpaginated read for merging into the unified movement-history
        view (`PlatformInventoryService.get_movement_history`) — the
        caller paginates the already-merged (platform + Shopify) list in
        Python, since the two source ledgers can't share one SQL query.
        """
        stmt = self.search_query(
            product_variant_id=product_variant_id,
            platform=platform,
            date_from=date_from,
            date_to=date_to,
        ).order_by(PlatformStockMovement.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class ProductMarketplaceMovementRepository(AppendOnlyRepository[ProductMarketplaceMovement]):
    """Repository for `ProductMarketplaceMovement` -- the manual
    marketplace ledger, scoped by `(product_id, catalog_variant_id,
    platform)`. `catalog_variant_id=None` means the product-scoped
    balance (a product with fewer than two CatalogVariants); a value is
    that OMS-visible variant's own balance. Rows are append-only: an
    Edit/Undo adds reversal/replacement rows, it never updates a row.
    """

    model = ProductMarketplaceMovement

    @staticmethod
    def _scope(catalog_variant_id: uuid.UUID | None):
        if catalog_variant_id is None:
            return ProductMarketplaceMovement.catalog_variant_id.is_(None)
        return ProductMarketplaceMovement.catalog_variant_id == catalog_variant_id

    async def get_by_id_with_relations(self, id_: uuid.UUID) -> ProductMarketplaceMovement | None:
        stmt = (
            select(ProductMarketplaceMovement)
            .where(ProductMarketplaceMovement.id == id_)
            .options(selectinload(ProductMarketplaceMovement.actor))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_latest_as_of(
        self,
        *,
        product_id: uuid.UUID,
        platform: str,
        as_of: date_type,
        catalog_variant_id: uuid.UUID | None = None,
    ) -> ProductMarketplaceMovement | None:
        """The running balance for one (product, scope, platform) as of
        the end of `as_of` -- same "latest row at/before cutoff"
        technique as `PlatformStockMovementRepository.get_latest_as_of`.
        """
        stmt = (
            select(ProductMarketplaceMovement)
            .where(
                ProductMarketplaceMovement.product_id == product_id,
                self._scope(catalog_variant_id),
                ProductMarketplaceMovement.platform == platform,
                ProductMarketplaceMovement.stock_date <= as_of,
            )
            .order_by(
                ProductMarketplaceMovement.stock_date.desc(),
                ProductMarketplaceMovement.created_at.desc(),
                ProductMarketplaceMovement.id.desc(),
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def sum_for_date(
        self,
        *,
        product_id: uuid.UUID,
        platform: str,
        stock_date: date_type,
        catalog_variant_id: uuid.UUID | None = None,
    ) -> tuple[int, int]:
        """`(stock_added, stock_deducted)` in OUTERS, both positive
        integers, for movements attributed to exactly `stock_date`,
        bucketed by the sign of `quantity_delta`: a positive row (RTO, or
        the reversal of a Sale) counts as added, a negative row (Sale, or
        the reversal of an RTO) as deducted. The movement_type itself is
        preserved on each row for history.
        """
        stmt = select(ProductMarketplaceMovement.quantity_delta).where(
            ProductMarketplaceMovement.product_id == product_id,
            self._scope(catalog_variant_id),
            ProductMarketplaceMovement.platform == platform,
            ProductMarketplaceMovement.stock_date == stock_date,
        )
        result = await self.session.execute(stmt)
        added = 0
        deducted = 0
        for (delta,) in result.all():
            if delta >= 0:
                added += delta
            else:
                deducted += -delta
        return added, deducted

    async def sum_sold_packets(
        self,
        *,
        product_id: uuid.UUID,
        date_from: date_type,
        date_to_exclusive: date_type,
        catalog_variant_id: uuid.UUID | None = None,
    ) -> int:
        """Packets sold across EVERY manual platform within one scope
        (`date_from <= stock_date < date_to_exclusive`) -- SALE rows only,
        summed from the exact packet quantity the user entered. A Sale
        that has since been reversed (Undo, or the superseded half of an
        Edit) is EXCLUDED, so the figure is the effective one; its
        replacement (if any) counts under its own date. An RTO or a
        reversal row is never counted as sold.
        """
        reversal = aliased(ProductMarketplaceMovement)
        already_reversed = (
            select(reversal.id).where(
                reversal.reverses_movement_id == ProductMarketplaceMovement.id
            )
        ).exists()
        total = await self.session.scalar(
            select(func.coalesce(func.sum(ProductMarketplaceMovement.quantity_packets), 0)).where(
                ProductMarketplaceMovement.product_id == product_id,
                self._scope(catalog_variant_id),
                ProductMarketplaceMovement.movement_type == ProductMarketplaceMovementType.SALE,
                ProductMarketplaceMovement.stock_date >= date_from,
                ProductMarketplaceMovement.stock_date < date_to_exclusive,
                ~already_reversed,
            )
        )
        return int(total or 0)

    def search_query(
        self,
        *,
        product_id: uuid.UUID,
        platform: str | None = None,
        date_from: date_type | None = None,
        date_to: date_type | None = None,
        catalog_variant_id: uuid.UUID | None = None,
    ):
        """`catalog_variant_id` is an optional FILTER here (None = every
        scope of the product), unlike the balance lookups above where
        None means the product-scoped balance.
        """
        stmt = (
            self._base_query()
            .where(ProductMarketplaceMovement.product_id == product_id)
            .options(selectinload(ProductMarketplaceMovement.actor))
        )
        if catalog_variant_id is not None:
            stmt = stmt.where(ProductMarketplaceMovement.catalog_variant_id == catalog_variant_id)
        if platform:
            stmt = stmt.where(ProductMarketplaceMovement.platform == platform)
        if date_from:
            stmt = stmt.where(ProductMarketplaceMovement.stock_date >= date_from)
        if date_to:
            stmt = stmt.where(ProductMarketplaceMovement.stock_date <= date_to)
        return stmt

    async def reversed_ids(self, movement_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        """Which of `movement_ids` already have a reversal row."""
        if not movement_ids:
            return set()
        result = await self.session.execute(
            select(ProductMarketplaceMovement.reverses_movement_id).where(
                ProductMarketplaceMovement.reverses_movement_id.in_(movement_ids)
            )
        )
        return {row[0] for row in result.all()}

    async def replaced_ids(self, movement_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        """Which of `movement_ids` have a replacement row (i.e. were Edited)."""
        if not movement_ids:
            return set()
        result = await self.session.execute(
            select(ProductMarketplaceMovement.replaces_movement_id).where(
                ProductMarketplaceMovement.replaces_movement_id.in_(movement_ids)
            )
        )
        return {row[0] for row in result.all()}

    async def packets_by_id(self, movement_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        """`{id: quantity_packets}` -- lets a replacement row show
        "edited from N packets" without loading the original in full.
        """
        if not movement_ids:
            return {}
        result = await self.session.execute(
            select(
                ProductMarketplaceMovement.id, ProductMarketplaceMovement.quantity_packets
            ).where(ProductMarketplaceMovement.id.in_(movement_ids))
        )
        return {row[0]: row[1] for row in result.all()}
