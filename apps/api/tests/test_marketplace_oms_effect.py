"""Marketplace Sale/RTO -> the product's OMS TOTAL stock.

One transaction writes the `ProductMarketplaceMovement` (the marketplace-
channel history/balance) AND a row on the EXISTING OMS-total ledger
(`catalog_variant_stock_adjustments`, the same one manual Edit Stock
uses): attached to the CatalogVariant when the product has exactly ONE
OMS-visible variant, otherwise product-scoped. No `ProductVariant` is
ever read for a decision or written to. See
`PlatformInventoryService.record_product_movement` and
`InventoryService.apply_marketplace_stock_effect`.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from app.core.timezone import ist_today
from app.models.audit_log import AuditLog
from app.models.enums import ProductMarketplaceMovementType
from app.models.inventory import InventoryMovement
from app.models.platform_inventory import InventoryPlatform, ProductMarketplaceMovement
from app.models.product import CatalogVariant, CatalogVariantStockAdjustment
from app.repositories.product import ProductRepository, ProductVariantRepository
from app.services.inventory_service import InventoryService
from app.services.platform_inventory_service import PlatformInventoryService
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

SALE = ProductMarketplaceMovementType.SALE
RTO = ProductMarketplaceMovementType.RTO


async def _make_grouped_product(session: AsyncSession, *, key: str, groups: dict[str, list[dict]]):
    """`groups`: CatalogVariant name -> variant specs. Returns
    `(product, {name: CatalogVariant}, [variants])`.
    """
    product, _ = await ProductRepository(session).upsert_by_external_id(
        source_system="shopify", external_id=f"prod-{key}", title=f"Product {key}"
    )
    cvs: dict[str, CatalogVariant] = {}
    variants = []
    for order, (name, specs) in enumerate(groups.items()):
        cv = CatalogVariant(product_id=product.id, name=name, display_order=order)
        session.add(cv)
        await session.flush()
        cvs[name] = cv
        for spec in specs:
            variant, _ = await ProductVariantRepository(session).upsert_by_external_id(
                source_system="shopify",
                external_id=f"var-{spec['sku']}",
                product_id=product.id,
                sku=spec["sku"],
                price=Decimal("100.00"),
                available_quantity=spec["available_quantity"],
                pack_size=spec.get("pack_size", 1),
                catalog_variant_id=cv.id,
            )
            variants.append(variant)
    await session.commit()
    return product, cvs, variants


async def _plain_product(session: AsyncSession, *, sku: str, available_quantity: int = 50):
    """One implicit (ungrouped) variant -- no CatalogVariant row at all."""
    product, _ = await ProductRepository(session).upsert_by_external_id(
        source_system="shopify", external_id=f"prod-{sku}", title=f"Product {sku}"
    )
    await ProductVariantRepository(session).upsert_by_external_id(
        source_system="shopify",
        external_id=f"var-{sku}",
        product_id=product.id,
        sku=sku,
        price=Decimal("100.00"),
        available_quantity=available_quantity,
    )
    await session.commit()
    return product


async def _single_cv_product(session: AsyncSession, key: str):
    # 300 + 396 + 296 = 992 (the approved worked example)
    return await _make_grouped_product(
        session,
        key=key,
        groups={
            "Vajra": [
                {"sku": f"{key}-30", "available_quantity": 300},
                {"sku": f"{key}-60", "available_quantity": 396},
                {"sku": f"{key}-90", "available_quantity": 296},
            ]
        },
    )


async def _count(session: AsyncSession, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def _stock(client, product_id) -> dict:
    return (await client.get(f"/api/v1/inventory/products/{product_id}/stock")).json()["data"]


async def _post(client, product_id, platform: str, kind: str, packets: int, reason=None):
    body = {"platform": platform, "movement_type": kind, "quantity_packets": packets}
    if reason:
        body["reason"] = reason
    return await client.post(
        f"/api/v1/inventory/products/{product_id}/marketplace-movements", json=body
    )


# --- Sale / RTO move the OMS total (backend-computed, never a UI subtraction) ---


async def test_sale_and_rto_move_the_single_catalog_variant_card_and_product_header(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, cvs, _ = await _single_cv_product(db_session, "OMS1")
    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read", "inventory.manage"]
    ) as client:
        before = await _stock(client, product.id)
        assert before["available_boxes"] == 992 and before["total_packets"] == 992

        assert (await _post(client, product.id, "amazon", "sale", 20)).status_code == 201
        after_sale = await _stock(client, product.id)
        assert after_sale["available_boxes"] == 972 and after_sale["total_packets"] == 972
        card = after_sale["oms_variants"][0]
        assert card["available_boxes"] == 972 and card["total_packets"] == 972
        assert after_sale["product_level_adjustment_boxes"] == 0  # attached to the CatalogVariant

        assert (await _post(client, product.id, "amazon", "rto", 2)).status_code == 201
        after_rto = await _stock(client, product.id)
        assert after_rto["available_boxes"] == 974 and after_rto["total_packets"] == 974

    rows = (await db_session.execute(select(CatalogVariantStockAdjustment))).scalars().all()
    assert sorted(r.quantity_delta for r in rows) == [-20, 2]
    assert all(r.catalog_variant_id == cvs["Vajra"].id and r.product_id is None for r in rows)


async def test_each_event_creates_exactly_one_movement_and_one_linked_ledger_row(
    db_session: AsyncSession,
) -> None:
    product, _, _ = await _single_cv_product(db_session, "OMS2")
    service = PlatformInventoryService(db_session)
    sale = await service.record_product_movement(
        product.id,
        platform=InventoryPlatform.FLIPKART,
        movement_type=SALE,
        quantity_packets=20,
        reason="Marketplace sale",
        stock_date=ist_today(),
        actor=None,
    )
    rto = await service.record_product_movement(
        product.id,
        platform=InventoryPlatform.FLIPKART,
        movement_type=RTO,
        quantity_packets=2,
        reason="Customer return",
        stock_date=ist_today(),
        actor=None,
    )

    assert await _count(db_session, ProductMarketplaceMovement) == 2  # two SEPARATE events
    assert await _count(db_session, CatalogVariantStockAdjustment) == 2
    assert sale.platform == rto.platform == InventoryPlatform.FLIPKART  # platform preserved
    ledger = {
        r.product_marketplace_movement_id: r
        for r in (await db_session.execute(select(CatalogVariantStockAdjustment))).scalars()
    }
    assert ledger[sale.id].quantity_delta == -20
    assert ledger[rto.id].quantity_delta == 2
    assert "Flipkart sale" in ledger[sale.id].reason
    assert "Marketplace sale" in ledger[sale.id].reason


async def test_multi_catalog_variant_product_requires_a_variant_and_never_chooses_one(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """Two or more CatalogVariants: the movement MUST name its OMS-visible
    variant -- it is never defaulted to a flavour, and nothing is written.
    """
    product, _, _ = await _make_grouped_product(
        db_session,
        key="OMS3",
        groups={
            "Gold": [{"sku": "O3-G60", "available_quantity": 10}],
            "Red": [{"sku": "O3-R60", "available_quantity": 40}],
            "Blue": [{"sku": "O3-B60", "available_quantity": 70}],
        },
    )
    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read", "inventory.manage"]
    ) as client:
        response = await _post(client, product.id, "amazon", "sale", 20)
    assert response.status_code == 422
    assert await _count(db_session, ProductMarketplaceMovement) == 0
    assert await _count(db_session, CatalogVariantStockAdjustment) == 0


async def test_product_with_no_catalog_variant_gets_a_product_level_row(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product = await _plain_product(db_session, sku="OMS4", available_quantity=50)
    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read", "inventory.manage"]
    ) as client:
        await _post(client, product.id, "blinkit", "sale", 5)
        body = await _stock(client, product.id)
    assert body["available_boxes"] == 45 and body["product_level_adjustment_boxes"] == -5
    # With one packet per outer, "Total Units" moves by the same 5 and the
    # product-level line reconciles in packets too.
    assert body["product_level_adjustment_packets"] == -5
    assert body["total_packets"] == 45


async def test_product_level_adjustment_packets_follow_packets_per_box_and_hide_when_mixed(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _, variants = await _make_grouped_product(
        db_session,
        key="PPB",
        groups={"Only": [{"sku": "PPB-A", "available_quantity": 50}]},
    )
    variants[0].packets_per_box = 60
    # No CatalogVariant-attached effect: force the product-level scope by
    # removing the group so the plain-product path is used.
    variants[0].catalog_variant_id = None
    await db_session.commit()
    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read", "inventory.manage"]
    ) as client:
        await _post(client, product.id, "blinkit", "sale", 300)  # 300 packets / 60 = 5 outers
        body = await _stock(client, product.id)
        assert body["product_level_adjustment_boxes"] == -5
        assert body["product_level_adjustment_packets"] == -300
        assert body["total_packets"] == 50 * 60 - 300

        # A second SKU with a different packets_per_box makes the packet
        # figure undefined -- reported as null, never guessed.
        await ProductVariantRepository(db_session).upsert_by_external_id(
            source_system="shopify",
            external_id="var-PPB-B",
            product_id=product.id,
            sku="PPB-B",
            price=Decimal("100.00"),
            available_quantity=10,
            packets_per_box=30,
        )
        await db_session.commit()
        mixed = await _stock(client, product.id)
        assert mixed["product_level_adjustment_boxes"] == -5
        assert mixed["product_level_adjustment_packets"] is None


async def test_no_product_variant_or_sku_movement_is_touched(db_session: AsyncSession) -> None:
    product, _, variants = await _single_cv_product(db_session, "OMS5")
    for v in variants:
        await ProductVariantRepository(db_session).update(v, inventory_quantity=777)
    await db_session.commit()

    def snap(rows):
        return {
            v.id: (
                v.available_quantity,
                v.inventory_quantity,
                v.pack_size,
                v.packets_per_box,
                v.catalog_variant_id,
            )
            for v in rows
        }

    before = snap(variants)
    movements_before = await _count(db_session, InventoryMovement)

    service = PlatformInventoryService(db_session)
    for kind in (SALE, RTO):
        await service.record_product_movement(
            product.id,
            platform=InventoryPlatform.MEESHO,
            movement_type=kind,
            quantity_packets=3,
            reason=None,
            stock_date=ist_today(),
            actor=None,
        )

    refreshed = await ProductVariantRepository(db_session).list_for_product(product.id)
    assert snap(refreshed) == before  # available_quantity, Shopify inventory_quantity, ...
    assert await _count(db_session, InventoryMovement) == movements_before


# --- atomicity ---------------------------------------------------------------


async def test_failure_writing_the_oms_ledger_rolls_back_the_marketplace_movement(
    db_session: AsyncSession, monkeypatch
) -> None:
    product, _, _ = await _single_cv_product(db_session, "OMS6")
    audit_before = await _count(db_session, AuditLog)

    async def _boom(self, **kwargs):
        raise RuntimeError("OMS ledger write failed")

    monkeypatch.setattr(InventoryService, "apply_marketplace_stock_effect", _boom)
    with pytest.raises(RuntimeError):
        await PlatformInventoryService(db_session).record_product_movement(
            product.id,
            platform=InventoryPlatform.AMAZON,
            movement_type=SALE,
            quantity_packets=20,
            reason=None,
            stock_date=ist_today(),
            actor=None,
        )

    assert await _count(db_session, ProductMarketplaceMovement) == 0  # neither ledger ahead
    assert await _count(db_session, CatalogVariantStockAdjustment) == 0
    assert await _count(db_session, AuditLog) == audit_before


async def test_failure_writing_the_marketplace_movement_leaves_the_oms_total_untouched(
    db_session: AsyncSession, monkeypatch
) -> None:
    product, _, _ = await _single_cv_product(db_session, "OMS6B")

    async def _boom(self, **kwargs):
        raise RuntimeError("marketplace ledger write failed")

    monkeypatch.setattr(PlatformInventoryService, "_append_movement", _boom)
    with pytest.raises(RuntimeError):
        await PlatformInventoryService(db_session).record_product_movement(
            product.id,
            platform=InventoryPlatform.AMAZON,
            movement_type=SALE,
            quantity_packets=20,
            reason=None,
            stock_date=ist_today(),
            actor=None,
        )
    assert await _count(db_session, CatalogVariantStockAdjustment) == 0
    assert await _count(db_session, ProductMarketplaceMovement) == 0


# --- conversion safety / validation ---------------------------------------------


async def test_non_uniform_conversion_is_rejected_and_writes_to_neither_ledger(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _, _ = await _make_grouped_product(
        db_session,
        key="OMS7",
        groups={
            "One": [
                {"sku": "O7-A", "available_quantity": 10, "pack_size": 1},
                {"sku": "O7-B", "available_quantity": 10, "pack_size": 2},
            ]
        },
    )
    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read", "inventory.manage"]
    ) as client:
        response = await _post(client, product.id, "amazon", "sale", 20)
    assert response.status_code == 422
    assert "pack sizes" in response.json()["error"]["message"]
    assert await _count(db_session, ProductMarketplaceMovement) == 0
    assert await _count(db_session, CatalogVariantStockAdjustment) == 0


async def test_uniform_pack_size_two_converts_the_oms_effect(db_session: AsyncSession) -> None:
    product, _, _ = await _make_grouped_product(
        db_session,
        key="OMS8",
        groups={"One": [{"sku": "O8-A", "available_quantity": 100, "pack_size": 2}]},
    )
    movement = await PlatformInventoryService(db_session).record_product_movement(
        product.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=SALE,
        quantity_packets=10,
        reason=None,
        stock_date=ist_today(),
        actor=None,
    )
    row = (await db_session.execute(select(CatalogVariantStockAdjustment))).scalars().one()
    assert movement.quantity_delta == row.quantity_delta == -20  # 10 packets * pack_size 2


@pytest.mark.parametrize("bad", [0, -5])
async def test_invalid_quantity_is_rejected_by_the_api(
    db_session: AsyncSession, make_authenticated_client, bad: int
) -> None:
    product = await _plain_product(db_session, sku=f"OMSBAD{abs(bad)}")
    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read", "inventory.manage"]
    ) as client:
        response = await _post(client, product.id, "amazon", "sale", bad)
    assert response.status_code == 422
    assert await _count(db_session, ProductMarketplaceMovement) == 0
    assert await _count(db_session, CatalogVariantStockAdjustment) == 0


async def test_add_stock_is_not_a_marketplace_operation(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product = await _plain_product(db_session, sku="OMS11")
    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read", "inventory.manage"]
    ) as client:
        response = await _post(client, product.id, "amazon", "stock_added", 5)
    assert response.status_code == 422
    assert await _count(db_session, ProductMarketplaceMovement) == 0
    assert await _count(db_session, CatalogVariantStockAdjustment) == 0


async def test_reason_is_optional_and_permission_is_still_required(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product = await _plain_product(db_session, sku="OMS12")
    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"], email="mkt-ro@example.com"
    ) as client:
        assert (await _post(client, product.id, "amazon", "sale", 1)).status_code == 403
    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read", "inventory.manage"]
    ) as client:
        assert (await _post(client, product.id, "amazon", "sale", 1)).status_code == 201


# --- coexistence with manual Edit Stock, the overview list, dispatch ---------------


async def test_manual_edit_stock_and_marketplace_effects_add_up_without_overwriting(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, cvs, _ = await _single_cv_product(db_session, "OMS9")
    cv_id = cvs["Vajra"].id
    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read", "inventory.manage"]
    ) as client:
        await client.post(
            f"/api/v1/inventory/catalog-variants/{cv_id}/adjust",
            json={"quantity_to_add": 8, "reason": "received"},
        )
        await _post(client, product.id, "amazon", "sale", 20)
        await client.post(
            f"/api/v1/inventory/catalog-variants/{cv_id}/adjust",
            json={"quantity_to_add": 2, "reason": "received again"},
        )
        body = await _stock(client, product.id)
        history = (
            await client.get(f"/api/v1/inventory/catalog-variants/{cv_id}/adjustments")
        ).json()["data"]

    assert body["available_boxes"] == 992 + 8 - 20 + 2
    assert len(history) == 3  # the marketplace effect is auditable next to the Edit Stock rows
    by_reason = {r["reason"]: r for r in history}
    assert by_reason["received again"]["quantity_after"] == 992 + 8 - 20 + 2  # it saw the sale


async def test_overview_list_total_matches_the_detail_header(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _, _ = await _single_cv_product(db_session, "OMS10")
    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read", "inventory.manage"]
    ) as client:
        await _post(client, product.id, "amazon", "sale", 20)
        rows = (await client.get("/api/v1/inventory/stock")).json()["data"]
    row = next(r for r in rows if r["id"] == str(product.id))
    assert row["total_available_boxes"] == 972 and row["total_packets"] == 972


# --- "Sold This Month" ------------------------------------------------------------


async def test_sold_this_month_sums_sales_across_platforms_and_excludes_rto(
    db_session: AsyncSession,
) -> None:
    product = await _plain_product(db_session, sku="SOLD1")
    service = PlatformInventoryService(db_session)
    today = ist_today()
    for platform, kind, qty in (
        (InventoryPlatform.AMAZON, SALE, 20),
        (InventoryPlatform.FLIPKART, SALE, 5),
        (InventoryPlatform.AMAZON, RTO, 2),  # never counted as sold
    ):
        await service.record_product_movement(
            product.id,
            platform=platform,
            movement_type=kind,
            quantity_packets=qty,
            reason=None,
            stock_date=today,
            actor=None,
        )
    summary = await service.get_product_platform_stock(product.id, stock_date=today)
    assert summary.sold_this_month_packets == 25


async def test_sold_this_month_excludes_other_months_and_is_zero_with_no_sales(
    db_session: AsyncSession,
) -> None:
    product = await _plain_product(db_session, sku="SOLD2")
    service = PlatformInventoryService(db_session)
    today = ist_today()
    empty = await service.get_product_platform_stock(product.id, stock_date=today)
    assert empty.sold_this_month_packets == 0

    last_month = today.replace(day=1) - timedelta(days=1)
    await service.record_product_movement(
        product.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=SALE,
        quantity_packets=99,
        reason=None,
        stock_date=last_month,
        actor=None,
    )
    summary = await service.get_product_platform_stock(product.id, stock_date=today)
    assert summary.sold_this_month_packets == 0


async def test_sold_this_month_is_scoped_to_the_product(db_session: AsyncSession) -> None:
    a = await _plain_product(db_session, sku="SOLD3A")
    b = await _plain_product(db_session, sku="SOLD3B")
    service = PlatformInventoryService(db_session)
    await service.record_product_movement(
        a.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=SALE,
        quantity_packets=7,
        reason=None,
        stock_date=ist_today(),
        actor=None,
    )
    today = ist_today()
    assert (
        await service.get_product_platform_stock(a.id, stock_date=today)
    ).sold_this_month_packets == 7
    assert (
        await service.get_product_platform_stock(b.id, stock_date=today)
    ).sold_this_month_packets == 0


async def test_platform_stock_endpoint_returns_the_monthly_figure(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product = await _plain_product(db_session, sku="SOLD4")
    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read", "inventory.manage"]
    ) as client:
        await _post(client, product.id, "amazon", "sale", 20)
        await _post(client, product.id, "flipkart", "sale", 5)
        data = (await client.get(f"/api/v1/inventory/products/{product.id}/platform-stock")).json()[
            "data"
        ]
    assert data["sold_this_month_packets"] == 25
