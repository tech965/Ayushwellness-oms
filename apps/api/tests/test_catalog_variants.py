"""OMS-visible catalog-variant grouping layer.

`CatalogVariant` groups one or more underlying Shopify `ProductVariant`
rows into an OMS-visible variant. It is presentation only: orders,
Shiprocket dispatch, RTO, `InventoryMovement`, SKUs, Shopify ids and
`available_quantity` are all untouched -- read APIs merely SUM the
underlying rows.

Fixtures build the groupings explicitly (no production data, no backfill
guessing).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.models.enums import PaymentType, RTOStatus
from app.models.product import CatalogVariant
from app.repositories.inventory import InventoryMovementRepository
from app.repositories.order import OrderItemRepository
from app.repositories.product import ProductRepository, ProductVariantRepository
from app.repositories.rto import RTORepository
from app.schemas.common import PageParams, SortParams
from app.schemas.order import OrderItemCreateRequest
from app.services.inventory_service import InventoryService
from app.services.order_service import OrderService
from app.services.product_service import ProductService
from app.services.shipment_service import ShipmentService
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


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
            available_quantity=spec["available_quantity"],
            packets_per_box=spec.get("packets_per_box", 1),
            inventory_quantity=spec.get("inventory_quantity", 0),
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


async def _herbal_masala(session: AsyncSession):
    """3 flavours x 2 pack sizes = 6 ProductVariants, grouped into 3
    OMS-visible flavour CatalogVariants.
    """
    product, v = await _product_with_variants(
        session,
        key="HM",
        variants=[
            {"sku": "HM-GUT-60", "available_quantity": 100, "packets_per_box": 60},
            {"sku": "HM-GUT-120", "available_quantity": 40, "packets_per_box": 120},
            {"sku": "HM-PAAN-60", "available_quantity": 200, "packets_per_box": 60},
            {"sku": "HM-PAAN-120", "available_quantity": 55, "packets_per_box": 120},
            {"sku": "HM-RG-60", "available_quantity": 10, "packets_per_box": 60},
            {"sku": "HM-RG-120", "available_quantity": 3, "packets_per_box": 120},
        ],
    )
    by_sku = {x.sku: x for x in v}
    gutka = await _make_catalog_variant(
        session,
        product_id=product.id,
        name="Ghutka Flavour",
        display_order=1,
        members=[by_sku["HM-GUT-60"], by_sku["HM-GUT-120"]],
    )
    paan = await _make_catalog_variant(
        session,
        product_id=product.id,
        name="Paan Masala Flavour",
        display_order=2,
        members=[by_sku["HM-PAAN-60"], by_sku["HM-PAAN-120"]],
    )
    royal = await _make_catalog_variant(
        session,
        product_id=product.id,
        name="Royal Tobacco Flavour",
        display_order=0,
        members=[by_sku["HM-RG-60"], by_sku["HM-RG-120"]],
    )
    return product, by_sku, {"gutka": gutka, "paan": paan, "royal": royal}


def _page() -> PageParams:
    return PageParams(page=1, page_size=50)


def _sort() -> SortParams:
    return SortParams(sort_by=None, sort_order="desc")


# --- 1-3: OMS-visible variant counts / grouping ------------------------


async def test_herbal_masala_returns_exactly_three_oms_visible_variants(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _by_sku, _cv = await _herbal_masala(db_session)

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as client:
        body = (await client.get(f"/api/v1/inventory/products/{product.id}/stock")).json()["data"]

    assert body["oms_variant_count"] == 3
    assert body["underlying_variant_count"] == 6  # every Shopify variant preserved
    names = [g["name"] for g in body["oms_variants"]]
    # ordered by display_order (Royal=0, Ghutka=1, Paan=2)
    assert names == ["Royal Tobacco Flavour", "Ghutka Flavour", "Paan Masala Flavour"]
    assert all(g["catalog_variant_id"] is not None for g in body["oms_variants"])


async def test_pack_size_variants_are_grouped_under_the_correct_flavour(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _by_sku, _cv = await _herbal_masala(db_session)

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as client:
        body = (await client.get(f"/api/v1/inventory/products/{product.id}/stock")).json()["data"]

    by_name = {g["name"]: g for g in body["oms_variants"]}
    assert {u["sku"] for u in by_name["Ghutka Flavour"]["underlying_variants"]} == {
        "HM-GUT-60",
        "HM-GUT-120",
    }
    assert {u["sku"] for u in by_name["Paan Masala Flavour"]["underlying_variants"]} == {
        "HM-PAAN-60",
        "HM-PAAN-120",
    }
    assert {u["sku"] for u in by_name["Royal Tobacco Flavour"]["underlying_variants"]} == {
        "HM-RG-60",
        "HM-RG-120",
    }
    for g in body["oms_variants"]:
        assert g["underlying_variant_count"] == 2


async def test_non_herbal_masala_product_returns_exactly_one_oms_visible_variant(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, variants = await _product_with_variants(
        db_session,
        key="VAJRA",
        variants=[
            {"sku": "VJR-30", "available_quantity": 331, "packets_per_box": 1},
            {"sku": "VJR-60", "available_quantity": 400, "packets_per_box": 1},
            {"sku": "VJR-90", "available_quantity": 262, "packets_per_box": 1},
        ],
    )
    await _make_catalog_variant(
        db_session, product_id=product.id, name="Vajrashakti", display_order=0, members=variants
    )

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as client:
        body = (await client.get(f"/api/v1/inventory/products/{product.id}/stock")).json()["data"]

    assert body["oms_variant_count"] == 1
    assert body["underlying_variant_count"] == 3
    assert body["oms_variants"][0]["name"] == "Vajrashakti"
    assert body["oms_variants"][0]["underlying_variant_count"] == 3


async def test_main_inventory_list_variant_count_is_oms_visible_count(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    hm_product, _by_sku, _cv = await _herbal_masala(db_session)
    single_product, single_variants = await _product_with_variants(
        db_session, key="SINGLE", variants=[{"sku": "ONE-PK", "available_quantity": 5}]
    )
    await _make_catalog_variant(
        db_session,
        product_id=single_product.id,
        name="The Product",
        display_order=0,
        members=single_variants,
    )

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as client:
        rows = (await client.get("/api/v1/inventory/stock")).json()["data"]

    by_id = {r["id"]: r for r in rows}
    assert by_id[str(hm_product.id)]["variant_count"] == 3
    assert by_id[str(single_product.id)]["variant_count"] == 1


# --- 4-6: aggregation math -------------------------------------------------


async def test_aggregate_boxes_are_sum_of_underlying_available_quantity(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _by_sku, _cv = await _herbal_masala(db_session)

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as client:
        body = (await client.get(f"/api/v1/inventory/products/{product.id}/stock")).json()["data"]

    by_name = {g["name"]: g for g in body["oms_variants"]}
    assert by_name["Ghutka Flavour"]["available_boxes"] == 100 + 40
    assert by_name["Paan Masala Flavour"]["available_boxes"] == 200 + 55
    assert by_name["Royal Tobacco Flavour"]["available_boxes"] == 10 + 3
    assert body["available_boxes"] == 100 + 40 + 200 + 55 + 10 + 3  # product total


async def test_aggregate_packets_use_sum_of_qty_times_packets_per_box(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _by_sku, _cv = await _herbal_masala(db_session)

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as client:
        body = (await client.get(f"/api/v1/inventory/products/{product.id}/stock")).json()["data"]

    by_name = {g["name"]: g for g in body["oms_variants"]}
    assert by_name["Ghutka Flavour"]["total_packets"] == 100 * 60 + 40 * 120
    assert by_name["Paan Masala Flavour"]["total_packets"] == 200 * 60 + 55 * 120


async def test_mixed_pack_sizes_do_not_fabricate_a_conversion_ratio(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _by_sku, _cv = await _herbal_masala(db_session)

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as client:
        body = (await client.get(f"/api/v1/inventory/products/{product.id}/stock")).json()["data"]

    for g in body["oms_variants"]:
        # every flavour groups a 60-pack and a 120-pack -> mixed
        assert g["packets_per_box_uniform"] is False
        assert "packets_per_box" not in g  # no single ratio invented for the group
    assert body["packets_per_box_uniform"] is False


# --- 7-9: existing data is untouched -------------------------------------


async def test_grouping_does_not_change_product_variant_quantities(
    db_session: AsyncSession,
) -> None:
    product, by_sku, _cv = await _herbal_masala(db_session)
    for sku, v in by_sku.items():
        refreshed = await ProductVariantRepository(db_session).get_by_id(v.id)
        assert refreshed.available_quantity == v.available_quantity
        assert refreshed.inventory_quantity == v.inventory_quantity
        assert refreshed.sku == sku
        assert refreshed.shopify_variant_id == v.shopify_variant_id
        assert refreshed.packets_per_box == v.packets_per_box


async def test_grouping_does_not_change_existing_order_items(db_session: AsyncSession) -> None:
    product, variants = await _product_with_variants(
        db_session, key="OI", variants=[{"sku": "OI-60", "available_quantity": 50}]
    )
    order = await OrderService(db_session).create_order(
        actor=None,
        order_number="ORD-CV-OI",
        customer_id=None,
        order_datetime=None,
        currency="INR",
        payment_type=PaymentType.PREPAID,
        shipping_charge=Decimal("0"),
        notes=None,
        items=[
            OrderItemCreateRequest(
                product_variant_id=variants[0].id,
                sku="OI-60",
                product_name="Product OI",
                quantity=2,
                unit_price=Decimal("100.00"),
            )
        ],
    )
    before = await OrderItemRepository(db_session).list_for_order(order.id)
    snap = [(i.id, i.product_variant_id, i.sku, i.quantity, i.total_amount) for i in before]

    await _make_catalog_variant(
        db_session, product_id=product.id, name="Product OI", display_order=0, members=variants
    )

    after = await OrderItemRepository(db_session).list_for_order(order.id)
    assert [(i.id, i.product_variant_id, i.sku, i.quantity, i.total_amount) for i in after] == snap


async def test_grouping_does_not_change_existing_inventory_movements(
    db_session: AsyncSession,
) -> None:
    product, variants = await _product_with_variants(
        db_session, key="MV", variants=[{"sku": "MV-60", "available_quantity": 20}]
    )
    movement = await InventoryService(db_session).adjust_to_target(
        variants[0].id, target_boxes=25, reason="count", actor=None
    )
    before = await InventoryMovementRepository(db_session).get_by_id_with_relations(movement.id)
    snap = (
        before.id,
        before.product_variant_id,
        before.quantity_delta,
        before.quantity_after,
        before.movement_type,
        before.reason,
    )

    await _make_catalog_variant(
        db_session, product_id=product.id, name="Product MV", display_order=0, members=variants
    )

    after_rows, total = await InventoryService(db_session).list_movements(
        page_params=_page(), sort_params=_sort(), product_variant_id=variants[0].id
    )
    assert total == 1
    a = after_rows[0]
    assert (
        a.id,
        a.product_variant_id,
        a.quantity_delta,
        a.quantity_after,
        a.movement_type,
        a.reason,
    ) == snap


# --- 10-11: Shiprocket dispatch / RTO unchanged under grouping ----------


async def test_shiprocket_dispatch_still_deducts_the_correct_product_variant(
    db_session: AsyncSession,
) -> None:
    product, variants = await _product_with_variants(
        db_session,
        key="DISP",
        variants=[
            {"sku": "DISP-A", "available_quantity": 10, "packets_per_box": 60},
            {"sku": "DISP-B", "available_quantity": 10, "packets_per_box": 60},
        ],
    )
    await _make_catalog_variant(
        db_session, product_id=product.id, name="Grouped", display_order=0, members=variants
    )
    order = await OrderService(db_session).create_order(
        actor=None,
        order_number="ORD-CV-DISP",
        customer_id=None,
        order_datetime=None,
        currency="INR",
        payment_type=PaymentType.PREPAID,
        shipping_charge=Decimal("0"),
        notes=None,
        items=[
            OrderItemCreateRequest(
                product_variant_id=variants[0].id,
                sku="DISP-A",
                product_name="Product DISP",
                quantity=120,  # 2 boxes at 60/box
                unit_price=Decimal("100.00"),
            )
        ],
    )
    shipment = await ShipmentService(db_session).create_shipment(
        actor=None,
        order_id=order.id,
        awb="AWB-CV-DISP",
        courier_id=None,
        expected_delivery_date=None,
    )
    service = InventoryService(db_session)
    await service.apply_dispatch(order_id=order.id, shipment_id=shipment.id)
    await service.apply_dispatch(order_id=order.id, shipment_id=shipment.id)  # idempotent

    a = await ProductVariantRepository(db_session).get_by_id(variants[0].id)
    b = await ProductVariantRepository(db_session).get_by_id(variants[1].id)
    assert a.available_quantity == 8  # 10 - 2, exactly once
    assert b.available_quantity == 10  # sibling in the same OMS variant untouched

    movements, total = await service.list_movements(
        page_params=_page(), sort_params=_sort(), product_variant_id=variants[0].id
    )
    assert total == 1 and movements[0].quantity_delta == -2


async def test_rto_restock_still_restores_the_correct_product_variant(
    db_session: AsyncSession,
) -> None:
    product, variants = await _product_with_variants(
        db_session,
        key="RTO",
        variants=[{"sku": "RTO-A", "available_quantity": 5, "packets_per_box": 60}],
    )
    await _make_catalog_variant(
        db_session, product_id=product.id, name="Grouped RTO", display_order=0, members=variants
    )
    order = await OrderService(db_session).create_order(
        actor=None,
        order_number="ORD-CV-RTO",
        customer_id=None,
        order_datetime=None,
        currency="INR",
        payment_type=PaymentType.PREPAID,
        shipping_charge=Decimal("0"),
        notes=None,
        items=[
            OrderItemCreateRequest(
                product_variant_id=variants[0].id,
                sku="RTO-A",
                product_name="Product RTO",
                quantity=120,
                unit_price=Decimal("100.00"),
            )
        ],
    )
    shipment = await ShipmentService(db_session).create_shipment(
        actor=None,
        order_id=order.id,
        awb="AWB-CV-RTO",
        courier_id=None,
        expected_delivery_date=None,
    )
    service = InventoryService(db_session)
    await service.apply_dispatch(order_id=order.id, shipment_id=shipment.id)
    rto, _ = await RTORepository(db_session).upsert_by_external_id(
        source_system="shiprocket",
        external_id="AWB-CV-RTO",
        shipment_id=shipment.id,
        order_id=order.id,
        status=RTOStatus.RECEIVED,
    )
    await db_session.commit()
    await service.apply_rto_restock(order_id=order.id, rto_id=rto.id)
    await service.apply_rto_restock(order_id=order.id, rto_id=rto.id)  # idempotent

    a = await ProductVariantRepository(db_session).get_by_id(variants[0].id)
    assert a.available_quantity == 5  # -2 dispatch then +2 restock, once


# --- 12: Shopify sync preserves grouping --------------------------------


async def test_shopify_resync_preserves_catalog_variant_assignments(
    db_session: AsyncSession,
) -> None:
    await ProductService(db_session).upsert_synced_product(
        source_system="shopify",
        external_id="prod-cv-sync",
        title="Sync CV Product",
        variants=[
            {
                "external_id": "var-cv-sync-1",
                "sku": "CV-SYNC-1",
                "price": Decimal("10.00"),
                "title": "Pack of 1",
                "inventory_quantity": 5,
            },
            {
                "external_id": "var-cv-sync-2",
                "sku": "CV-SYNC-2",
                "price": Decimal("10.00"),
                "title": "Pack of 2",
                "inventory_quantity": 5,
            },
        ],
    )
    product = await ProductRepository(db_session).get_by_source_external_id(
        source_system="shopify", external_id="prod-cv-sync"
    )
    variants = await ProductVariantRepository(db_session).list_for_product(product.id)
    cv = await _make_catalog_variant(
        db_session, product_id=product.id, name="Sync CV Product", display_order=0, members=variants
    )

    # Shopify changes titles and quantities on a later sync.
    await ProductService(db_session).upsert_synced_product(
        source_system="shopify",
        external_id="prod-cv-sync",
        title="Sync CV Product RENAMED",
        variants=[
            {
                "external_id": "var-cv-sync-1",
                "sku": "CV-SYNC-1",
                "price": Decimal("10.00"),
                "title": "Pack of 1 RENAMED",
                "inventory_quantity": 999,
            },
            {
                "external_id": "var-cv-sync-2",
                "sku": "CV-SYNC-2",
                "price": Decimal("10.00"),
                "title": "Pack of 2 RENAMED",
                "inventory_quantity": 999,
            },
        ],
    )

    for v in await ProductVariantRepository(db_session).list_for_product(product.id):
        assert v.catalog_variant_id == cv.id  # grouping survived the resync
    refreshed_cv = await db_session.get(CatalogVariant, cv.id)
    assert refreshed_cv.name == "Sync CV Product"  # sync never touches the OMS name


# --- 13-14: Edit Stock safety -----------------------------------------


async def test_no_aggregate_target_endpoint_exists_for_a_grouped_oms_variant(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """Editing a grouped OMS variant's stock is always per underlying
    ProductVariant. The product-level adjust endpoint refuses a
    multi-variant product (no auto-distribution); there is no
    catalog-variant-level stock endpoint at all.
    """
    product, _by_sku, cv = await _herbal_masala(db_session)

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.manage"]
    ) as client:
        # product-level adjust -> 422 (6 underlying variants)
        resp = await client.post(
            f"/api/v1/inventory/products/{product.id}/adjust",
            json={"target_boxes": 500, "reason": "no"},
        )
        assert resp.status_code == 422
        # there is no /catalog-variants/{id}/adjust route
        missing = await client.post(
            f"/api/v1/inventory/catalog-variants/{cv['gutka'].id}/adjust",
            json={"target_boxes": 500, "reason": "no"},
        )
        assert missing.status_code == 404

    # nothing moved
    for v in await ProductVariantRepository(db_session).list_for_product(product.id):
        assert v.available_quantity in (100, 40, 200, 55, 10, 3)


async def test_single_underlying_variant_edit_stock_still_works(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, variants = await _product_with_variants(
        db_session, key="SV", variants=[{"sku": "SV-1", "available_quantity": 395}]
    )
    await _make_catalog_variant(
        db_session, product_id=product.id, name="Single", display_order=0, members=variants
    )

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read", "inventory.manage"]
    ) as client:
        # per-variant adjust (what the UI uses for every OMS variant row)
        r1 = await client.post(
            f"/api/v1/inventory/stock/{variants[0].id}/adjust",
            json={"target_boxes": 400, "reason": "received"},
        )
        assert r1.status_code == 200 and r1.json()["data"]["quantity_delta"] == 5
        # single-variant product-level adjust still valid too
        r2 = await client.post(
            f"/api/v1/inventory/products/{product.id}/adjust",
            json={"target_boxes": 410, "reason": "received again"},
        )
        assert r2.status_code == 200 and r2.json()["data"]["quantity_after"] == 410

    refreshed = await ProductVariantRepository(db_session).get_by_id(variants[0].id)
    assert refreshed.available_quantity == 410


# --- 15: history aggregates underlying movements ----------------------


async def test_history_for_catalog_variant_spans_all_underlying_skus(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, by_sku, cv = await _herbal_masala(db_session)
    service = InventoryService(db_session)
    await service.adjust_to_target(by_sku["HM-GUT-60"].id, target_boxes=90, reason="a", actor=None)
    await service.adjust_to_target(by_sku["HM-GUT-120"].id, target_boxes=30, reason="b", actor=None)
    # a movement on a DIFFERENT flavour -- must NOT show up under Ghutka
    await service.adjust_to_target(
        by_sku["HM-PAAN-60"].id, target_boxes=150, reason="c", actor=None
    )

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as client:
        rows = (
            await client.get(
                "/api/v1/inventory/movements",
                params={"catalog_variant_id": str(cv["gutka"].id)},
            )
        ).json()["data"]

    assert len(rows) == 2
    assert {r["sku"] for r in rows} == {"HM-GUT-60", "HM-GUT-120"}  # spans both pack SKUs
    assert all(r["catalog_variant_id"] == str(cv["gutka"].id) for r in rows)


# --- 16: permissions -------------------------------------------------------


async def test_product_stock_requires_inventory_read(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _by_sku, _cv = await _herbal_masala(db_session)
    async with await make_authenticated_client(
        db_session, permission_codes=["analytics.read"], email="noperm@example.com"
    ) as client:
        resp = await client.get(f"/api/v1/inventory/products/{product.id}/stock")
        assert resp.status_code == 403


async def test_catalog_variant_rename_requires_inventory_manage(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _by_sku, cv = await _herbal_masala(db_session)

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"], email="readonly@example.com"
    ) as read_client:
        denied = await read_client.patch(
            f"/api/v1/inventory/catalog-variants/{cv['gutka'].id}/name",
            json={"name": "Hacked"},
        )
        assert denied.status_code == 403

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.manage"]
    ) as client:
        ok = await client.patch(
            f"/api/v1/inventory/catalog-variants/{cv['gutka'].id}/name",
            json={"name": "  Ghutka Flavour (Premium)  "},
        )
        assert ok.status_code == 200
        assert ok.json()["data"]["name"] == "Ghutka Flavour (Premium)"  # trimmed
        blank = await client.patch(
            f"/api/v1/inventory/catalog-variants/{cv['gutka'].id}/name",
            json={"name": "   "},
        )
        assert blank.status_code == 422

    refreshed = await db_session.get(CatalogVariant, cv["gutka"].id)
    assert refreshed.name == "Ghutka Flavour (Premium)"


# --- real-world Aayush Wellness Herbal Masala shape (Gold/Red/Blue Packet,
# 60/120/180 pouches) + draft-duplicate exclusion --------------------------


async def _herbal_masala_gold_red_blue(session: AsyncSession):
    """The real production shape: 3 flavours x 3 pack sizes = 9
    ProductVariants, grouped into 3 OMS-visible CatalogVariants named
    exactly as approved for the canonical active product (Royal Tobacco ->
    Gold Packet, Gutka -> Red Packet, Paan Masala -> Blue Packet).
    """
    product, v = await _product_with_variants(
        session,
        key="HMGRB",
        variants=[
            {"sku": "AW-HM-RG-60", "available_quantity": 10},
            {"sku": "AW-HM-RG-120", "available_quantity": 20},
            {"sku": "AW-HM-RG-180", "available_quantity": 30},
            {"sku": "AW-HM-CR-60", "available_quantity": 40},
            {"sku": "AW-HM-CR-120", "available_quantity": 50},
            {"sku": "AW-HM-CR-180", "available_quantity": 60},
            {"sku": "AW-HM-PN-60", "available_quantity": 70},
            {"sku": "AW-HM-PN-120", "available_quantity": 80},
            {"sku": "AW-HM-PN-180", "available_quantity": 90},
        ],
    )
    by_sku = {x.sku: x for x in v}
    gold = await _make_catalog_variant(
        session,
        product_id=product.id,
        name="Gold Packet",
        display_order=0,
        members=[by_sku["AW-HM-RG-60"], by_sku["AW-HM-RG-120"], by_sku["AW-HM-RG-180"]],
    )
    red = await _make_catalog_variant(
        session,
        product_id=product.id,
        name="Red Packet",
        display_order=1,
        members=[by_sku["AW-HM-CR-60"], by_sku["AW-HM-CR-120"], by_sku["AW-HM-CR-180"]],
    )
    blue = await _make_catalog_variant(
        session,
        product_id=product.id,
        name="Blue Packet",
        display_order=2,
        members=[by_sku["AW-HM-PN-60"], by_sku["AW-HM-PN-120"], by_sku["AW-HM-PN-180"]],
    )
    return product, by_sku, {"gold": gold, "red": red, "blue": blue}


async def test_gold_red_blue_packet_each_contain_their_three_pack_sizes(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _by_sku, _cv = await _herbal_masala_gold_red_blue(db_session)

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as client:
        body = (await client.get(f"/api/v1/inventory/products/{product.id}/stock")).json()["data"]

    assert body["oms_variant_count"] == 3
    assert body["underlying_variant_count"] == 9
    by_name = {g["name"]: g for g in body["oms_variants"]}
    assert set(by_name) == {"Gold Packet", "Red Packet", "Blue Packet"}
    assert {u["sku"] for u in by_name["Gold Packet"]["underlying_variants"]} == {
        "AW-HM-RG-60",
        "AW-HM-RG-120",
        "AW-HM-RG-180",
    }
    assert {u["sku"] for u in by_name["Red Packet"]["underlying_variants"]} == {
        "AW-HM-CR-60",
        "AW-HM-CR-120",
        "AW-HM-CR-180",
    }
    assert {u["sku"] for u in by_name["Blue Packet"]["underlying_variants"]} == {
        "AW-HM-PN-60",
        "AW-HM-PN-120",
        "AW-HM-PN-180",
    }
    for g in body["oms_variants"]:
        assert g["underlying_variant_count"] == 3


async def test_gold_red_blue_packet_variant_count_is_three_everywhere(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """Pins the exact regression this shape is prone to: 9 raw Shopify
    SKUs must never surface as 9 (or a partial 6) in either the Overview
    list or the detail payload -- only the grouped count of 3.
    """
    product, _by_sku, _cv = await _herbal_masala_gold_red_blue(db_session)

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as client:
        list_rows = (await client.get("/api/v1/inventory/stock")).json()["data"]
        detail = (
            await client.get(f"/api/v1/inventory/products/{product.id}/stock")
        ).json()["data"]

    by_id = {r["id"]: r for r in list_rows}
    assert by_id[str(product.id)]["variant_count"] == 3
    assert detail["oms_variant_count"] == 3


async def test_draft_duplicate_product_is_excluded_from_inventory_overview(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """Reproduces the real production confusion: an unpublished Shopify
    duplicate reuses the exact title of the real, grouped product but has
    never been grouped into CatalogVariants (`catalog_variant_id` is NULL
    on every row). If it were listed, staff could click into it from the
    Overview and see its raw, ungrouped SKU count (here 2) instead of the
    real product's OMS-visible count (3) -- exactly the "6 or 9" symptom
    reported for Aayush Wellness Herbal Masala. The draft row itself is
    never modified or grouped; it is only excluded from this listing.
    """
    active_product, _by_sku, _cv = await _herbal_masala_gold_red_blue(db_session)

    draft_product, _draft_variants = await _product_with_variants(
        db_session,
        key="HMGRB-DRAFT-DUP",
        variants=[
            {"sku": "AW-HM-DUP-60", "available_quantity": 1},
            {"sku": "AW-HM-DUP-120", "available_quantity": 1},
        ],
    )
    draft_product.title = active_product.title
    draft_product.status = "draft"
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as client:
        rows = (await client.get("/api/v1/inventory/stock")).json()["data"]

    ids = {r["id"] for r in rows}
    assert str(active_product.id) in ids
    assert str(draft_product.id) not in ids

    by_id = {r["id"]: r for r in rows}
    assert by_id[str(active_product.id)]["variant_count"] == 3


async def test_catalog_variant_with_no_members_still_returns_empty(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, variants = await _product_with_variants(
        db_session, key="EMPTY", variants=[{"sku": "EMPTY-1", "available_quantity": 7}]
    )
    # a declared flavour that no ProductVariant maps to yet
    await _make_catalog_variant(
        db_session, product_id=product.id, name="Future Flavour", display_order=0, members=[]
    )

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as client:
        body = (await client.get(f"/api/v1/inventory/products/{product.id}/stock")).json()["data"]

    by_name = {g["name"]: g for g in body["oms_variants"]}
    assert by_name["Future Flavour"]["underlying_variant_count"] == 0
    assert by_name["Future Flavour"]["available_boxes"] == 0
    assert by_name["Future Flavour"]["stock_status"] == "out_of_stock"
    # the unassigned real variant still shows as its own implicit OMS variant
    assert body["oms_variant_count"] == 2
