from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased, selectinload

from app.models.enums import InventoryMovementType, ProductStatus, RTORestockStatus, RTOStatus
from app.models.inventory import InventoryMovement
from app.models.product import (
    CatalogVariant,
    CatalogVariantStockAdjustment,
    Product,
    ProductVariant,
)
from app.models.rto import RTO
from app.repositories.base import AppendOnlyRepository, BaseRepository


class InventoryMovementRepository(AppendOnlyRepository[InventoryMovement]):
    model = InventoryMovement

    async def create(self, **fields) -> InventoryMovement:  # noqa: ANN003
        """Every ledger row carries a warehouse; default to the default one."""
        if fields.get("warehouse_id") is None:
            from app.services.inventory_reservation_service import default_warehouse_id

            fields["warehouse_id"] = await default_warehouse_id(self.session)
        return await super().create(**fields)

    async def get_by_id_with_relations(self, id_: uuid.UUID) -> InventoryMovement | None:
        stmt = (
            select(InventoryMovement)
            .where(InventoryMovement.id == id_)
            .options(
                selectinload(InventoryMovement.product_variant).selectinload(
                    ProductVariant.product
                ),
                selectinload(InventoryMovement.actor),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_for_order(
        self,
        *,
        order_id: uuid.UUID,
        product_variant_id: uuid.UUID,
        movement_type: InventoryMovementType,
    ) -> InventoryMovement | None:
        """The single row (if any) already recorded for this exact
        (order, variant, movement type) -- used both as the idempotency
        check (`exists_for_order`, below) and, by `InventoryService.
        apply_rto_restock`, to read back a prior `DISPATCH` movement's
        exact box quantity rather than recomputing it (`packets_per_box`
        can change between dispatch and RTO; the restock must not).

        This snapshot-time SELECT is NOT by itself sufficient to prevent
        two concurrent transactions from both proceeding -- see the
        `InventoryService` module docstring for why the DB-level
        `UniqueConstraint` on `inventory_movements` (product_variant_id,
        order_id, movement_type) is the actual safety net, and why a
        `session.begin_nested()` SAVEPOINT around the write is required
        to handle losing that race safely.

        Ordered + `.limit(1)` rather than `scalar_one_or_none()`: on a
        database where the unique constraint above has not been applied
        yet (or a pre-existing row predates it), more than one row can
        exist for the same key -- `scalar_one_or_none()` raises
        `MultipleResultsFound` in that case, turning a read into an
        unhandled 500. Deterministically returning the earliest row
        (oldest `created_at`, tie-broken by `id`) matches the canonical
        row the `c9f4a2e6b813` migration's cleanup keeps.
        """
        stmt = (
            select(InventoryMovement)
            .where(
                InventoryMovement.order_id == order_id,
                InventoryMovement.product_variant_id == product_variant_id,
                InventoryMovement.movement_type == movement_type,
            )
            .order_by(InventoryMovement.created_at.asc(), InventoryMovement.id.asc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def exists_for_order(
        self,
        *,
        order_id: uuid.UUID,
        product_variant_id: uuid.UUID,
        movement_type: InventoryMovementType,
    ) -> bool:
        movement = await self.get_for_order(
            order_id=order_id, product_variant_id=product_variant_id, movement_type=movement_type
        )
        return movement is not None

    async def get_for_rto(
        self, *, rto_id: uuid.UUID, product_variant_id: uuid.UUID
    ) -> InventoryMovement | None:
        """The RTO_RESTOCK row (if any) already recorded for this exact
        (rto, variant) -- the receipt-level idempotency check mirroring
        `get_for_order`, but keyed by `rto_id` rather than `order_id`
        (see `app.models.inventory.InventoryMovement`'s module docstring:
        one RTO row is one courier receipt, and a single order can have
        more than one). Same ordered + `.limit(1)` defensiveness as
        `get_for_order`.
        """
        stmt = (
            select(InventoryMovement)
            .where(
                InventoryMovement.rto_id == rto_id,
                InventoryMovement.product_variant_id == product_variant_id,
                InventoryMovement.movement_type == InventoryMovementType.RTO_RESTOCK,
            )
            .order_by(InventoryMovement.created_at.asc(), InventoryMovement.id.asc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def exists_for_rto(self, *, rto_id: uuid.UUID, product_variant_id: uuid.UUID) -> bool:
        return (
            await self.get_for_rto(rto_id=rto_id, product_variant_id=product_variant_id)
        ) is not None

    async def restocked_boxes_for_order_variant(
        self, *, order_id: uuid.UUID, product_variant_id: uuid.UUID
    ) -> int:
        """SUM of every RTO_RESTOCK already recorded for this (order,
        variant), across EVERY RTO/receipt -- the "already returned"
        baseline `InventoryService.apply_rto_restock`/`RTOService.
        verify_warehouse_receipt` subtract from the single `DISPATCH`
        row's box quantity to get what a new receipt may still accept.
        """
        total = await self.session.scalar(
            select(func.coalesce(func.sum(InventoryMovement.quantity_delta), 0)).where(
                InventoryMovement.order_id == order_id,
                InventoryMovement.product_variant_id == product_variant_id,
                InventoryMovement.movement_type == InventoryMovementType.RTO_RESTOCK,
            )
        )
        return int(total or 0)

    def search_query(
        self,
        *,
        product_variant_id: uuid.UUID | None = None,
        product_id: uuid.UUID | None = None,
        catalog_variant_id: uuid.UUID | None = None,
        order_id: uuid.UUID | None = None,
        movement_type: str | None = None,
        sku: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ):
        stmt = self._base_query().options(
            selectinload(InventoryMovement.product_variant).selectinload(ProductVariant.product),
            selectinload(InventoryMovement.actor),
        )
        # `product_id`/`catalog_variant_id`/`sku` all filter via
        # ProductVariant; join it once so passing any combination of them
        # never double-joins.
        if product_id is not None or catalog_variant_id is not None or sku is not None:
            stmt = stmt.join(
                ProductVariant, ProductVariant.id == InventoryMovement.product_variant_id
            )
            if product_id is not None:
                stmt = stmt.where(ProductVariant.product_id == product_id)
            if catalog_variant_id is not None:
                # OMS-visible variant history spans EVERY underlying Shopify
                # ProductVariant grouped under it -- no ledger row is rewritten.
                stmt = stmt.where(ProductVariant.catalog_variant_id == catalog_variant_id)
            if sku:
                # Module 1.1's catalogue-wide "SKU-wise movement history"
                # search -- partial, case-insensitive, same convention as
                # every other free-text search in this codebase.
                stmt = stmt.where(ProductVariant.sku.ilike(f"%{sku}%"))
        if product_variant_id:
            stmt = stmt.where(InventoryMovement.product_variant_id == product_variant_id)
        if order_id:
            stmt = stmt.where(InventoryMovement.order_id == order_id)
        if movement_type:
            stmt = stmt.where(InventoryMovement.movement_type == movement_type)
        if date_from:
            stmt = stmt.where(InventoryMovement.created_at >= date_from)
        if date_to:
            stmt = stmt.where(InventoryMovement.created_at <= date_to)
        return stmt

    async def movement_totals_by_sign_bulk(
        self,
        *,
        product_variant_ids: list[uuid.UUID],
        created_from: datetime,
        created_to: datetime,
    ) -> dict[uuid.UUID, tuple[int, int]]:
        """`{variant_id: (added_boxes, deducted_boxes)}` for every variant
        in `product_variant_ids` within one UTC timestamp window
        (typically one IST calendar day's bounds) -- one query, no N+1
        across a product's several SKUs. Backs the multi-platform
        inventory feature's "Shopify: stock added/deducted on the
        selected date" row, reusing this EXISTING ledger (never a
        duplicate/estimated figure -- see `app.services.
        platform_inventory_service`).

        Buckets by the sign of `quantity_delta`, across EVERY
        `InventoryMovementType` (DISPATCH, RTO_RESTOCK, MANUAL_ADJUSTMENT,
        INITIAL_STOCK) -- not just DISPATCH/RTO_RESTOCK. "Stock Added"/
        "Sold or Deducted" means every positive/negative Shopify movement
        on that date, including a staff manual adjustment made through
        the existing (non-platform) Add Stock action -- narrowing to two
        hardcoded movement types would silently under-report that case.
        """
        if not product_variant_ids:
            return {}
        stmt = (
            select(
                InventoryMovement.product_variant_id,
                func.sum(InventoryMovement.quantity_delta),
            )
            .where(
                InventoryMovement.product_variant_id.in_(product_variant_ids),
                InventoryMovement.quantity_delta > 0,
                InventoryMovement.created_at >= created_from,
                InventoryMovement.created_at < created_to,
            )
            .group_by(InventoryMovement.product_variant_id)
        )
        added_result = await self.session.execute(stmt)
        added: dict[uuid.UUID, int] = {
            variant_id: int(total or 0) for variant_id, total in added_result.all()
        }

        stmt = (
            select(
                InventoryMovement.product_variant_id,
                func.sum(InventoryMovement.quantity_delta),
            )
            .where(
                InventoryMovement.product_variant_id.in_(product_variant_ids),
                InventoryMovement.quantity_delta < 0,
                InventoryMovement.created_at >= created_from,
                InventoryMovement.created_at < created_to,
            )
            .group_by(InventoryMovement.product_variant_id)
        )
        deducted_result = await self.session.execute(stmt)
        deducted: dict[uuid.UUID, int] = {
            variant_id: -int(total or 0) for variant_id, total in deducted_result.all()
        }

        return {
            variant_id: (added.get(variant_id, 0), deducted.get(variant_id, 0))
            for variant_id in set(added) | set(deducted)
        }

    async def get_latest_as_of_bulk(
        self, *, product_variant_ids: list[uuid.UUID], created_to: datetime
    ) -> dict[uuid.UUID, InventoryMovement]:
        """The latest `InventoryMovement` row (by `created_at`) strictly
        before `created_to`, for every variant in `product_variant_ids` --
        one query, no N+1. `quantity_after` on the returned row IS the
        variant's Shopify stock balance at that instant (each movement
        already records the resulting running balance, the same
        mechanism `PlatformStockMovementRepository.get_latest_as_of`
        already uses for the manual marketplace ledger) -- this
        reconstructs a HISTORICAL Shopify balance from the existing
        ledger, never `ProductVariant.available_quantity` (which is only
        ever today's LIVE value, see `app.services.
        platform_inventory_service`).

        A variant absent from the returned dict means no movement exists
        before `created_to` -- the ledger genuinely cannot reconstruct a
        balance for that cutoff (it may predate the variant's first-ever
        movement); the caller must treat this as "unknown", never guess 0.
        """
        if not product_variant_ids:
            return {}
        stmt = (
            select(InventoryMovement)
            .where(
                InventoryMovement.product_variant_id.in_(product_variant_ids),
                InventoryMovement.created_at < created_to,
            )
            .order_by(InventoryMovement.product_variant_id, InventoryMovement.created_at.desc())
        )
        result = await self.session.execute(stmt)
        latest: dict[uuid.UUID, InventoryMovement] = {}
        for movement in result.scalars().all():
            if movement.product_variant_id not in latest:
                latest[movement.product_variant_id] = movement
        return latest

    async def last_movement_at_bulk(
        self, *, product_variant_ids: list[uuid.UUID], created_to: datetime
    ) -> dict[uuid.UUID, datetime]:
        """`{variant_id: created_at}` of the latest movement at or before
        `created_to`, for every variant in `product_variant_ids` -- the
        "Last Updated" column for the Shopify row of the platform-stock
        summary table.
        """
        if not product_variant_ids:
            return {}
        stmt = (
            select(InventoryMovement.product_variant_id, InventoryMovement.created_at)
            .where(
                InventoryMovement.product_variant_id.in_(product_variant_ids),
                InventoryMovement.created_at < created_to,
            )
            .order_by(InventoryMovement.product_variant_id, InventoryMovement.created_at.desc())
        )
        result = await self.session.execute(stmt)
        latest: dict[uuid.UUID, datetime] = {}
        for variant_id, created_at in result.all():
            if variant_id not in latest:
                latest[variant_id] = created_at
        return latest

    async def all_time_delta_total(self) -> int:
        """SUM of every `quantity_delta` ever recorded, across every
        variant -- the one number `InventoryProductRepository.
        dashboard_stock_totals`'s opening-stock derivation needs
        (opening = today's total available boxes - this). Excludes
        variants of DRAFT products, matching that method's own filter,
        so the two halves of the subtraction cover the same population.
        """
        total = await self.session.scalar(
            select(func.coalesce(func.sum(InventoryMovement.quantity_delta), 0))
            .select_from(InventoryMovement)
            .join(ProductVariant, ProductVariant.id == InventoryMovement.product_variant_id)
            .join(Product, Product.id == ProductVariant.product_id)
            .where(Product.status != ProductStatus.DRAFT)
        )
        return int(total or 0)

    async def totals_by_sign_in_range(
        self, *, date_from: datetime | None, date_to: datetime | None
    ) -> tuple[int, int]:
        """`(total_inward_boxes, total_outward_boxes)` across every variant
        within `[date_from, date_to]` (either bound optional -- all-time
        when both are None). Inward/outward are the sign of
        `quantity_delta`, across every movement type -- a manual "Add
        Stock" counts as inward exactly like an RTO restock does; a
        manual downward correction counts as outward exactly like a
        dispatch does. Mirrors `movement_totals_by_sign_bulk`'s own
        sign-bucketing, just summed across the whole catalogue instead of
        per-variant.
        """
        conditions = [Product.status != ProductStatus.DRAFT]
        if date_from is not None:
            conditions.append(InventoryMovement.created_at >= date_from)
        if date_to is not None:
            conditions.append(InventoryMovement.created_at <= date_to)

        base = (
            select(func.coalesce(func.sum(InventoryMovement.quantity_delta), 0))
            .select_from(InventoryMovement)
            .join(ProductVariant, ProductVariant.id == InventoryMovement.product_variant_id)
            .join(Product, Product.id == ProductVariant.product_id)
        )
        inward = await self.session.scalar(
            base.where(*conditions, InventoryMovement.quantity_delta > 0)
        )
        outward = await self.session.scalar(
            base.where(*conditions, InventoryMovement.quantity_delta < 0)
        )
        return int(inward or 0), int(outward or 0)

    async def rto_in_transit_boxes_and_count(self) -> tuple[int, int]:
        """`(boxes, shipment_count)` physically on their way back to the
        warehouse right now: a `DISPATCH` movement whose order has an RTO
        row still `INITIATED`/`IN_TRANSIT` (confirmed-received RTOs are
        excluded by that status filter alone -- restock only ever fires
        on `RTOStatus.RECEIVED`, see `InventoryService.apply_rto_restock`).

        The `NOT EXISTS` guard additionally excludes any (order, variant)
        pair that already has an `RTO_RESTOCK` movement -- a defensive
        belt-and-braces check (not merely redundant with the status
        filter): if a restock has somehow already landed for a pair
        whose RTO row hasn't yet been marked RECEIVED, that stock is
        already back in `available_quantity`, and counting it here too
        would double-count it. Same `NOT EXISTS`-correlated-subquery
        idiom already used elsewhere in this codebase (see
        `PlatformInventoryService.sum_sold_packets`'s reversal exclusion)
        rather than a tuple-IN, which SQLite cannot execute.
        """
        in_transit_order_ids = select(RTO.order_id).where(
            RTO.status.in_([RTOStatus.INITIATED, RTOStatus.IN_TRANSIT])
        )
        restock = aliased(InventoryMovement)
        already_restocked = (
            select(restock.id)
            .where(
                restock.order_id == InventoryMovement.order_id,
                restock.product_variant_id == InventoryMovement.product_variant_id,
                restock.movement_type == InventoryMovementType.RTO_RESTOCK,
            )
            .exists()
        )
        stmt = select(
            func.count(InventoryMovement.id),
            func.coalesce(func.sum(-InventoryMovement.quantity_delta), 0),
        ).where(
            InventoryMovement.movement_type == InventoryMovementType.DISPATCH,
            InventoryMovement.order_id.in_(in_transit_order_ids),
            ~already_restocked,
        )
        result = await self.session.execute(stmt)
        count, boxes = result.one()
        return int(boxes or 0), int(count or 0)

    async def rto_awaiting_verification_boxes_and_count(self) -> tuple[int, int]:
        """`(boxes, shipment_count)` the courier reports delivered back to
        the warehouse but NOT yet confirmed received by warehouse staff
        (`RTO.restock_status == AWAITING_VERIFICATION`) -- deliberately
        separate from `rto_in_transit_boxes_and_count` (courier status
        still `INITIATED`/`IN_TRANSIT`): a package can be "awaiting
        verification" only once the courier has already marked it
        delivered, so the two sets never overlap and must never be added
        together into one "in transit" figure (Module 1 requirement:
        awaiting-verification stock is neither physical nor in transit).

        The `NOT EXISTS` guard is keyed by `rto_id` (not `order_id`,
        unlike `rto_in_transit_boxes_and_count`'s own guard) -- a
        receipt-level check matching `InventoryMovementRepository.
        get_for_rto`'s own idempotency key, so a second, still-pending
        RTO on the same order/variant is never excluded just because a
        DIFFERENT RTO on that order already restocked its own receipt.
        """
        awaiting_rtos = select(RTO.id, RTO.order_id).where(
            RTO.restock_status == RTORestockStatus.AWAITING_VERIFICATION
        ).subquery()
        restock = aliased(InventoryMovement)
        already_restocked = (
            select(restock.id)
            .where(
                restock.rto_id == awaiting_rtos.c.id,
                restock.product_variant_id == InventoryMovement.product_variant_id,
                restock.movement_type == InventoryMovementType.RTO_RESTOCK,
            )
            .exists()
        )
        stmt = (
            select(
                func.count(InventoryMovement.id),
                func.coalesce(func.sum(-InventoryMovement.quantity_delta), 0),
            )
            .select_from(InventoryMovement)
            .join(awaiting_rtos, awaiting_rtos.c.order_id == InventoryMovement.order_id)
            .where(
                InventoryMovement.movement_type == InventoryMovementType.DISPATCH,
                ~already_restocked,
            )
        )
        result = await self.session.execute(stmt)
        count, boxes = result.one()
        return int(boxes or 0), int(count or 0)


class InventoryStockRepository(BaseRepository[ProductVariant]):
    """Read-side view over a single `ProductVariant` -- intentionally not
    `ProductVariantRepository` (`app.repositories.product`), which owns
    variant CRUD/sync; this repository only ever reads.
    """

    model = ProductVariant

    async def dashboard_totals(self) -> dict[str, int]:
        """`{total_products, total_skus, total_available_boxes,
        total_available_packets}` across every non-DRAFT product's
        variants -- one aggregate query, database-side (never loads every
        variant into Python the way the product-list page's own
        `total_packets` computation does; the formula is identical:
        `available_quantity * packets_per_box`, confirmed against
        `app.api.v1.endpoints.inventory`'s existing per-product
        computation -- never `pack_size`, which is a different factor).
        """
        stmt = (
            select(
                func.count(func.distinct(Product.id)),
                func.count(ProductVariant.id),
                func.coalesce(func.sum(ProductVariant.available_quantity), 0),
                func.coalesce(
                    func.sum(ProductVariant.available_quantity * ProductVariant.packets_per_box), 0
                ),
            )
            .select_from(ProductVariant)
            .join(Product, Product.id == ProductVariant.product_id)
            .where(Product.status != ProductStatus.DRAFT)
        )
        result = await self.session.execute(stmt)
        total_products, total_skus, total_boxes, total_packets = result.one()
        return {
            "total_products": int(total_products or 0),
            "total_skus": int(total_skus or 0),
            "total_available_boxes": int(total_boxes or 0),
            "total_available_packets": int(total_packets or 0),
        }

    async def low_and_out_of_stock_counts(self, *, low_stock_threshold: int) -> tuple[int, int]:
        """`(low_stock_count, out_of_stock_count)` across every non-DRAFT
        product's variants, using the same threshold
        `InventoryService.get_low_stock_threshold`/`compute_stock_status`
        already applies per-variant elsewhere -- never a second,
        separately-reasoned threshold.
        """
        base = (
            select(func.count(ProductVariant.id))
            .select_from(ProductVariant)
            .join(Product, Product.id == ProductVariant.product_id)
            .where(Product.status != ProductStatus.DRAFT)
        )
        out_of_stock = await self.session.scalar(
            base.where(ProductVariant.available_quantity <= 0)
        )
        low_stock = await self.session.scalar(
            base.where(
                ProductVariant.available_quantity > 0,
                ProductVariant.available_quantity <= low_stock_threshold,
            )
        )
        return int(low_stock or 0), int(out_of_stock or 0)

    async def get_by_id_with_product(self, id_: uuid.UUID) -> ProductVariant | None:
        stmt = (
            select(ProductVariant)
            .where(ProductVariant.id == id_)
            .options(selectinload(ProductVariant.product))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


class CatalogVariantStockAdjustmentRepository(AppendOnlyRepository[CatalogVariantStockAdjustment]):
    """Ledger for a manual "Total Stock" edit made directly against a
    `CatalogVariant` -- see `app.models.product.CatalogVariantStockAdjustment`
    for why this is a separate table from `inventory_movements`.
    """

    model = CatalogVariantStockAdjustment

    async def get_by_id_with_relations(
        self, id_: uuid.UUID
    ) -> CatalogVariantStockAdjustment | None:
        stmt = (
            select(CatalogVariantStockAdjustment)
            .where(CatalogVariantStockAdjustment.id == id_)
            .options(selectinload(CatalogVariantStockAdjustment.actor))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_for_movement(
        self, product_marketplace_movement_id: uuid.UUID
    ) -> CatalogVariantStockAdjustment | None:
        """The OMS-total ledger row a marketplace movement wrote (there is
        at most one -- the column is unique), or None for a movement that
        pre-dates OMS-stock linking and so never had an OMS effect.
        """
        result = await self.session.execute(
            select(CatalogVariantStockAdjustment).where(
                CatalogVariantStockAdjustment.product_marketplace_movement_id
                == product_marketplace_movement_id
            )
        )
        return result.scalar_one_or_none()

    async def sum_for_catalog_variant(self, catalog_variant_id: uuid.UUID) -> int:
        """Cumulative reconciliation offset recorded so far -- 0 with no
        prior adjustments. Summed live, never cached, so it can never
        drift out of sync with the rows that back it.
        """
        total = await self.session.scalar(
            select(func.coalesce(func.sum(CatalogVariantStockAdjustment.quantity_delta), 0)).where(
                CatalogVariantStockAdjustment.catalog_variant_id == catalog_variant_id
            )
        )
        return int(total or 0)

    async def sum_by_product(self, product_id: uuid.UUID) -> dict[uuid.UUID, int]:
        """Every CatalogVariant's cumulative offset for one product, in a
        single query -- used to build the product detail page without an
        N+1 (one query per OMS-visible variant).
        """
        stmt = (
            select(
                CatalogVariantStockAdjustment.catalog_variant_id,
                func.sum(CatalogVariantStockAdjustment.quantity_delta),
            )
            .join(
                CatalogVariant,
                CatalogVariant.id == CatalogVariantStockAdjustment.catalog_variant_id,
            )
            .where(CatalogVariant.product_id == product_id)
            .group_by(CatalogVariantStockAdjustment.catalog_variant_id)
        )
        result = await self.session.execute(stmt)
        return {row[0]: int(row[1] or 0) for row in result.all()}

    async def sum_product_level(self, product_id: uuid.UUID) -> int:
        """Cumulative PRODUCT-scoped rows (marketplace Sale/RTO on a
        product with no single deterministic CatalogVariant). Counts
        toward the product total only, never toward any one CatalogVariant.
        """
        total = await self.session.scalar(
            select(func.coalesce(func.sum(CatalogVariantStockAdjustment.quantity_delta), 0)).where(
                CatalogVariantStockAdjustment.product_id == product_id
            )
        )
        return int(total or 0)

    async def sum_all_for_products(self, product_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        """`{product_id: total ledger delta}` across BOTH scopes
        (CatalogVariant-scoped + product-scoped) for many products in two
        queries -- what the Inventory overview list adds to its per-SKU
        sums so it agrees with the product detail header.
        """
        if not product_ids:
            return {}
        totals: dict[uuid.UUID, int] = {}
        via_cv = await self.session.execute(
            select(
                CatalogVariant.product_id, func.sum(CatalogVariantStockAdjustment.quantity_delta)
            )
            .join(
                CatalogVariant,
                CatalogVariant.id == CatalogVariantStockAdjustment.catalog_variant_id,
            )
            .where(CatalogVariant.product_id.in_(product_ids))
            .group_by(CatalogVariant.product_id)
        )
        for pid, total in via_cv.all():
            totals[pid] = totals.get(pid, 0) + int(total or 0)
        direct = await self.session.execute(
            select(
                CatalogVariantStockAdjustment.product_id,
                func.sum(CatalogVariantStockAdjustment.quantity_delta),
            )
            .where(CatalogVariantStockAdjustment.product_id.in_(product_ids))
            .group_by(CatalogVariantStockAdjustment.product_id)
        )
        for pid, total in direct.all():
            totals[pid] = totals.get(pid, 0) + int(total or 0)
        return totals

    def search_query(self, *, catalog_variant_id: uuid.UUID):
        return (
            self._base_query()
            .where(CatalogVariantStockAdjustment.catalog_variant_id == catalog_variant_id)
            .options(selectinload(CatalogVariantStockAdjustment.actor))
        )


class InventoryProductRepository(BaseRepository[Product]):
    """Product-level read side backing the main Inventory page (Product ->
    Variant -> Inventory hierarchy) -- searches by product title/vendor OR
    any of its variants' SKU, and eager-loads variants so a stock summary
    (variant count, total boxes) can be computed per product with no N+1.

    Draft products are excluded: Shopify sync pulls in unpublished
    duplicates/test products (which often reuse the exact title of a real
    published product) that have never been reviewed or grouped into
    `CatalogVariant`s. Surfacing them here would let staff land on an
    ungrouped duplicate's raw Shopify-variant count instead of the real
    product's OMS-visible one. Archived products remain visible -- they
    can still carry real stock to sell down.
    """

    model = Product

    def search_query(self, *, q: str | None = None):
        stmt = (
            self._base_query()
            .where(Product.status != ProductStatus.DRAFT)
            .options(selectinload(Product.variants), selectinload(Product.catalog_variants))
        )
        if q:
            like = f"%{q}%"
            stmt = stmt.where(
                or_(
                    Product.title.ilike(like),
                    Product.vendor.ilike(like),
                    Product.variants.any(ProductVariant.sku.ilike(like)),
                )
            )
        return stmt
