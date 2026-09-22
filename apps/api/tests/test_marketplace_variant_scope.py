"""Per-OMS-variant marketplace stock (Herbal Masala: Gold/Red/Blue) and
append-only Edit / Undo.

A product with TWO OR MORE CatalogVariants tracks marketplace stock per
OMS-visible variant, keyed `(product_id, catalog_variant_id, platform)`;
the underlying 60/120/180 SKUs never get a table or a scope of their own.
Edit and Undo never update or delete a ledger row: they append a reversal
(and, for an Edit, a replacement), so the history is a permanent audit
trail and the effective balance is the running sum.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from app.core.timezone import ist_today
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
REVERSAL = ProductMarketplaceMovementType.REVERSAL
PERMS = ["inventory.read", "inventory.manage"]


async def _grouped_product(session: AsyncSession, *, key: str, groups: dict[str, list[dict]]):
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
                available_quantity=spec["qty"],
                pack_size=spec.get("pack_size", 1),
                catalog_variant_id=cv.id,
            )
            variants.append(variant)
    await session.commit()
    return product, cvs, variants


async def _herbal(session: AsyncSession, key: str = "HERB"):
    """3 OMS variants x 3 pack-size SKUs (60/120/180) = 9 SKUs.
    Gold 10+20+30=60, Red 40+50+60=150, Blue 70+80+90=240.
    """
    return await _grouped_product(
        session,
        key=key,
        groups={
            "Gold Packet": [
                {"sku": f"{key}-G60", "qty": 10},
                {"sku": f"{key}-G120", "qty": 20},
                {"sku": f"{key}-G180", "qty": 30},
            ],
            "Red Packet": [
                {"sku": f"{key}-R60", "qty": 40},
                {"sku": f"{key}-R120", "qty": 50},
                {"sku": f"{key}-R180", "qty": 60},
            ],
            "Blue Packet": [
                {"sku": f"{key}-B60", "qty": 70},
                {"sku": f"{key}-B120", "qty": 80},
                {"sku": f"{key}-B180", "qty": 90},
            ],
        },
    )


async def _single_cv(session: AsyncSession, key: str):
    return await _grouped_product(
        session,
        key=key,
        groups={"Vajra": [{"sku": f"{key}-30", "qty": 300}, {"sku": f"{key}-60", "qty": 692}]},
    )


async def _count(session: AsyncSession, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def _post(client, product_id, platform, kind, packets, *, cv=None, reason=None):
    body = {"platform": platform, "movement_type": kind, "quantity_packets": packets}
    if cv is not None:
        body["catalog_variant_id"] = str(cv.id)
    if reason:
        body["reason"] = reason
    return await client.post(
        f"/api/v1/inventory/products/{product_id}/marketplace-movements", json=body
    )


async def _stock(client, product_id) -> dict:
    return (await client.get(f"/api/v1/inventory/products/{product_id}/stock")).json()["data"]


async def _platform(client, product_id) -> dict:
    return (await client.get(f"/api/v1/inventory/products/{product_id}/platform-stock")).json()[
        "data"
    ]


def _row(section_or_data: dict, platform: str) -> dict:
    return next(r for r in section_or_data["platforms"] if r["platform"] == platform)


def _section(data: dict, name: str) -> dict:
    return next(v for v in data["variants"] if v["name"] == name)


async def _history(client, product_id, cv=None) -> list[dict]:
    params = {"catalog_variant_id": str(cv.id)} if cv is not None else {}
    response = await client.get(
        f"/api/v1/inventory/products/{product_id}/marketplace-movements", params=params
    )
    return response.json()["data"]


# --- one Marketplace Stock table per OMS variant ------------------------------


async def test_herbal_gets_one_marketplace_section_per_oms_variant_never_per_sku(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _, variants = await _herbal(db_session)
    assert len(variants) == 9
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        data = await _platform(client, product.id)

    assert data["scope"] == "catalog_variant"
    assert data["platforms"] == []  # NO combined Herbal table
    assert [v["name"] for v in data["variants"]] == ["Gold Packet", "Red Packet", "Blue Packet"]
    assert len(data["variants"]) == 3  # 9 SKUs -> 3 sections, never 9
    for section in data["variants"]:
        assert [r["platform"] for r in section["platforms"]] == [
            "shopify",
            "amazon",
            "flipkart",
            "blinkit",
            "meesho",
            "manual_other",
        ]
        assert _row(section, "shopify")["is_automatic"] is True


async def test_each_variants_shopify_row_sums_only_its_own_skus(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _, _ = await _herbal(db_session)
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        data = await _platform(client, product.id)
    assert _row(_section(data, "Gold Packet"), "shopify")["current_stock"] == 60
    assert _row(_section(data, "Red Packet"), "shopify")["current_stock"] == 150
    assert _row(_section(data, "Blue Packet"), "shopify")["current_stock"] == 240


@pytest.mark.parametrize(
    ("moved", "others"),
    [
        ("Gold Packet", ("Red Packet", "Blue Packet")),
        ("Red Packet", ("Gold Packet", "Blue Packet")),
        ("Blue Packet", ("Gold Packet", "Red Packet")),
    ],
)
async def test_a_movement_affects_only_its_own_variant(
    db_session: AsyncSession, make_authenticated_client, moved: str, others: tuple[str, str]
) -> None:
    product, cvs, _ = await _herbal(db_session)
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        assert (
            await _post(client, product.id, "amazon", "sale", 20, cv=cvs[moved])
        ).status_code == 201
        data = await _platform(client, product.id)
        stock = await _stock(client, product.id)

    assert _row(_section(data, moved), "amazon")["current_stock"] == -20
    for other in others:  # Amazon in the other two variants is untouched
        assert _row(_section(data, other), "amazon")["current_stock"] == 0
    # ...and so is their OMS card; only the moved variant's own card dropped
    base = {"Gold Packet": 60, "Red Packet": 150, "Blue Packet": 240}
    cards = {g["name"]: g["available_boxes"] for g in stock["oms_variants"]}
    assert cards[moved] == base[moved] - 20
    for other in others:
        assert cards[other] == base[other]
    assert stock["available_boxes"] == 450 - 20
    assert stock["product_level_adjustment_boxes"] == 0  # attached to the variant, not the product


async def test_rto_on_blue_affects_only_blue(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, cvs, _ = await _herbal(db_session)
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        await _post(client, product.id, "amazon", "rto", 2, cv=cvs["Blue Packet"])
        data = await _platform(client, product.id)
    assert _row(_section(data, "Blue Packet"), "amazon")["current_stock"] == 2
    assert _row(_section(data, "Gold Packet"), "amazon")["current_stock"] == 0
    assert _row(_section(data, "Red Packet"), "amazon")["current_stock"] == 0


async def test_every_platform_is_independent_within_a_variant(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, cvs, _ = await _herbal(db_session)
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        for platform, qty in (("amazon", 1), ("flipkart", 2), ("blinkit", 3), ("meesho", 4)):
            await _post(client, product.id, platform, "rto", qty, cv=cvs["Gold Packet"])
        data = await _platform(client, product.id)
    gold = _section(data, "Gold Packet")
    assert [
        _row(gold, p)["current_stock"] for p in ("amazon", "flipkart", "blinkit", "meesho")
    ] == [
        1,
        2,
        3,
        4,
    ]
    assert _row(gold, "manual_other")["current_stock"] == 0


async def test_no_product_variant_is_touched_by_a_variant_scoped_movement(
    db_session: AsyncSession,
) -> None:
    product, cvs, variants = await _herbal(db_session)

    def snap(rows):
        return {
            v.id: (v.available_quantity, v.inventory_quantity, v.pack_size, v.catalog_variant_id)
            for v in rows
        }

    before = snap(variants)
    inv_before = await _count(db_session, InventoryMovement)
    service = PlatformInventoryService(db_session)
    for name, kind in (("Gold Packet", SALE), ("Red Packet", RTO)):
        await service.record_product_movement(
            product.id, platform=InventoryPlatform.AMAZON, movement_type=kind,
            quantity_packets=3, reason=None, stock_date=ist_today(), actor=None,
            catalog_variant_id=cvs[name].id,
        )  # fmt: skip
    after = snap(await ProductVariantRepository(db_session).list_for_product(product.id))
    assert after == before
    assert await _count(db_session, InventoryMovement) == inv_before


# --- validation of the scope key ------------------------------------------------


async def test_a_variant_is_required_for_a_multi_variant_product(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _, _ = await _herbal(db_session)
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        assert (await _post(client, product.id, "amazon", "sale", 5)).status_code == 422
    assert await _count(db_session, ProductMarketplaceMovement) == 0


async def test_another_products_variant_is_rejected(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _, _ = await _herbal(db_session, "HERBA")
    _, other_cvs, _ = await _herbal(db_session, "HERBB")
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        response = await _post(client, product.id, "amazon", "sale", 5, cv=other_cvs["Gold Packet"])
    assert response.status_code == 422
    assert await _count(db_session, ProductMarketplaceMovement) == 0


async def test_a_single_variant_product_stays_product_scoped_and_rejects_a_variant(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, cvs, _ = await _single_cv(db_session, "SINGLE")
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        assert (
            await _post(client, product.id, "amazon", "sale", 5, cv=cvs["Vajra"])
        ).status_code == 422
        assert (await _post(client, product.id, "amazon", "sale", 5)).status_code == 201
        data = await _platform(client, product.id)
    assert data["scope"] == "product" and data["variants"] == []
    assert _row(data, "amazon")["current_stock"] == -5


async def test_shopify_is_not_a_manual_platform(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, cvs, _ = await _herbal(db_session)
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        response = await _post(client, product.id, "shopify", "sale", 5, cv=cvs["Gold Packet"])
    assert response.status_code == 422


async def test_conversion_uniformity_is_checked_per_variant_not_per_product(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """Gold's SKUs disagree on pack_size, Red's agree: Gold is rejected
    (nothing written), Red still works.
    """
    product, cvs, _ = await _grouped_product(
        db_session,
        key="MIXED",
        groups={
            "Gold Packet": [
                {"sku": "MX-G60", "qty": 10, "pack_size": 1},
                {"sku": "MX-G120", "qty": 10, "pack_size": 2},
            ],
            "Red Packet": [
                {"sku": "MX-R60", "qty": 10, "pack_size": 2},
                {"sku": "MX-R120", "qty": 10, "pack_size": 2},
            ],
        },
    )
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        gold = await _post(client, product.id, "amazon", "sale", 5, cv=cvs["Gold Packet"])
        assert gold.status_code == 422
        assert await _count(db_session, ProductMarketplaceMovement) == 0
        red = await _post(client, product.id, "amazon", "sale", 5, cv=cvs["Red Packet"])
        assert red.status_code == 201
    row = (await db_session.execute(select(CatalogVariantStockAdjustment))).scalars().one()
    assert row.quantity_delta == -10 and row.catalog_variant_id == cvs["Red Packet"].id


# --- monthly sales are variant-specific --------------------------------------------


async def test_monthly_sales_are_per_variant_and_exclude_rto(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, cvs, _ = await _herbal(db_session)
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        await _post(client, product.id, "amazon", "sale", 20, cv=cvs["Gold Packet"])
        await _post(client, product.id, "flipkart", "sale", 5, cv=cvs["Gold Packet"])
        await _post(client, product.id, "amazon", "sale", 8, cv=cvs["Red Packet"])
        await _post(client, product.id, "amazon", "rto", 9, cv=cvs["Red Packet"])  # not "sold"
        await _post(client, product.id, "blinkit", "sale", 14, cv=cvs["Blue Packet"])
        data = await _platform(client, product.id)
    assert _section(data, "Gold Packet")["sold_this_month_packets"] == 25
    assert _section(data, "Red Packet")["sold_this_month_packets"] == 8
    assert _section(data, "Blue Packet")["sold_this_month_packets"] == 14
    assert data["sold_this_month_packets"] == 0  # no combined Herbal figure


# --- history is variant-scoped ------------------------------------------------------


async def test_history_is_filtered_per_variant(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, cvs, _ = await _herbal(db_session)
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        await _post(client, product.id, "amazon", "sale", 20, cv=cvs["Gold Packet"])
        await _post(client, product.id, "amazon", "rto", 2, cv=cvs["Blue Packet"])
        gold = await _history(client, product.id, cvs["Gold Packet"])
        blue = await _history(client, product.id, cvs["Blue Packet"])
        red = await _history(client, product.id, cvs["Red Packet"])
    assert [(r["movement_type"], r["quantity_packets"]) for r in gold] == [("sale", 20)]
    assert [(r["movement_type"], r["quantity_packets"]) for r in blue] == [("rto", 2)]
    assert red == []
    assert gold[0]["catalog_variant_id"] == str(cvs["Gold Packet"].id)


# --- Undo --------------------------------------------------------------------------


async def _undo(client, movement_id, reason="wrong entry"):
    return await client.post(
        f"/api/v1/inventory/marketplace-movements/{movement_id}/undo", json={"reason": reason}
    )


async def _edit(client, movement_id, packets, reason="correction"):
    return await client.post(
        f"/api/v1/inventory/marketplace-movements/{movement_id}/edit",
        json={"quantity_packets": packets, "reason": reason},
    )


async def test_undo_appends_a_reversal_and_keeps_the_original(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, cvs, _ = await _herbal(db_session)
    gold = cvs["Gold Packet"]
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        sale = (await _post(client, product.id, "amazon", "sale", 20, cv=gold)).json()["data"]
        before_undo = await _stock(client, product.id)
        assert before_undo["available_boxes"] == 450 - 20

        undo = await _undo(client, sale["id"], "customer cancelled")
        assert undo.status_code == 201
        reversal = undo.json()["data"]
        data = await _platform(client, product.id)
        stock = await _stock(client, product.id)
        history = await _history(client, product.id, gold)

    # nothing deleted or rewritten: original + reversal, both present
    assert await _count(db_session, ProductMarketplaceMovement) == 2
    assert reversal["movement_type"] == "reversal"
    assert reversal["quantity_delta"] == 20 and reversal["quantity_packets"] == 20
    assert reversal["reverses_movement_id"] == sale["id"]
    assert reversal["reason"] == "customer cancelled"
    assert reversal["catalog_variant_id"] == str(gold.id)  # scope preserved
    assert reversal["platform"] == "amazon"  # platform preserved
    assert reversal["actor_user_id"] is not None
    # effective balances are back to the pre-sale values
    assert _row(_section(data, "Gold Packet"), "amazon")["current_stock"] == 0
    assert stock["available_boxes"] == 450
    assert {g["name"]: g["available_boxes"] for g in stock["oms_variants"]}["Gold Packet"] == 60
    # status of each history row
    by_type = {r["movement_type"]: r for r in history}
    assert by_type["sale"]["status"] == "undone"
    assert by_type["sale"]["can_edit"] is False and by_type["sale"]["can_undo"] is False
    assert by_type["reversal"]["status"] == "reversal"
    assert by_type["reversal"]["can_edit"] is False and by_type["reversal"]["can_undo"] is False
    # the original row itself is byte-for-byte what was written
    original = await db_session.get(ProductMarketplaceMovement, sale["id"])
    assert original.quantity_delta == -20 and original.movement_type == SALE


async def test_undo_writes_a_compensating_oms_ledger_row(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, cvs, _ = await _herbal(db_session)
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        sale = (
            await _post(client, product.id, "amazon", "sale", 20, cv=cvs["Blue Packet"])
        ).json()["data"]
        await _undo(client, sale["id"])
    rows = (await db_session.execute(select(CatalogVariantStockAdjustment))).scalars().all()
    assert sorted(r.quantity_delta for r in rows) == [-20, 20]
    assert all(r.catalog_variant_id == cvs["Blue Packet"].id for r in rows)
    assert all(r.product_marketplace_movement_id is not None for r in rows)  # each linked


async def test_an_rto_can_be_undone_too(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, cvs, _ = await _herbal(db_session)
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        rto = (await _post(client, product.id, "flipkart", "rto", 2, cv=cvs["Red Packet"])).json()[
            "data"
        ]
        assert (await _undo(client, rto["id"])).status_code == 201
        data = await _platform(client, product.id)
        stock = await _stock(client, product.id)
    assert _row(_section(data, "Red Packet"), "flipkart")["current_stock"] == 0
    assert stock["available_boxes"] == 450


async def test_undo_cannot_be_applied_twice_or_to_a_reversal(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, cvs, _ = await _herbal(db_session)
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        sale = (
            await _post(client, product.id, "amazon", "sale", 20, cv=cvs["Gold Packet"])
        ).json()["data"]
        reversal = (await _undo(client, sale["id"])).json()["data"]
        assert (await _undo(client, sale["id"])).status_code == 422  # already undone
        assert (await _undo(client, reversal["id"])).status_code == 422  # a reversal
        assert (await _edit(client, sale["id"], 15)).status_code == 422  # undone => not editable
    assert await _count(db_session, ProductMarketplaceMovement) == 2  # nothing extra written


# --- Edit --------------------------------------------------------------------------


async def test_edit_appends_reversal_plus_replacement_and_keeps_the_original(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, cvs, _ = await _herbal(db_session)
    gold = cvs["Gold Packet"]
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        sale = (await _post(client, product.id, "amazon", "sale", 20, cv=gold)).json()["data"]
        edit = await _edit(client, sale["id"], 15, "correction")
        assert edit.status_code == 201
        replacement = edit.json()["data"]
        data = await _platform(client, product.id)
        stock = await _stock(client, product.id)
        history = await _history(client, product.id, gold)

    assert await _count(db_session, ProductMarketplaceMovement) == 3  # sale, reversal, sale
    # effective marketplace balance and OMS total reflect 15, not 20
    assert _row(_section(data, "Gold Packet"), "amazon")["current_stock"] == -15
    assert {g["name"]: g["available_boxes"] for g in stock["oms_variants"]}["Gold Packet"] == 45
    assert stock["available_boxes"] == 450 - 15
    # the replacement is a normal, still-editable sale marked "edited from 20"
    assert replacement["movement_type"] == "sale" and replacement["quantity_packets"] == 15
    assert replacement["replaces_movement_id"] == sale["id"]
    assert replacement["edited_from_packets"] == 20
    assert replacement["status"] == "active" and replacement["can_edit"] is True
    assert replacement["reason"] == "correction" and replacement["actor_user_id"] is not None
    assert replacement["platform"] == "amazon" and replacement["catalog_variant_id"] == str(gold.id)
    # the history shows all three events, separately
    by_id = {r["id"]: r for r in history}
    assert by_id[sale["id"]]["status"] == "edited"
    assert sorted(r["movement_type"] for r in history) == ["reversal", "sale", "sale"]
    reversal = next(r for r in history if r["movement_type"] == "reversal")
    assert reversal["quantity_delta"] == 20 and reversal["reverses_movement_id"] == sale["id"]
    original = await db_session.get(ProductMarketplaceMovement, sale["id"])
    assert original.quantity_packets == 20 and original.quantity_delta == -20  # untouched


async def test_an_rto_can_be_edited(db_session: AsyncSession, make_authenticated_client) -> None:
    product, cvs, _ = await _herbal(db_session)
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        rto = (await _post(client, product.id, "amazon", "rto", 5, cv=cvs["Red Packet"])).json()[
            "data"
        ]
        assert (await _edit(client, rto["id"], 2)).status_code == 201
        data = await _platform(client, product.id)
        stock = await _stock(client, product.id)
    assert _row(_section(data, "Red Packet"), "amazon")["current_stock"] == 2
    assert stock["available_boxes"] == 450 + 2


async def test_an_edited_movement_can_be_edited_again_via_its_replacement(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, cvs, _ = await _herbal(db_session)
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        sale = (
            await _post(client, product.id, "amazon", "sale", 20, cv=cvs["Gold Packet"])
        ).json()["data"]
        first = (await _edit(client, sale["id"], 15)).json()["data"]
        assert (await _edit(client, sale["id"], 10)).status_code == 422  # original is superseded
        assert (await _edit(client, first["id"], 10)).status_code == 201
        data = await _platform(client, product.id)
    assert _row(_section(data, "Gold Packet"), "amazon")["current_stock"] == -10


@pytest.mark.parametrize(
    ("packets", "reason", "expected"),
    [(20, "same", 422), (0, "x", 422), (-3, "x", 422), (15, "", 422), (15, "   ", 422)],
)
async def test_edit_validation(
    db_session: AsyncSession, make_authenticated_client, packets: int, reason: str, expected: int
) -> None:
    product, cvs, _ = await _herbal(db_session)
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        sale = (
            await _post(client, product.id, "amazon", "sale", 20, cv=cvs["Gold Packet"])
        ).json()["data"]
        response = await _edit(client, sale["id"], packets, reason)
    assert response.status_code == expected
    assert await _count(db_session, ProductMarketplaceMovement) == 1  # nothing written


async def test_undo_requires_a_reason(db_session: AsyncSession, make_authenticated_client) -> None:
    product, cvs, _ = await _herbal(db_session)
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        sale = (
            await _post(client, product.id, "amazon", "sale", 20, cv=cvs["Gold Packet"])
        ).json()["data"]
        assert (await _undo(client, sale["id"], "  ")).status_code == 422
    assert await _count(db_session, ProductMarketplaceMovement) == 1


async def test_edit_and_undo_need_inventory_manage_and_a_real_movement(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, cvs, _ = await _herbal(db_session)
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        sale = (
            await _post(client, product.id, "amazon", "sale", 20, cv=cvs["Gold Packet"])
        ).json()["data"]
        missing = "00000000-0000-0000-0000-000000000000"
        assert (await _undo(client, missing)).status_code == 404
        assert (await _edit(client, missing, 5)).status_code == 404
    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"], email="mkt-edit-ro@example.com"
    ) as client:
        assert (await _undo(client, sale["id"])).status_code == 403
        assert (await _edit(client, sale["id"], 5)).status_code == 403


async def test_edit_with_non_deterministic_conversion_writes_nothing(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, cvs, variants = await _herbal(db_session)
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        sale = (
            await _post(client, product.id, "amazon", "sale", 20, cv=cvs["Gold Packet"])
        ).json()["data"]
        gold_skus = [v for v in variants if v.catalog_variant_id == cvs["Gold Packet"].id]
        await ProductVariantRepository(db_session).update(gold_skus[0], pack_size=3)
        await db_session.commit()  # Gold's SKUs now disagree on pack_size
        response = await _edit(client, sale["id"], 15)
    assert response.status_code == 422
    assert await _count(db_session, ProductMarketplaceMovement) == 1
    assert await _count(db_session, CatalogVariantStockAdjustment) == 1


async def test_a_failed_oms_write_rolls_back_the_whole_edit(
    db_session: AsyncSession, monkeypatch
) -> None:
    product, cvs, _ = await _herbal(db_session)
    service = PlatformInventoryService(db_session)
    sale = await service.record_product_movement(
        product.id, platform=InventoryPlatform.AMAZON, movement_type=SALE,
        quantity_packets=20, reason=None, stock_date=ist_today(), actor=None,
        catalog_variant_id=cvs["Gold Packet"].id,
    )  # fmt: skip
    rows_before = await _count(db_session, ProductMarketplaceMovement)
    ledger_before = await _count(db_session, CatalogVariantStockAdjustment)

    original = InventoryService.apply_marketplace_stock_effect
    calls = {"n": 0}

    async def _fail_second(self, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:  # the replacement's OMS effect, after the reversal's succeeded
            raise RuntimeError("OMS ledger write failed")
        return await original(self, **kwargs)

    monkeypatch.setattr(InventoryService, "apply_marketplace_stock_effect", _fail_second)
    with pytest.raises(RuntimeError):
        await service.edit_movement(sale.id, quantity_packets=15, reason="fix", actor=None)

    assert calls["n"] == 2  # the reversal's write really did happen first...
    assert await _count(db_session, ProductMarketplaceMovement) == rows_before  # ...and was undone
    assert await _count(db_session, CatalogVariantStockAdjustment) == ledger_before


# --- legacy rows (recorded before OMS-stock linking: no OMS ledger row) ------------


async def _legacy_sale(session: AsyncSession, product_id, packets=20, balance_after=-20):
    return await PlatformInventoryService(session).product_movements.create(
        product_id=product_id, platform="amazon", movement_type=SALE,
        quantity_packets=packets, quantity_delta=-packets, quantity_after=balance_after,
        stock_date=ist_today(), reason="legacy", actor_user_id=None,
    )  # fmt: skip


async def test_undoing_a_legacy_row_never_double_counts_the_oms_total(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _, _ = await _single_cv(db_session, "LEGACY1")
    legacy = await _legacy_sale(db_session, product.id)
    await db_session.commit()
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        before = await _stock(client, product.id)
        assert (await _undo(client, legacy.id)).status_code == 201
        after = await _stock(client, product.id)
        data = await _platform(client, product.id)
    assert after["available_boxes"] == before["available_boxes"]  # the sale never touched OMS stock
    assert await _count(db_session, CatalogVariantStockAdjustment) == 0  # so nothing to compensate
    assert _row(data, "amazon")["current_stock"] == 0  # marketplace balance restored


async def test_editing_a_legacy_row_brings_the_oms_total_in_line(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _, _ = await _single_cv(db_session, "LEGACY2")
    legacy = await _legacy_sale(db_session, product.id, packets=20)
    await db_session.commit()
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        before = await _stock(client, product.id)
        assert (await _edit(client, legacy.id, 15)).status_code == 201
        after = await _stock(client, product.id)
        data = await _platform(client, product.id)
    assert after["available_boxes"] == before["available_boxes"] - 15  # a normal effect for 15
    assert _row(data, "amazon")["current_stock"] == -15


# --- monthly sold reflects the effective state ---------------------------------------


async def test_monthly_sold_uses_the_effective_quantity_after_edit_and_undo(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, cvs, _ = await _herbal(db_session)
    gold = cvs["Gold Packet"]
    async with await make_authenticated_client(db_session, permission_codes=PERMS) as client:
        a = (await _post(client, product.id, "amazon", "sale", 20, cv=gold)).json()["data"]
        b = (await _post(client, product.id, "flipkart", "sale", 5, cv=gold)).json()["data"]
        assert (
            _section(await _platform(client, product.id), "Gold Packet")["sold_this_month_packets"]
            == 25
        )
        await _edit(client, a["id"], 15)  # 20 -> 15
        assert (
            _section(await _platform(client, product.id), "Gold Packet")["sold_this_month_packets"]
            == 20
        )
        await _undo(client, b["id"])  # 5 undone
        assert (
            _section(await _platform(client, product.id), "Gold Packet")["sold_this_month_packets"]
            == 15
        )


async def test_marketplace_balance_is_never_restated_for_past_days(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """Corrections are dated the day they are made, so an earlier day's
    reconstructed balance is unchanged (it was true when recorded).
    """
    product, cvs, _ = await _herbal(db_session)
    gold = cvs["Gold Packet"]
    service = PlatformInventoryService(db_session)
    yesterday = ist_today() - timedelta(days=1)
    sale = await service.record_product_movement(
        product.id, platform=InventoryPlatform.AMAZON, movement_type=SALE,
        quantity_packets=20, reason=None, stock_date=yesterday, actor=None,
        catalog_variant_id=gold.id,
    )  # fmt: skip
    await service.undo_movement(sale.id, reason="oops", actor=None)

    past = await service.get_product_platform_stock(product.id, stock_date=yesterday)
    today = await service.get_product_platform_stock(product.id, stock_date=ist_today())
    past_row = next(
        r for r in next(v for v in past.variants if v.name == "Gold Packet").platforms
        if r.platform == "amazon"
    )  # fmt: skip
    today_row = next(
        r for r in next(v for v in today.variants if v.name == "Gold Packet").platforms
        if r.platform == "amazon"
    )  # fmt: skip
    assert past_row.current_stock == -20  # yesterday really was -20
    assert today_row.current_stock == 0  # today's effective balance is restored
