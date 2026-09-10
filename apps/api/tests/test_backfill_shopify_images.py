"""`scripts/backfill_shopify_images.py` -- the one-off, dry-run-by-default
Shopify image backfill for existing OMS Product/ProductVariant rows.

These tests exercise the script's pure planning/apply logic against a
fake Shopify adapter (canned `FetchPage`s + the REAL
`ShopifyProductNormalizer`, so image-omission behaviour is exactly what
live sync produces). They never touch a network or a real Shopify store.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.integrations.base import FetchPage
from app.integrations.shopify.normalizer import ShopifyProductNormalizer
from app.models.inventory import InventoryMovement
from app.models.product import CatalogVariant, Product, ProductVariant
from app.repositories.product import ProductRepository, ProductVariantRepository
from scripts.backfill_shopify_images import (
    ACTION_MISSING_OMS_PRODUCT,
    ACTION_MISSING_OMS_VARIANT,
    ACTION_NO_SHOPIFY_IMAGE,
    ACTION_UNCHANGED,
    ACTION_UPDATE,
    apply_plan,
    build_plan,
    has_blocking_problems,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


# --- fake Shopify adapter --------------------------------------------------


class _FakeAdapter:
    """Minimal stand-in: `fetch` replays canned pages in order; `normalize`
    runs the real normaliser so `image_url` omission is genuine.
    """

    def __init__(self, pages: list[FetchPage]) -> None:
        self._pages = list(pages)

    async def fetch(self, entity_type: str, *, cursor: str | None = None, limit: int = 50):
        assert entity_type == "products"
        return self._pages.pop(0)

    def normalize(self, entity_type: str, raw: dict) -> dict:
        assert entity_type == "products"
        return ShopifyProductNormalizer().normalize(raw)

    async def authenticate(self) -> None:  # pragma: no cover - not used in these tests
        return None

    async def aclose(self) -> None:  # pragma: no cover
        return None


def _raw_product(
    *,
    product_gid: str,
    title: str = "A Product",
    featured_image: str | None = None,
    variants: list[dict] | None = None,
) -> dict:
    node: dict = {
        "id": product_gid,
        "title": title,
        "status": "ACTIVE",
        "variants": {
            "edges": [{"node": v} for v in (variants or [])],
        },
    }
    if featured_image is not None:
        node["featuredImage"] = {"url": featured_image}
    return node


def _raw_variant(*, variant_gid: str, sku: str, image: str | None = None) -> dict:
    node: dict = {"id": variant_gid, "sku": sku}
    if image is not None:
        node["image"] = {"url": image}
    return node


def _one_page(nodes: list[dict]) -> list[FetchPage]:
    return [FetchPage(nodes=nodes, next_cursor=None, has_more=False)]


# --- OMS seed helpers ---------------------------------------------------


async def _seed_product(
    session: AsyncSession,
    *,
    key: str,
    shopify_product_id: str,
    image_url: str | None = None,
    title: str = "OMS Product",
):
    product, _ = await ProductRepository(session).upsert_by_external_id(
        source_system="shopify",
        external_id=f"prod-{key}",
        shopify_product_id=shopify_product_id,
        title=title,
        image_url=image_url,
    )
    await session.commit()
    return product


async def _seed_variant(
    session: AsyncSession,
    *,
    product_id,
    shopify_variant_id: str,
    sku: str,
    image_url: str | None = None,
    available_quantity: int = 17,
    inventory_quantity: int = 4,
    packets_per_box: int = 3,
    title: str = "OMS Variant",
):
    variant, _ = await ProductVariantRepository(session).upsert_by_external_id(
        source_system="shopify",
        external_id=f"var-{sku}",
        product_id=product_id,
        shopify_variant_id=shopify_variant_id,
        sku=sku,
        image_url=image_url,
        title=title,
        price=Decimal("100.00"),
        available_quantity=available_quantity,
        inventory_quantity=inventory_quantity,
        packets_per_box=packets_per_box,
    )
    await session.commit()
    return variant


# --- 1-2: product + variant image update planned ------------------------


async def test_plans_product_and_variant_image_updates(db_session: AsyncSession) -> None:
    product = await _seed_product(db_session, key="p1", shopify_product_id="100", image_url=None)
    await _seed_variant(
        db_session,
        product_id=product.id,
        shopify_variant_id="900",
        sku="SKU-900",
        image_url=None,
    )

    adapter = _FakeAdapter(
        _one_page(
            [
                _raw_product(
                    product_gid="gid://shopify/Product/100",
                    featured_image="https://cdn.shopify.com/s/files/1/prod.jpg",
                    variants=[
                        _raw_variant(
                            variant_gid="gid://shopify/ProductVariant/900",
                            sku="SKU-900",
                            image="https://cdn.shopify.com/s/files/1/variant.jpg",
                        )
                    ],
                )
            ]
        )
    )

    plan = await build_plan(db_session, adapter)

    assert plan.shopify_products_fetched == 1
    assert plan.shopify_variants_fetched == 1
    assert not has_blocking_problems(plan)
    (prec,) = plan.products
    (vrec,) = plan.variants
    assert prec.action == ACTION_UPDATE
    assert prec.shopify_image_url == "https://cdn.shopify.com/s/files/1/prod.jpg"
    assert vrec.action == ACTION_UPDATE
    assert vrec.shopify_image_url == "https://cdn.shopify.com/s/files/1/variant.jpg"


# --- 3: identical image -> no write ------------------------------------


async def test_identical_image_is_unchanged_and_not_rewritten(db_session: AsyncSession) -> None:
    same = "https://cdn.shopify.com/s/files/1/same.jpg"
    product = await _seed_product(db_session, key="p2", shopify_product_id="101", image_url=same)
    await _seed_variant(
        db_session,
        product_id=product.id,
        shopify_variant_id="901",
        sku="SKU-901",
        image_url=same,
    )

    adapter = _FakeAdapter(
        _one_page(
            [
                _raw_product(
                    product_gid="gid://shopify/Product/101",
                    featured_image=same,
                    variants=[
                        _raw_variant(
                            variant_gid="gid://shopify/ProductVariant/901",
                            sku="SKU-901",
                            image=same,
                        )
                    ],
                )
            ]
        )
    )

    plan = await build_plan(db_session, adapter)
    assert plan.products[0].action == ACTION_UNCHANGED
    assert plan.variants[0].action == ACTION_UNCHANGED

    updated_p, updated_v = await apply_plan(db_session, plan)
    assert (updated_p, updated_v) == (0, 0)


# --- 4: Shopify has no image -> existing OMS image preserved -----------


async def test_no_shopify_image_never_erases_existing_oms_image(db_session: AsyncSession) -> None:
    kept = "https://old-image.jpg"
    product = await _seed_product(db_session, key="p3", shopify_product_id="102", image_url=kept)
    variant = await _seed_variant(
        db_session,
        product_id=product.id,
        shopify_variant_id="902",
        sku="SKU-902",
        image_url=kept,
    )

    adapter = _FakeAdapter(
        _one_page(
            [
                _raw_product(
                    product_gid="gid://shopify/Product/102",
                    featured_image=None,  # Shopify has NO product image
                    variants=[
                        _raw_variant(
                            variant_gid="gid://shopify/ProductVariant/902",
                            sku="SKU-902",
                            image=None,  # ... and NO variant image
                        )
                    ],
                )
            ]
        )
    )

    plan = await build_plan(db_session, adapter)
    assert plan.products[0].action == ACTION_NO_SHOPIFY_IMAGE
    assert plan.variants[0].action == ACTION_NO_SHOPIFY_IMAGE
    assert not has_blocking_problems(plan)

    await apply_plan(db_session, plan)

    refreshed_p = await ProductRepository(db_session).get_by_id(product.id)
    refreshed_v = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed_p.image_url == kept  # untouched
    assert refreshed_v.image_url == kept  # untouched


# --- 5-6: missing OMS product / variant blocks ------------------------


async def test_missing_oms_product_blocks(db_session: AsyncSession) -> None:
    # Nothing seeded for shopify_product_id 200.
    adapter = _FakeAdapter(
        _one_page(
            [
                _raw_product(
                    product_gid="gid://shopify/Product/200",
                    featured_image="https://cdn.shopify.com/x.jpg",
                    variants=[],
                )
            ]
        )
    )
    plan = await build_plan(db_session, adapter)
    assert plan.products[0].action == ACTION_MISSING_OMS_PRODUCT
    assert has_blocking_problems(plan)


async def test_missing_oms_variant_blocks(db_session: AsyncSession) -> None:
    await _seed_product(db_session, key="p4", shopify_product_id="103")
    # The product exists in OMS; its variant 903 does not.
    adapter = _FakeAdapter(
        _one_page(
            [
                _raw_product(
                    product_gid="gid://shopify/Product/103",
                    featured_image="https://cdn.shopify.com/p.jpg",
                    variants=[
                        _raw_variant(variant_gid="gid://shopify/ProductVariant/903", sku="SKU-903")
                    ],
                )
            ]
        )
    )
    plan = await build_plan(db_session, adapter)
    assert plan.products[0].action != ACTION_MISSING_OMS_PRODUCT
    assert plan.variants[0].action == ACTION_MISSING_OMS_VARIANT
    assert has_blocking_problems(plan)


async def test_apply_refuses_a_blocked_plan(db_session: AsyncSession) -> None:
    adapter = _FakeAdapter(
        _one_page([_raw_product(product_gid="gid://shopify/Product/999", variants=[])])
    )
    plan = await build_plan(db_session, adapter)
    assert has_blocking_problems(plan)
    with pytest.raises(RuntimeError):
        await apply_plan(db_session, plan)


# --- 7: dry-run / build_plan performs no writes -----------------------


async def test_build_plan_issues_no_writes(db_session: AsyncSession) -> None:
    product = await _seed_product(db_session, key="p5", shopify_product_id="104", image_url=None)
    await _seed_variant(db_session, product_id=product.id, shopify_variant_id="904", sku="SKU-904")
    adapter = _FakeAdapter(
        _one_page(
            [
                _raw_product(
                    product_gid="gid://shopify/Product/104",
                    featured_image="https://cdn.shopify.com/new.jpg",
                    variants=[
                        _raw_variant(
                            variant_gid="gid://shopify/ProductVariant/904",
                            sku="SKU-904",
                            image="https://cdn.shopify.com/newv.jpg",
                        )
                    ],
                )
            ]
        )
    )

    plan = await build_plan(db_session, adapter)
    # build_plan is read-only: nothing staged for INSERT/UPDATE.
    assert not db_session.new
    assert not db_session.dirty
    # ... and the rows on disk still hold their pre-plan (NULL) images.
    refreshed_p = await ProductRepository(db_session).get_by_id(product.id)
    assert refreshed_p.image_url is None
    assert plan.products[0].action == ACTION_UPDATE  # the plan still SAYS it would update


# --- 8-10: apply writes ONLY image_url, creates nothing --------------


async def test_apply_updates_only_image_url_and_creates_nothing(db_session: AsyncSession) -> None:
    product = await _seed_product(
        db_session,
        key="p6",
        shopify_product_id="105",
        image_url=None,
        title="Locked Title",
    )
    variant = await _seed_variant(
        db_session,
        product_id=product.id,
        shopify_variant_id="905",
        sku="SKU-905",
        image_url=None,
        available_quantity=42,
        inventory_quantity=7,
        packets_per_box=6,
        title="Locked Variant Title",
    )
    # A catalog grouping the variant -- must be left completely alone.
    cv = CatalogVariant(product_id=product.id, name="Group", display_order=0)
    db_session.add(cv)
    await db_session.flush()
    variant.catalog_variant_id = cv.id
    await db_session.commit()

    products_before = (
        await db_session.execute(select(func.count()).select_from(Product))
    ).scalar_one()
    variants_before = (
        await db_session.execute(select(func.count()).select_from(ProductVariant))
    ).scalar_one()
    movements_before = (
        await db_session.execute(select(func.count()).select_from(InventoryMovement))
    ).scalar_one()

    adapter = _FakeAdapter(
        _one_page(
            [
                _raw_product(
                    product_gid="gid://shopify/Product/105",
                    title="SHOPIFY TITLE THAT MUST BE IGNORED",
                    featured_image="https://cdn.shopify.com/final-p.jpg",
                    variants=[
                        _raw_variant(
                            variant_gid="gid://shopify/ProductVariant/905",
                            sku="SKU-905",
                            image="https://cdn.shopify.com/final-v.jpg",
                        )
                    ],
                )
            ]
        )
    )
    plan = await build_plan(db_session, adapter)
    updated_p, updated_v = await apply_plan(db_session, plan)
    assert (updated_p, updated_v) == (1, 1)

    refreshed_p = await ProductRepository(db_session).get_by_id(product.id)
    refreshed_v = await ProductVariantRepository(db_session).get_by_id(variant.id)

    # the two allowed writes happened
    assert refreshed_p.image_url == "https://cdn.shopify.com/final-p.jpg"
    assert refreshed_v.image_url == "https://cdn.shopify.com/final-v.jpg"

    # nothing else moved
    assert refreshed_p.title == "Locked Title"
    assert refreshed_p.shopify_product_id == "105"
    assert refreshed_v.title == "Locked Variant Title"
    assert refreshed_v.sku == "SKU-905"
    assert refreshed_v.shopify_variant_id == "905"
    assert refreshed_v.available_quantity == 42
    assert refreshed_v.inventory_quantity == 7
    assert refreshed_v.packets_per_box == 6
    assert refreshed_v.catalog_variant_id == cv.id

    products_after = (
        await db_session.execute(select(func.count()).select_from(Product))
    ).scalar_one()
    variants_after = (
        await db_session.execute(select(func.count()).select_from(ProductVariant))
    ).scalar_one()
    movements_after = (
        await db_session.execute(select(func.count()).select_from(InventoryMovement))
    ).scalar_one()
    assert products_after == products_before  # no Product rows created
    assert variants_after == variants_before  # no ProductVariant rows created
    assert movements_after == movements_before  # no InventoryMovement rows created


# --- 11: running twice is idempotent --------------------------------


async def test_apply_is_idempotent(db_session: AsyncSession) -> None:
    product = await _seed_product(db_session, key="p7", shopify_product_id="106", image_url=None)
    await _seed_variant(db_session, product_id=product.id, shopify_variant_id="906", sku="SKU-906")

    def _fresh_adapter() -> _FakeAdapter:
        return _FakeAdapter(
            _one_page(
                [
                    _raw_product(
                        product_gid="gid://shopify/Product/106",
                        featured_image="https://cdn.shopify.com/idem-p.jpg",
                        variants=[
                            _raw_variant(
                                variant_gid="gid://shopify/ProductVariant/906",
                                sku="SKU-906",
                                image="https://cdn.shopify.com/idem-v.jpg",
                            )
                        ],
                    )
                ]
            )
        )

    plan1 = await build_plan(db_session, _fresh_adapter())
    assert await apply_plan(db_session, plan1) == (1, 1)

    plan2 = await build_plan(db_session, _fresh_adapter())
    assert plan2.products[0].action == ACTION_UNCHANGED
    assert plan2.variants[0].action == ACTION_UNCHANGED
    assert await apply_plan(db_session, plan2) == (0, 0)


# --- 12-14: quantities / movements / catalog assignments unchanged ---
# (Covered by test_apply_updates_only_image_url_and_creates_nothing above,
#  which asserts available_quantity/inventory_quantity/packets_per_box,
#  the InventoryMovement row count, and catalog_variant_id all unchanged.)


async def test_paginates_until_no_next_page(db_session: AsyncSession) -> None:
    """Two pages; the sweep must consume both, not stop after page 1."""
    p1 = await _seed_product(db_session, key="pg1", shopify_product_id="301")
    p2 = await _seed_product(db_session, key="pg2", shopify_product_id="302")
    await _seed_variant(db_session, product_id=p1.id, shopify_variant_id="311", sku="SKU-311")
    await _seed_variant(db_session, product_id=p2.id, shopify_variant_id="312", sku="SKU-312")

    pages = [
        FetchPage(
            nodes=[
                _raw_product(
                    product_gid="gid://shopify/Product/301",
                    featured_image="https://cdn.shopify.com/a.jpg",
                    variants=[
                        _raw_variant(variant_gid="gid://shopify/ProductVariant/311", sku="SKU-311")
                    ],
                )
            ],
            next_cursor="cursor-1",
            has_more=True,
        ),
        FetchPage(
            nodes=[
                _raw_product(
                    product_gid="gid://shopify/Product/302",
                    featured_image="https://cdn.shopify.com/b.jpg",
                    variants=[
                        _raw_variant(variant_gid="gid://shopify/ProductVariant/312", sku="SKU-312")
                    ],
                )
            ],
            next_cursor=None,
            has_more=False,
        ),
    ]
    plan = await build_plan(db_session, _FakeAdapter(pages))
    assert plan.shopify_products_fetched == 2
    assert plan.shopify_variants_fetched == 2
    assert not has_blocking_problems(plan)
