"""`scripts/backfill_pack_sizes.py` -- the one-off, dry-run-by-default
pack_size correction for the canonical Aayush Wellness Herbal Masala
product.

Exercises the script's pure planning/apply logic against real
`Product`/`ProductVariant` rows in the test database -- never touches a
network or a real Shopify store.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.repositories.product import ProductRepository, ProductVariantRepository
from scripts.backfill_pack_sizes import (
    CANONICAL_SHOPIFY_PRODUCT_ID,
    apply_plan,
    build_plan,
    has_blocking_problems,
)
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _make_canonical_product(session: AsyncSession, *, external_id: str = "prod-canonical"):
    product, _ = await ProductRepository(session).upsert_by_external_id(
        source_system="shopify",
        external_id=external_id,
        title="Aayush Wellness Herbal Masala",
        shopify_product_id=CANONICAL_SHOPIFY_PRODUCT_ID,
    )
    await session.commit()
    return product


async def _make_variant(
    session: AsyncSession,
    *,
    product_id,
    sku: str,
    title: str | None,
    pack_size: int = 1,
    external_id: str | None = None,
):
    variant, _ = await ProductVariantRepository(session).upsert_by_external_id(
        source_system="shopify",
        external_id=external_id or f"var-{sku}",
        product_id=product_id,
        sku=sku,
        title=title,
        price=Decimal("100.00"),
        pack_size=pack_size,
    )
    await session.commit()
    return variant


async def test_build_plan_parses_pouch_count_from_a_clean_sku(db_session: AsyncSession) -> None:
    product = await _make_canonical_product(db_session)
    await _make_variant(
        db_session,
        product_id=product.id,
        sku="AW-HM-PN-60",
        title="Paan Masala Flavour / 60 - Pouches",
    )

    _, plan = await build_plan(db_session)

    assert len(plan) == 1
    assert plan[0].pouch_count == 60
    assert plan[0].parsed_from == "sku"
    assert plan[0].target_pack_size == 1


async def test_build_plan_parses_pouch_count_from_a_sku_with_shopify_fallback_suffix(
    db_session: AsyncSession,
) -> None:
    product = await _make_canonical_product(db_session)
    await _make_variant(
        db_session,
        product_id=product.id,
        sku="AW-HM-PN-120-shopify-45082735280317",
        title="Paan Masala Flavour / 120 - Pouches",
    )

    _, plan = await build_plan(db_session)

    assert plan[0].pouch_count == 120
    assert plan[0].parsed_from == "sku"
    assert plan[0].target_pack_size == 2


async def test_build_plan_falls_back_to_title_when_sku_has_no_embedded_number(
    db_session: AsyncSession,
) -> None:
    """A synthetic `shopify-<id>` SKU (no embedded pouch count) still
    carries the real pouch count in its title -- exactly the real shape
    found for some 180-pack rows in this engagement's own database.
    """
    product = await _make_canonical_product(db_session)
    await _make_variant(
        db_session,
        product_id=product.id,
        sku="shopify-47993095618749",
        title="Paan Masala Flavour / 180 - Pouches",
    )

    _, plan = await build_plan(db_session)

    assert plan[0].pouch_count == 180
    assert plan[0].parsed_from == "title"
    assert plan[0].target_pack_size == 3


async def test_build_plan_blocks_on_an_unparseable_variant(db_session: AsyncSession) -> None:
    product = await _make_canonical_product(db_session)
    await _make_variant(
        db_session,
        product_id=product.id,
        sku="totally-unrelated-sku",
        title=None,
    )

    plan_product, plan = await build_plan(db_session)

    assert plan[0].target_pack_size is None
    assert has_blocking_problems(plan_product, plan) is True


async def test_build_plan_blocks_on_a_pouch_count_with_no_approved_mapping(
    db_session: AsyncSession,
) -> None:
    """A real, well-formed pouch count that simply isn't one of the
    approved 60/120/180 sizes -- never guessed, always blocks.
    """
    product = await _make_canonical_product(db_session)
    await _make_variant(
        db_session,
        product_id=product.id,
        sku="AW-HM-PN-999",
        title="Paan Masala Flavour / 999 - Pouches",
    )

    plan_product, plan = await build_plan(db_session)

    assert plan[0].pouch_count == 999
    assert plan[0].target_pack_size is None
    assert has_blocking_problems(plan_product, plan) is True


async def test_missing_canonical_product_blocks(db_session: AsyncSession) -> None:
    plan_product, plan = await build_plan(db_session)
    assert plan_product is None
    assert plan == []
    assert has_blocking_problems(plan_product, plan) is True


async def test_apply_plan_sets_1_2_3_for_60_120_180_and_never_touches_an_unrelated_product(
    db_session: AsyncSession,
) -> None:
    product = await _make_canonical_product(db_session)
    v60 = await _make_variant(
        db_session, product_id=product.id, sku="AW-HM-PN-60", title="Paan / 60 - Pouches"
    )
    v120 = await _make_variant(
        db_session, product_id=product.id, sku="AW-HM-PN-120", title="Paan / 120 - Pouches"
    )
    v180 = await _make_variant(
        db_session,
        product_id=product.id,
        sku="shopify-999",
        title="Paan / 180 - Pouches",
    )

    # An unrelated product with its own "120" SKU -- must never be
    # touched by a script scoped to the canonical Shopify product id only.
    other_product, _ = await ProductRepository(db_session).upsert_by_external_id(
        source_system="shopify", external_id="prod-other", title="Some Other Product"
    )
    await db_session.commit()
    other_variant = await _make_variant(
        db_session,
        product_id=other_product.id,
        sku="OTHER-120",
        title="Some Other Product / 120 units",
        external_id="var-other-120",
    )

    plan_product, plan = await build_plan(db_session)
    assert has_blocking_problems(plan_product, plan) is False
    await apply_plan(db_session, plan)

    refreshed_60 = await ProductVariantRepository(db_session).get_by_id(v60.id)
    refreshed_120 = await ProductVariantRepository(db_session).get_by_id(v120.id)
    refreshed_180 = await ProductVariantRepository(db_session).get_by_id(v180.id)
    refreshed_other = await ProductVariantRepository(db_session).get_by_id(other_variant.id)
    assert refreshed_60 is not None
    assert refreshed_120 is not None
    assert refreshed_180 is not None
    assert refreshed_other is not None

    assert refreshed_60.pack_size == 1
    assert refreshed_120.pack_size == 2
    assert refreshed_180.pack_size == 3
    assert refreshed_other.pack_size == 1  # untouched -- default, never guessed


async def test_apply_plan_is_idempotent(db_session: AsyncSession) -> None:
    product = await _make_canonical_product(db_session)
    await _make_variant(
        db_session, product_id=product.id, sku="AW-HM-PN-120", title="Paan / 120 - Pouches"
    )

    _, plan_1 = await build_plan(db_session)
    await apply_plan(db_session, plan_1)

    _, plan_2 = await build_plan(db_session)
    assert plan_2[0].current_pack_size == 2
    assert plan_2[0].current_pack_size == plan_2[0].target_pack_size  # already correct

    await apply_plan(db_session, plan_2)  # a no-op second run must not raise
    _, plan_3 = await build_plan(db_session)
    assert plan_3[0].current_pack_size == 2
