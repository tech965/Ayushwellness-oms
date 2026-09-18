"""Repository for `PlatformStockMovement` — the manual, date-wise
marketplace stock ledger. Mirrors `InventoryMovementRepository`'s shape
(`app.repositories.inventory`) wherever the two ledgers' concerns line
up, but adds the platform/date-bucketed reads that ledger has no reason
to support.
"""

from __future__ import annotations

import uuid
from datetime import date as date_type

from sqlalchemy import select
from sqlalchemy.orm import selectinload

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
    """Repository for `ProductMarketplaceMovement` -- the product-level
    (no SKU) manual marketplace ledger. Mirrors
    `PlatformStockMovementRepository`'s shape exactly, scoped by
    `product_id` instead of `product_variant_id`.
    """

    model = ProductMarketplaceMovement

    async def get_by_id_with_relations(self, id_: uuid.UUID) -> ProductMarketplaceMovement | None:
        stmt = (
            select(ProductMarketplaceMovement)
            .where(ProductMarketplaceMovement.id == id_)
            .options(selectinload(ProductMarketplaceMovement.actor))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_latest_as_of(
        self, *, product_id: uuid.UUID, platform: str, as_of: date_type
    ) -> ProductMarketplaceMovement | None:
        """The running balance for one (product, platform) as of the end
        of `as_of` -- same "latest row at/before cutoff" technique as
        `PlatformStockMovementRepository.get_latest_as_of`.
        """
        stmt = (
            select(ProductMarketplaceMovement)
            .where(
                ProductMarketplaceMovement.product_id == product_id,
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
        self, *, product_id: uuid.UUID, platform: str, stock_date: date_type
    ) -> tuple[int, int]:
        """`(stock_added, stock_deducted)` in OUTERS, both positive
        integers, for movements attributed to exactly `stock_date`.
        `stock_added` covers both STOCK_ADDED and RTO (both positive
        deltas); `stock_deducted` covers SALE. The movement_type itself
        is preserved on each row for history -- this is only the
        summary table's two-column bucketing, same convention as
        `PlatformStockMovementRepository.sum_for_date`.
        """
        stmt = select(
            ProductMarketplaceMovement.movement_type, ProductMarketplaceMovement.quantity_delta
        ).where(
            ProductMarketplaceMovement.product_id == product_id,
            ProductMarketplaceMovement.platform == platform,
            ProductMarketplaceMovement.stock_date == stock_date,
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

    def search_query(
        self,
        *,
        product_id: uuid.UUID,
        platform: str | None = None,
        date_from: date_type | None = None,
        date_to: date_type | None = None,
    ):
        stmt = (
            self._base_query()
            .where(ProductMarketplaceMovement.product_id == product_id)
            .options(selectinload(ProductMarketplaceMovement.actor))
        )
        if platform:
            stmt = stmt.where(ProductMarketplaceMovement.platform == platform)
        if date_from:
            stmt = stmt.where(ProductMarketplaceMovement.stock_date >= date_from)
        if date_to:
            stmt = stmt.where(ProductMarketplaceMovement.stock_date <= date_to)
        return stmt

    async def list_for_date_range(
        self,
        *,
        product_id: uuid.UUID,
        platform: str | None = None,
        date_from: date_type | None = None,
        date_to: date_type | None = None,
    ) -> list[ProductMarketplaceMovement]:
        stmt = self.search_query(
            product_id=product_id, platform=platform, date_from=date_from, date_to=date_to
        ).order_by(ProductMarketplaceMovement.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
