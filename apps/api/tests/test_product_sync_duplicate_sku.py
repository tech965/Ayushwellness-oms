"""Round 4 — a real, confirmed sync error: two different Shopify
variants sharing one SKU. `product_variants.sku` is globally unique;
upserting the second variant used to raise `UniqueViolationError` and
silently drop that variant forever, every sync. See
`ProductService._safe_sku`'s docstring for the fix and business rule.
"""

from __future__ import annotations

import pytest
from app.repositories.product import ProductVariantRepository
from app.services.product_service import ProductService
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


def _product(external_id: str, variant_external_id: str, sku: str) -> dict:
    return {
        "source_system": "shopify",
        "external_id": external_id,
        "shopify_product_id": external_id,
        "title": f"Product {external_id}",
        "variants": [
            {
                "source_system": "shopify",
                "external_id": variant_external_id,
                "shopify_variant_id": variant_external_id,
                "sku": sku,
                "price": "100.00",
            }
        ],
    }


async def test_a_colliding_sku_is_remapped_not_dropped(db_session: AsyncSession) -> None:
    service = ProductService(db_session)

    # First variant legitimately claims the SKU.
    product_a, _ = await service.upsert_synced_product(**_product("P1", "V1", "AW-HM-PN-60"))
    variant_a = (
        await ProductVariantRepository(db_session).list_for_product(product_a.id)
    )[0]
    assert variant_a.sku == "AW-HM-PN-60"

    # A second, genuinely different Shopify variant reuses the same SKU
    # (the real scenario found live) -- must still be created, not lost.
    product_b, _ = await service.upsert_synced_product(**_product("P2", "V2", "AW-HM-PN-60"))
    variants_b = await ProductVariantRepository(db_session).list_for_product(product_b.id)
    assert len(variants_b) == 1
    variant_b = variants_b[0]

    assert variant_b.sku != "AW-HM-PN-60"  # remapped, not the same row
    assert variant_b.sku.startswith("AW-HM-PN-60-shopify-V2")
    assert variant_a.sku == "AW-HM-PN-60"  # first-claimed keeps the real SKU


async def test_resyncing_the_same_variant_keeps_its_own_sku_unchanged(
    db_session: AsyncSession,
) -> None:
    """A variant colliding with *itself* (a normal resync, sku unchanged)
    must not be treated as a duplicate and remapped.
    """
    service = ProductService(db_session)
    await service.upsert_synced_product(**_product("P1", "V1", "AW-HM-PN-60"))
    product, _ = await service.upsert_synced_product(**_product("P1", "V1", "AW-HM-PN-60"))

    variants = await ProductVariantRepository(db_session).list_for_product(product.id)
    assert len(variants) == 1
    assert variants[0].sku == "AW-HM-PN-60"
