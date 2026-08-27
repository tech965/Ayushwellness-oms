from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.models.product import Product
from app.repositories.product import ProductRepository, ProductVariantRepository
from app.schemas.common import PageParams, SortParams

logger = get_logger(__name__)


class ProductService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.products = ProductRepository(session)
        self.variants = ProductVariantRepository(session)

    async def list_products(
        self,
        *,
        page_params: PageParams,
        sort_params: SortParams,
        q: str | None = None,
        status: str | None = None,
    ) -> tuple[list[Product], int]:
        query = self.products.search_query(q=q, status=status)
        items, total = await self.products.list(
            page_params=page_params, sort_params=sort_params, query=query
        )
        return list(items), total

    async def get_product(self, product_id: uuid.UUID) -> Product:
        product = await self.products.get_by_id_with_variants(product_id)
        if product is None:
            raise NotFoundError("Product not found.")
        return product

    async def create_product(self, **fields) -> Product:  # noqa: ANN003
        product = await self.products.create(**fields, source_system="manual")
        await self.session.commit()
        return product

    async def update_product(self, product_id: uuid.UUID, **fields) -> Product:  # noqa: ANN003
        product = await self.get_product(product_id)
        clean = {k: v for k, v in fields.items() if v is not None}
        if clean:
            await self.products.update(product, **clean)
        await self.session.commit()
        return product

    async def upsert_synced_product(self, **data) -> tuple[Product, bool]:  # noqa: ANN003
        """Idempotent create-or-update from a sync adapter's normalized
        product dict, including its variants (each upserted by its own
        `source_system`/`external_id`, keyed within this product).
        """
        variants = data.pop("variants", [])
        source_system = data.pop("source_system")
        external_id = data.pop("external_id")

        product, created = await self.products.upsert_by_external_id(
            source_system=source_system, external_id=external_id, **data
        )

        for variant in variants:
            variant = dict(variant)
            variant.pop("source_system", None)
            variant_external_id = variant.pop("external_id")
            variant["sku"] = await self._safe_sku(
                sku=variant.get("sku"),
                source_system=source_system,
                external_id=variant_external_id,
            )
            await self.variants.upsert_by_external_id(
                source_system=source_system,
                external_id=variant_external_id,
                product_id=product.id,
                **variant,
            )

        await self.session.commit()
        return product, created

    async def _safe_sku(
        self, *, sku: str | None, source_system: str, external_id: str
    ) -> str | None:
        """Round 4 fix: `product_variants.sku` is globally unique, but a
        merchant can (and, on the real store this was found against,
        does) reuse the same SKU across two genuinely different Shopify
        variants/products. Upserting the second one used to raise
        `UniqueViolationError` straight out of the sync/webhook loop,
        which correctly didn't crash the whole job (see
        `SyncService._run_entity_sync`) but did silently drop that one
        variant every single sync, forever, with no way to fix it short
        of a manual DB edit.

        Business rule: first-claimed keeps the real SKU; a later variant
        that collides gets a namespaced fallback so it's still created
        and searchable, instead of being discarded. The true SKU always
        survives in `raw_external_payload` for reconciliation.
        """
        if sku is None:
            return None
        existing = await self.variants.get_by_sku(sku)
        if existing is None or (existing.source_system, existing.external_id) == (
            source_system,
            external_id,
        ):
            return sku

        fallback = f"{sku}-shopify-{external_id}"
        logger.warning(
            "duplicate_shopify_sku_remapped",
            sku=sku,
            fallback_sku=fallback,
            conflicting_variant_id=str(existing.id),
            shopify_variant_external_id=external_id,
        )
        return fallback
