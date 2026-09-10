"""Shopify product images in the OMS Inventory UI.

`Product.image_url` (added by `f4a2c6e9b1d7`) is Shopify's featured
product image; `ProductVariant.image_url` (added by `b1e6d4f9a2c8`) is a
variant's OWN Shopify image, set only when Shopify actually assigns one
distinct from the product's. Both follow the same sync contract as
`title_override`/`available_quantity`: updated when Shopify's payload has
a value, left completely alone (never nulled) when it doesn't -- so a
transient missing image can never erase a previously-known one.

This is presentation only: it never changes `available_quantity`,
`inventory_quantity`, SKUs, Shopify ids, `CatalogVariant` assignments,
OrderItems, or InventoryMovements.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.integrations.shopify.normalizer import ShopifyProductNormalizer
from app.integrations.shopify.webhook_shapes import product_webhook_to_graphql_shape
from app.models.product import CatalogVariant
from app.repositories.inventory import InventoryMovementRepository
from app.repositories.product import ProductRepository, ProductVariantRepository
from app.schemas.common import PageParams
from app.services.inventory_service import InventoryService
from app.services.product_service import ProductService
from sqlalchemy.ext.asyncio import AsyncSession

# No module-level `pytestmark = pytest.mark.asyncio` -- this file mixes
# pure sync normalizer/webhook-shape tests with async DB-backed tests, so
# each async test is marked individually instead.


async def _product_with_variants(session: AsyncSession, *, key: str, variants: list[dict]):
    product, _ = await ProductRepository(session).upsert_by_external_id(
        source_system="shopify", external_id=f"prod-{key}", title=f"Product {key}"
    )
    made = []
    for spec in variants:
        variant, _ = await ProductVariantRepository(session).upsert_by_external_id(
            source_system="shopify",
            external_id=f"var-{spec['sku']}",
            product_id=product.id,
            sku=spec["sku"],
            title=spec.get("title"),
            price=Decimal("100.00"),
            available_quantity=spec.get("available_quantity", 0),
            packets_per_box=spec.get("packets_per_box", 1),
            inventory_quantity=spec.get("inventory_quantity", 0),
            image_url=spec.get("image_url"),
        )
        made.append(variant)
    await session.commit()
    return product, made


async def _make_catalog_variant(
    session: AsyncSession, *, product_id, name: str, display_order: int, members: list
) -> CatalogVariant:
    cv = CatalogVariant(product_id=product_id, name=name, display_order=display_order)
    session.add(cv)
    await session.flush()
    for v in members:
        v.catalog_variant_id = cv.id
    await session.commit()
    return cv


# --- 1-2: normalizer field mapping + omission-when-absent ----------------


def test_product_normalization_maps_featured_image() -> None:
    raw = {
        "id": "gid://shopify/Product/1",
        "title": "Aayush Wellness Herbal Masala",
        "featuredImage": {"url": "https://cdn.shopify.com/s/files/1/herbal-masala.jpg"},
        "variants": {"edges": []},
    }
    data = ShopifyProductNormalizer().normalize(raw)
    assert data["image_url"] == "https://cdn.shopify.com/s/files/1/herbal-masala.jpg"


def test_product_normalization_omits_image_key_when_shopify_has_none() -> None:
    """No `featuredImage` at all -- the key must be OMITTED from the
    normalized dict, not set to None, so `BaseRepository.update`'s blind
    `setattr` loop never touches (and never erases) an existing image.
    """
    raw = {"id": "gid://shopify/Product/1", "title": "No Image Product", "variants": {"edges": []}}
    data = ShopifyProductNormalizer().normalize(raw)
    assert "image_url" not in data


def test_variant_normalization_maps_its_own_image_when_present() -> None:
    raw = {
        "id": "gid://shopify/Product/1",
        "title": "P",
        "variants": {
            "edges": [
                {
                    "node": {
                        "id": "gid://shopify/ProductVariant/1",
                        "sku": "SKU-1",
                        "image": {"url": "https://cdn.shopify.com/s/files/1/royal-tobacco.jpg"},
                    }
                }
            ]
        },
    }
    data = ShopifyProductNormalizer().normalize(raw)
    assert data["variants"][0]["image_url"] == "https://cdn.shopify.com/s/files/1/royal-tobacco.jpg"


def test_variant_normalization_omits_image_key_when_shopify_has_none() -> None:
    raw = {
        "id": "gid://shopify/Product/1",
        "title": "P",
        "variants": {"edges": [{"node": {"id": "gid://shopify/ProductVariant/1", "sku": "SKU-1"}}]},
    }
    data = ShopifyProductNormalizer().normalize(raw)
    assert "image_url" not in data["variants"][0]


def test_webhook_shape_carries_product_and_variant_images_through() -> None:
    """REST webhook payload -> GraphQL node shape: the product's `image`
    and each variant's `image_id` (resolved against the product-level
    `images[]` array) both arrive at the normalizer in the same shape the
    GraphQL pull-sync already produces -- so a `products/update` webhook
    updates images too, not only the periodic pull sync.
    """
    raw = {
        "id": 42,
        "title": "Aayush Wellness Herbal Masala",
        "image": {"id": 100, "src": "https://cdn.shopify.com/s/files/1/featured.jpg"},
        "images": [
            {"id": 100, "src": "https://cdn.shopify.com/s/files/1/featured.jpg"},
            {"id": 101, "src": "https://cdn.shopify.com/s/files/1/royal-tobacco.jpg"},
        ],
        "variants": [
            {"id": 1, "sku": "AW-HM-RG-60", "image_id": 101},
            {"id": 2, "sku": "AW-HM-CR-60", "image_id": None},
        ],
        "options": [],
    }
    shaped = product_webhook_to_graphql_shape(raw)
    assert shaped["featuredImage"] == {"url": "https://cdn.shopify.com/s/files/1/featured.jpg"}

    normalized = ShopifyProductNormalizer().normalize(shaped)
    assert normalized["image_url"] == "https://cdn.shopify.com/s/files/1/featured.jpg"
    by_sku = {v["sku"]: v for v in normalized["variants"]}
    assert (
        by_sku["AW-HM-RG-60"]["image_url"] == "https://cdn.shopify.com/s/files/1/royal-tobacco.jpg"
    )
    assert "image_url" not in by_sku["AW-HM-CR-60"]  # no image_id -- omitted, not guessed


# --- 1, 2 (service level): persisted, and preserved across a blank sync --


@pytest.mark.asyncio
async def test_service_upsert_persists_shopify_image(db_session: AsyncSession) -> None:
    await ProductService(db_session).upsert_synced_product(
        source_system="shopify",
        external_id="prod-img-1",
        title="Herbal Masala",
        image_url="https://cdn.shopify.com/s/files/1/herbal-masala.jpg",
        variants=[
            {
                "external_id": "var-img-1",
                "sku": "SKU-IMG-1",
                "price": Decimal("50.00"),
                "image_url": "https://cdn.shopify.com/s/files/1/royal-tobacco.jpg",
            }
        ],
    )
    product = await ProductRepository(db_session).get_by_source_external_id(
        source_system="shopify", external_id="prod-img-1"
    )
    variant = await ProductVariantRepository(db_session).get_by_sku("SKU-IMG-1")
    assert product.image_url == "https://cdn.shopify.com/s/files/1/herbal-masala.jpg"
    assert variant.image_url == "https://cdn.shopify.com/s/files/1/royal-tobacco.jpg"


@pytest.mark.asyncio
async def test_resync_preserves_image_when_a_later_payload_has_none(
    db_session: AsyncSession,
) -> None:
    """The point of the feature: a later sync where Shopify momentarily
    reports no image must NOT erase a previously-known one -- same
    guarantee `title_override` gets, applied to a field the normalizer
    DOES actively update (unlike `title_override`, which it never emits
    at all).
    """
    await ProductService(db_session).upsert_synced_product(
        source_system="shopify",
        external_id="prod-img-2",
        title="Original Title",
        image_url="https://cdn.shopify.com/s/files/1/original.jpg",
        variants=[
            {
                "external_id": "var-img-2",
                "sku": "SKU-IMG-2",
                "price": Decimal("50.00"),
                "image_url": "https://cdn.shopify.com/s/files/1/original-variant.jpg",
            }
        ],
    )

    # Later sync: Shopify's payload has NO image this time (normalizer
    # omits the key entirely -- see test_product_normalization_omits_*
    # above), and the title changed.
    await ProductService(db_session).upsert_synced_product(
        source_system="shopify",
        external_id="prod-img-2",
        title="CHANGED Title",
        variants=[
            {
                "external_id": "var-img-2",
                "sku": "SKU-IMG-2",
                "price": Decimal("50.00"),
            }
        ],
    )

    product = await ProductRepository(db_session).get_by_source_external_id(
        source_system="shopify", external_id="prod-img-2"
    )
    variant = await ProductVariantRepository(db_session).get_by_sku("SKU-IMG-2")
    assert product.title == "CHANGED Title"  # the sync did update this field
    assert product.image_url == "https://cdn.shopify.com/s/files/1/original.jpg"  # image preserved
    assert variant.image_url == "https://cdn.shopify.com/s/files/1/original-variant.jpg"


# --- 3-4: title_override / CatalogVariant grouping survive an image sync -


@pytest.mark.asyncio
async def test_title_override_and_catalog_variant_grouping_survive_image_sync(
    db_session: AsyncSession,
) -> None:
    product, variants = await _product_with_variants(
        db_session,
        key="grp",
        variants=[
            {"sku": "SKU-GRP-1", "available_quantity": 10},
            {"sku": "SKU-GRP-2", "available_quantity": 20},
        ],
    )
    cv = await _make_catalog_variant(
        db_session, product_id=product.id, name="Grouped Variant", display_order=0, members=variants
    )
    await InventoryService(db_session).set_product_display_name(
        product.id, name="My Custom Name", actor=None
    )

    # A Shopify resync that also brings a product image.
    await ProductService(db_session).upsert_synced_product(
        source_system="shopify",
        external_id="prod-grp",
        title="Grouped Product",
        image_url="https://cdn.shopify.com/s/files/1/grouped.jpg",
        variants=[
            {"external_id": f"var-{v.sku}", "sku": v.sku, "price": Decimal("100.00")}
            for v in variants
        ],
    )

    refreshed_product = await ProductRepository(db_session).get_by_id(product.id)
    refreshed_variants = [
        await ProductVariantRepository(db_session).get_by_id(v.id) for v in variants
    ]
    assert refreshed_product.image_url == "https://cdn.shopify.com/s/files/1/grouped.jpg"
    assert refreshed_product.title_override == "My Custom Name"  # untouched by the image sync
    assert all(v.catalog_variant_id == cv.id for v in refreshed_variants)  # grouping untouched
    # Quantities untouched by the sync (requirement 9) -- the normalizer
    # never emits `available_quantity`.
    assert {v.available_quantity for v in refreshed_variants} == {10, 20}


# --- 9-10: inventory quantities / movements are untouched by image sync --


@pytest.mark.asyncio
async def test_image_sync_does_not_touch_inventory_quantities_or_movements(
    db_session: AsyncSession,
) -> None:
    product, variants = await _product_with_variants(
        db_session, key="qty", variants=[{"sku": "SKU-QTY-1", "available_quantity": 42}]
    )
    movements_before = await InventoryMovementRepository(db_session).list(
        page_params=PageParams(page=1, page_size=50),
        query=InventoryMovementRepository(db_session).search_query(product_id=product.id),
    )

    await ProductService(db_session).upsert_synced_product(
        source_system="shopify",
        external_id="prod-qty",
        title="Qty Product",
        image_url="https://cdn.shopify.com/s/files/1/qty.jpg",
        variants=[{"external_id": "var-qty-1", "sku": "SKU-QTY-1", "price": Decimal("10.00")}],
    )

    refreshed = await ProductVariantRepository(db_session).get_by_id(variants[0].id)
    assert refreshed.available_quantity == 42  # OMS-authoritative stock: untouched
    movements_after = await InventoryMovementRepository(db_session).list(
        page_params=PageParams(page=1, page_size=50),
        query=InventoryMovementRepository(db_session).search_query(product_id=product.id),
    )
    assert movements_after[1] == movements_before[1]  # count unchanged -- no ledger row written


# --- 5: main Inventory list response exposes image_url --------------------


@pytest.mark.asyncio
async def test_inventory_stock_list_exposes_product_image_url(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    await _product_with_variants(
        db_session,
        key="list-img",
        variants=[{"sku": "SKU-LIST-IMG", "available_quantity": 5}],
    )
    product = await ProductRepository(db_session).get_by_source_external_id(
        source_system="shopify", external_id="prod-list-img"
    )
    await ProductRepository(db_session).update(
        product, image_url="https://cdn.shopify.com/s/files/1/list.jpg"
    )
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as client:
        body = (await client.get("/api/v1/inventory/stock", params={"q": "list-img"})).json()

    row = next(r for r in body["data"] if r["id"] == str(product.id))
    assert row["image_url"] == "https://cdn.shopify.com/s/files/1/list.jpg"


# --- 6-8: OMS CatalogVariant response resolves image_url ------------------


@pytest.mark.asyncio
async def test_oms_variant_resolves_its_own_underlying_variant_image(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """Requirement 6 + 7: a CatalogVariant whose underlying ProductVariant
    has its OWN Shopify image resolves to THAT image, not the product's.
    """
    product, variants = await _product_with_variants(
        db_session,
        key="resolve",
        variants=[
            {
                "sku": "SKU-RESOLVE-1",
                "available_quantity": 5,
                "image_url": "https://cdn.shopify.com/s/files/1/variant-specific.jpg",
            }
        ],
    )
    await ProductRepository(db_session).update(
        product, image_url="https://cdn.shopify.com/s/files/1/product-featured.jpg"
    )
    await db_session.commit()
    await _make_catalog_variant(
        db_session, product_id=product.id, name="Only Variant", display_order=0, members=variants
    )

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as client:
        body = (await client.get(f"/api/v1/inventory/products/{product.id}/stock")).json()["data"]

    assert body["image_url"] == "https://cdn.shopify.com/s/files/1/product-featured.jpg"
    group = body["oms_variants"][0]
    assert group["image_url"] == "https://cdn.shopify.com/s/files/1/variant-specific.jpg"


@pytest.mark.asyncio
async def test_oms_variant_falls_back_to_product_image_when_no_variant_image(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """Requirement 8: no distinct Shopify variant image anywhere in the
    group -- falls back cleanly to the product's featured image, never a
    broken/missing state.
    """
    product, variants = await _product_with_variants(
        db_session,
        key="fallback",
        variants=[{"sku": "SKU-FALLBACK-1", "available_quantity": 5}],  # no image_url
    )
    await ProductRepository(db_session).update(
        product, image_url="https://cdn.shopify.com/s/files/1/product-only.jpg"
    )
    await db_session.commit()
    await _make_catalog_variant(
        db_session, product_id=product.id, name="Only Variant", display_order=0, members=variants
    )

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as client:
        body = (await client.get(f"/api/v1/inventory/products/{product.id}/stock")).json()["data"]

    assert (
        body["oms_variants"][0]["image_url"] == "https://cdn.shopify.com/s/files/1/product-only.jpg"
    )


@pytest.mark.asyncio
async def test_canonical_style_flavour_variants_each_resolve_their_own_image(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """The canonical Herbal Masala shape: 3 flavour CatalogVariants, each
    with a distinct Shopify variant image -- every OMS card resolves its
    OWN flavour's photo, never another flavour's and never guessed from
    filename/ordering (the association here is the explicit
    ProductVariant.image_url each member was given, exactly as the real
    sync would set it from Shopify's variant/image data).
    """
    product, v = await _product_with_variants(
        db_session,
        key="canon",
        variants=[
            {
                "sku": "AW-HM-RG-60",
                "available_quantity": 10,
                "image_url": "https://cdn.shopify.com/s/files/1/royal-tobacco.jpg",
            },
            {
                "sku": "AW-HM-CR-60",
                "available_quantity": 20,
                "image_url": "https://cdn.shopify.com/s/files/1/ghutka.jpg",
            },
            {
                "sku": "AW-HM-PN-60",
                "available_quantity": 30,
                "image_url": "https://cdn.shopify.com/s/files/1/paan-masala.jpg",
            },
        ],
    )
    await ProductRepository(db_session).update(
        product, image_url="https://cdn.shopify.com/s/files/1/herbal-masala.jpg"
    )
    await db_session.commit()
    by_sku = {x.sku: x for x in v}
    await _make_catalog_variant(
        db_session,
        product_id=product.id,
        name="Royal Tobacco Flavour",
        display_order=0,
        members=[by_sku["AW-HM-RG-60"]],
    )
    await _make_catalog_variant(
        db_session,
        product_id=product.id,
        name="Ghutka Flavour",
        display_order=1,
        members=[by_sku["AW-HM-CR-60"]],
    )
    await _make_catalog_variant(
        db_session,
        product_id=product.id,
        name="Paan Masala Flavour",
        display_order=2,
        members=[by_sku["AW-HM-PN-60"]],
    )

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as client:
        body = (await client.get(f"/api/v1/inventory/products/{product.id}/stock")).json()["data"]

    assert body["oms_variant_count"] == 3
    by_name = {g["name"]: g["image_url"] for g in body["oms_variants"]}
    assert by_name["Royal Tobacco Flavour"] == "https://cdn.shopify.com/s/files/1/royal-tobacco.jpg"
    assert by_name["Ghutka Flavour"] == "https://cdn.shopify.com/s/files/1/ghutka.jpg"
    assert by_name["Paan Masala Flavour"] == "https://cdn.shopify.com/s/files/1/paan-masala.jpg"


@pytest.mark.asyncio
async def test_multi_image_group_resolves_deterministically_by_lowest_sku(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """Edge case: TWO underlying variants in the same OMS group each have
    their own (different) Shopify image -- e.g. two pack sizes of the
    same flavour that happen to have distinct photos. `list_for_product`
    has no `ORDER BY`, so the resolution must not depend on incidental DB
    scan order: it picks the lowest-SKU member with an image, always --
    proven here with the members created in BOTH possible orderings
    (two separate products, since a `ProductVariant` can only ever
    belong to one `CatalogVariant`).

    "AW-HM-RG-120" < "AW-HM-RG-60" as a plain string (lexicographic, not
    numeric, comparison: '1' < '6') -- the point isn't which SKU wins,
    it's that the SAME one wins every time.
    """
    for key, first_sku in (("multi-img-a", "120"), ("multi-img-b", "60")):
        product, v = await _product_with_variants(
            db_session,
            key=key,
            variants=(
                [
                    {
                        "sku": "AW-HM-RG-120",
                        "available_quantity": 5,
                        "image_url": "https://cdn.shopify.com/s/files/1/royal-tobacco-120.jpg",
                    },
                    {
                        "sku": "AW-HM-RG-60",
                        "available_quantity": 10,
                        "image_url": "https://cdn.shopify.com/s/files/1/royal-tobacco-60.jpg",
                    },
                ]
                if first_sku == "120"
                else [
                    {
                        "sku": "AW-HM-RG-60",
                        "available_quantity": 10,
                        "image_url": "https://cdn.shopify.com/s/files/1/royal-tobacco-60.jpg",
                    },
                    {
                        "sku": "AW-HM-RG-120",
                        "available_quantity": 5,
                        "image_url": "https://cdn.shopify.com/s/files/1/royal-tobacco-120.jpg",
                    },
                ]
            ),
        )
        await _make_catalog_variant(
            db_session,
            product_id=product.id,
            name="Royal Tobacco Flavour",
            display_order=0,
            members=v,  # insertion order matches the table above
        )

        async with await make_authenticated_client(
            db_session, permission_codes=["inventory.read"], email=f"{key}@example.com"
        ) as client:
            body = (await client.get(f"/api/v1/inventory/products/{product.id}/stock")).json()[
                "data"
            ]

        assert (
            body["oms_variants"][0]["image_url"]
            == "https://cdn.shopify.com/s/files/1/royal-tobacco-120.jpg"
        )
