"""Multi-platform (Amazon/Flipkart/Blinkit/Meesho/Manual) marketplace
stock — a manual, date-wise ledger that sits alongside the existing
Shopify/OMS inventory system, never inside it. See
`app.services.platform_inventory_service` for the design.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from app.core.exceptions import ValidationError
from app.core.timezone import ist_day_bounds_for_date, ist_today
from app.integrations.shiprocket.sync import apply_tracking_event
from app.models.enums import (
    InventoryMovementType,
    PaymentType,
    PlatformStockMovementType,
    ProductMarketplaceMovementType,
    ShipmentStatus,
)
from app.models.platform_inventory import InventoryPlatform
from app.repositories.inventory import InventoryMovementRepository
from app.repositories.product import ProductRepository, ProductVariantRepository
from app.schemas.common import PageParams
from app.schemas.order import OrderItemCreateRequest
from app.schemas.platform_inventory import ProductPlatformStockResponse
from app.services.inventory_service import InventoryService
from app.services.order_service import OrderService
from app.services.platform_inventory_service import PlatformInventoryService
from app.services.rto_service import RTOService
from app.services.shipment_service import ShipmentService
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _make_variant(
    session: AsyncSession, *, sku: str, available_quantity: int = 10, shopify_order_id=None
):
    product, _ = await ProductRepository(session).upsert_by_external_id(
        source_system="shopify", external_id=f"prod-{sku}", title=f"Product {sku}"
    )
    variant, _ = await ProductVariantRepository(session).upsert_by_external_id(
        source_system="shopify",
        external_id=f"var-{sku}",
        product_id=product.id,
        sku=sku,
        price=Decimal("100.00"),
        available_quantity=available_quantity,
    )
    await session.commit()
    return product, variant


async def _make_order_with_item(
    session: AsyncSession, *, order_number: str, sku: str, quantity: int, product_variant_id
):
    return await OrderService(session).create_order(
        actor=None,
        order_number=order_number,
        customer_id=None,
        order_datetime=datetime.now(UTC),
        currency="INR",
        payment_type=PaymentType.PREPAID,
        shipping_charge=Decimal("0"),
        notes=None,
        items=[
            OrderItemCreateRequest(
                product_variant_id=product_variant_id,
                sku=sku,
                product_name=f"Product {sku}",
                quantity=quantity,
                unit_price=Decimal("100.00"),
            )
        ],
    )


async def _make_shipment(session: AsyncSession, *, order_id, awb: str):
    return await ShipmentService(session).create_shipment(
        actor=None, order_id=order_id, awb=awb, courier_id=None, expected_delivery_date=None
    )


async def _add_shopify_movement(
    session: AsyncSession,
    *,
    variant_id,
    movement_type: InventoryMovementType,
    quantity_delta: int,
    quantity_after: int,
    on_date: date,
    hour: int = 12,
) -> None:
    """Directly inserts a historical `InventoryMovement` row dated to a
    specific IST calendar day (via the exact `ist_day_bounds_for_date`
    helper the fix itself uses, so the test can't disagree with the
    implementation about what "that IST day" means) -- simulates real
    Shopify dispatch/RTO/manual-adjustment history predating "today",
    which `apply_tracking_event`'s real flow always dates to "now".
    """
    day_start, _ = ist_day_bounds_for_date(on_date)
    created_at = day_start + timedelta(hours=hour)
    await InventoryMovementRepository(session).create(
        product_variant_id=variant_id,
        movement_type=movement_type,
        quantity_delta=quantity_delta,
        quantity_after=quantity_after,
        created_at=created_at,
    )
    await session.commit()


def _shopify_row(summary: ProductPlatformStockResponse):
    rows = {row.platform: row for row in summary.platforms}
    return rows["shopify"]


def _tracking_event(*, status: ShipmentStatus) -> dict:
    return {
        "event_timestamp": datetime.now(UTC),
        "external_event_id": f"evt-{status.value}",
        "status": status.value,
        "mapped_status": status,
        "location": "Hub",
        "description": status.value,
        "courier_name": "Test Courier",
        "raw_payload": None,
    }


def _page_params() -> PageParams:
    return PageParams(page=1, page_size=50)


# --- Add Stock: additive, per-platform -------------------------------


async def test_add_amazon_stock_is_additive(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-AMZ-1")
    service = PlatformInventoryService(db_session)

    first = await service.record_movement(
        variant.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=500,
        reason="Initial warehouse stock",
        stock_date=None,
        actor=None,
    )
    assert first.quantity_delta == 500
    assert first.quantity_after == 500

    second = await service.record_movement(
        variant.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=100,
        reason=None,
        stock_date=None,
        actor=None,
    )
    # current(500) + 100 = 600, never the client's raw "100" as a total.
    assert second.quantity_delta == 100
    assert second.quantity_after == 600


async def test_add_flipkart_stock_is_additive(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-FLP-1")
    service = PlatformInventoryService(db_session)
    await service.record_movement(
        variant.id,
        platform=InventoryPlatform.FLIPKART,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=350,
        reason=None,
        stock_date=None,
        actor=None,
    )
    second = await service.record_movement(
        variant.id,
        platform=InventoryPlatform.FLIPKART,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=10,
        reason=None,
        stock_date=None,
        actor=None,
    )
    assert second.quantity_after == 360


async def test_add_blinkit_stock_is_additive(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-BLK-1")
    service = PlatformInventoryService(db_session)
    await service.record_movement(
        variant.id,
        platform=InventoryPlatform.BLINKIT,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=200,
        reason=None,
        stock_date=None,
        actor=None,
    )
    second = await service.record_movement(
        variant.id,
        platform=InventoryPlatform.BLINKIT,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=15,
        reason=None,
        stock_date=None,
        actor=None,
    )
    assert second.quantity_after == 215


async def test_add_meesho_stock_is_additive(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-MSH-1")
    service = PlatformInventoryService(db_session)
    await service.record_movement(
        variant.id,
        platform=InventoryPlatform.MEESHO,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=150,
        reason=None,
        stock_date=None,
        actor=None,
    )
    second = await service.record_movement(
        variant.id,
        platform=InventoryPlatform.MEESHO,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=5,
        reason=None,
        stock_date=None,
        actor=None,
    )
    assert second.quantity_after == 155


async def test_add_stock_calculates_from_actual_current_stock_not_a_hardcoded_value(
    db_session: AsyncSession,
) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-CALC-1")
    service = PlatformInventoryService(db_session)
    await service.record_movement(
        variant.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=37,
        reason=None,
        stock_date=None,
        actor=None,
    )
    result = await service.record_movement(
        variant.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=13,
        reason=None,
        stock_date=None,
        actor=None,
    )
    assert result.quantity_after == 50  # 37 + 13, never a hardcoded/guessed number


async def test_add_stock_rejects_negative_quantity(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-NEG-1")
    service = PlatformInventoryService(db_session)
    with pytest.raises(ValidationError):
        await service.record_movement(
            variant.id,
            platform=InventoryPlatform.AMAZON,
            movement_type=PlatformStockMovementType.STOCK_ADDED,
            quantity=-5,
            reason=None,
            stock_date=None,
            actor=None,
        )


async def test_add_stock_rejects_zero_quantity(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-ZERO-1")
    service = PlatformInventoryService(db_session)
    with pytest.raises(ValidationError):
        await service.record_movement(
            variant.id,
            platform=InventoryPlatform.AMAZON,
            movement_type=PlatformStockMovementType.STOCK_ADDED,
            quantity=0,
            reason=None,
            stock_date=None,
            actor=None,
        )


async def test_reason_is_optional_for_platform_stock_unlike_shopify_adjustment(
    db_session: AsyncSession,
) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-REASON-1")
    service = PlatformInventoryService(db_session)
    movement = await service.record_movement(
        variant.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=10,
        reason=None,
        stock_date=None,
        actor=None,
    )
    assert movement.reason is None  # never required to raise ValidationError


async def test_unknown_platform_is_rejected(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-UNK-1")
    service = PlatformInventoryService(db_session)
    with pytest.raises(ValidationError):
        await service.record_movement(
            variant.id,
            platform="ebay",
            movement_type=PlatformStockMovementType.STOCK_ADDED,
            quantity=10,
            reason=None,
            stock_date=None,
            actor=None,
        )


# --- Record deduction (Sold/Dispatched) -------------------------------


async def test_record_deduction_subtracts_from_current_stock(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-DED-1")
    service = PlatformInventoryService(db_session)
    await service.record_movement(
        variant.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=500,
        reason=None,
        stock_date=None,
        actor=None,
    )
    result = await service.record_movement(
        variant.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_DEDUCTED,
        quantity=18,
        reason="Marketplace sale",
        stock_date=None,
        actor=None,
    )
    assert result.quantity_delta == -18
    assert result.quantity_after == 482


async def test_deduction_below_zero_is_rejected(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-DED-NEG-1")
    service = PlatformInventoryService(db_session)
    await service.record_movement(
        variant.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=10,
        reason=None,
        stock_date=None,
        actor=None,
    )
    with pytest.raises(ValidationError):
        await service.record_movement(
            variant.id,
            platform=InventoryPlatform.AMAZON,
            movement_type=PlatformStockMovementType.STOCK_DEDUCTED,
            quantity=11,
            reason=None,
            stock_date=None,
            actor=None,
        )


# --- Platform / variant isolation -------------------------------------


async def test_platform_isolation_amazon_stock_never_affects_flipkart(
    db_session: AsyncSession,
) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-ISO-1")
    service = PlatformInventoryService(db_session)
    await service.record_movement(
        variant.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=999,
        reason=None,
        stock_date=None,
        actor=None,
    )
    latest_flipkart = await service.movements.get_latest_as_of(
        product_variant_id=variant.id, platform=InventoryPlatform.FLIPKART, as_of=ist_today()
    )
    assert latest_flipkart is None  # Flipkart is untouched by an Amazon movement


async def test_variant_isolation_different_skus_never_share_platform_stock(
    db_session: AsyncSession,
) -> None:
    _, variant_a = await _make_variant(db_session, sku="PLAT-VARA-1")
    _, variant_b = await _make_variant(db_session, sku="PLAT-VARB-1")
    service = PlatformInventoryService(db_session)
    await service.record_movement(
        variant_a.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=100,
        reason=None,
        stock_date=None,
        actor=None,
    )
    latest_b = await service.movements.get_latest_as_of(
        product_variant_id=variant_b.id, platform=InventoryPlatform.AMAZON, as_of=ist_today()
    )
    assert latest_b is None  # variant B's Amazon stock is completely separate from A's


async def test_shopify_stock_is_unaffected_by_platform_stock_operations(
    db_session: AsyncSession,
) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-SHOP-1", available_quantity=42)
    service = PlatformInventoryService(db_session)
    await service.record_movement(
        variant.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=500,
        reason=None,
        stock_date=None,
        actor=None,
    )
    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.available_quantity == 42  # completely untouched


# --- Historical dates: preserved, never overwritten --------------------


async def test_historical_dates_remain_separate_and_are_never_overwritten(
    db_session: AsyncSession,
) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-HIST-1")
    service = PlatformInventoryService(db_session)
    day1 = date(2026, 9, 11)
    day2 = date(2026, 9, 12)

    await service.record_movement(
        variant.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=500,
        reason=None,
        stock_date=day1,
        actor=None,
    )
    await service.record_movement(
        variant.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=30,
        reason=None,
        stock_date=day2,
        actor=None,
    )

    as_of_day1 = await service.movements.get_latest_as_of(
        product_variant_id=variant.id, platform=InventoryPlatform.AMAZON, as_of=day1
    )
    as_of_day2 = await service.movements.get_latest_as_of(
        product_variant_id=variant.id, platform=InventoryPlatform.AMAZON, as_of=day2
    )
    assert as_of_day1.quantity_after == 500  # day 1's balance is untouched by day 2's entry
    assert as_of_day2.quantity_after == 530


async def test_todays_stock_movement_calculation_added_and_deducted(
    db_session: AsyncSession,
) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-TODAY-1")
    service = PlatformInventoryService(db_session)
    today = ist_today()

    await service.record_movement(
        variant.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=50,
        reason=None,
        stock_date=today,
        actor=None,
    )
    await service.record_movement(
        variant.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_DEDUCTED,
        quantity=20,
        reason=None,
        stock_date=today,
        actor=None,
    )

    added, deducted = await service.movements.sum_for_date(
        product_variant_id=variant.id, platform=InventoryPlatform.AMAZON, stock_date=today
    )
    assert added == 50
    assert deducted == 20


# --- Product-level platform stock summary -------------------------------


async def test_product_platform_stock_summary_shows_opening_added_deducted_current(
    db_session: AsyncSession,
) -> None:
    product, variant = await _make_variant(db_session, sku="PLAT-SUM-1")
    service = PlatformInventoryService(db_session)
    yesterday = ist_today() - timedelta(days=1)
    today = ist_today()

    await service.record_product_movement(
        product.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=ProductMarketplaceMovementType.RTO,
        quantity_packets=500,
        reason=None,
        stock_date=yesterday,
        actor=None,
    )
    await service.record_product_movement(
        product.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=ProductMarketplaceMovementType.RTO,
        quantity_packets=50,
        reason=None,
        stock_date=today,
        actor=None,
    )
    await service.record_product_movement(
        product.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=ProductMarketplaceMovementType.SALE,
        quantity_packets=20,
        reason=None,
        stock_date=today,
        actor=None,
    )

    summary = await service.get_product_platform_stock(product.id, stock_date=today)
    rows = {row.platform: row for row in summary.platforms}

    amazon = rows[InventoryPlatform.AMAZON]
    assert amazon.opening_stock == 500
    assert amazon.stock_added == 50
    assert amazon.stock_deducted == 20
    assert amazon.current_stock == 530  # 500 + 50 - 20
    assert amazon.is_automatic is False

    # Every platform is present even with zero activity -- never omitted.
    assert set(rows) == {"shopify", *InventoryPlatform.ALL}
    flipkart = rows[InventoryPlatform.FLIPKART]
    assert flipkart.opening_stock == 0
    assert flipkart.current_stock == 0

    shopify = rows["shopify"]
    assert shopify.is_automatic is True
    assert shopify.opening_stock is None  # no historical Shopify balance snapshot exists
    assert shopify.current_stock == 10  # the variant's real, LIVE available_quantity


async def test_backfilling_a_past_date_does_not_change_a_later_dates_own_balance(
    db_session: AsyncSession,
) -> None:
    """Explicit documentation of the out-of-order-backfill limitation:
    entering data chronologically is fully supported; a later backfill
    into an EARLIER date does not retroactively recompute a day that was
    already entered after it -- no silent, invented cascading recompute.
    """
    _, variant = await _make_variant(db_session, sku="PLAT-BACKFILL-1")
    service = PlatformInventoryService(db_session)
    day1 = date(2026, 9, 11)
    day2 = date(2026, 9, 12)

    await service.record_movement(
        variant.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=530,
        reason=None,
        stock_date=day2,
        actor=None,
    )
    # Backfilling day1 AFTER day2 already exists.
    await service.record_movement(
        variant.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=500,
        reason=None,
        stock_date=day1,
        actor=None,
    )

    as_of_day2 = await service.movements.get_latest_as_of(
        product_variant_id=variant.id, platform=InventoryPlatform.AMAZON, as_of=day2
    )
    assert as_of_day2.quantity_after == 530  # day2's own recorded balance, unchanged


# --- Unified movement history -------------------------------------------


async def test_movement_history_merges_platform_and_shopify_rows_sorted_by_time(
    db_session: AsyncSession,
) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-HIST-MERGE-1", available_quantity=100)
    service = PlatformInventoryService(db_session)
    order = await _make_order_with_item(
        db_session,
        order_number="PLAT-HIST-ORD-1",
        sku="PLAT-HIST-MERGE-1",
        quantity=1,
        product_variant_id=variant.id,
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="PLAT-HIST-AWB-1")
    await apply_tracking_event(
        db_session,
        shipment,
        _tracking_event(status=ShipmentStatus.PICKED_UP),
        shipment_service=ShipmentService(db_session),
        rto_service=RTOService(db_session),
        inventory_service=InventoryService(db_session),
    )
    await service.record_movement(
        variant.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=50,
        reason="Warehouse stock",
        stock_date=None,
        actor=None,
    )

    rows, total = await service.get_movement_history(
        variant.id, platform=None, date_from=None, date_to=None, page_params=_page_params()
    )
    assert total == 2
    platforms_seen = {row.platform for row in rows}
    assert platforms_seen == {"shopify", InventoryPlatform.AMAZON}


async def test_movement_history_filtered_to_one_platform_excludes_shopify(
    db_session: AsyncSession,
) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-HIST-FILTER-1")
    service = PlatformInventoryService(db_session)
    await service.record_movement(
        variant.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=50,
        reason=None,
        stock_date=None,
        actor=None,
    )
    rows, total = await service.get_movement_history(
        variant.id,
        platform=InventoryPlatform.AMAZON,
        date_from=None,
        date_to=None,
        page_params=_page_params(),
    )
    assert total == 1
    assert rows[0].platform == InventoryPlatform.AMAZON


# --- Shipment / in-transit summary (reuses existing dispatch+shipment data) --


async def test_shipment_summary_counts_in_transit_boxes_from_existing_dispatch_data(
    db_session: AsyncSession,
) -> None:
    product, variant = await _make_variant(db_session, sku="PLAT-SHIP-1", available_quantity=20)
    order = await _make_order_with_item(
        db_session,
        order_number="PLAT-SHIP-ORD-1",
        sku="PLAT-SHIP-1",
        quantity=3,
        product_variant_id=variant.id,
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="PLAT-SHIP-AWB-1")
    for status in (ShipmentStatus.PICKED_UP, ShipmentStatus.IN_TRANSIT):
        await apply_tracking_event(
            db_session,
            shipment,
            _tracking_event(status=status),
            shipment_service=ShipmentService(db_session),
            rto_service=RTOService(db_session),
            inventory_service=InventoryService(db_session),
        )

    service = PlatformInventoryService(db_session)
    summary = await service.get_product_shipment_summary(product.id, stock_date=ist_today())
    assert summary.in_transit == 3
    assert summary.out_for_delivery == 0
    assert summary.rto == 0
    assert len(summary.variants) == 1
    assert summary.variants[0].sku == "PLAT-SHIP-1"


async def test_shipment_summary_never_fabricates_counts_for_a_product_with_no_shipments(
    db_session: AsyncSession,
) -> None:
    product, _ = await _make_variant(db_session, sku="PLAT-SHIP-EMPTY-1")
    service = PlatformInventoryService(db_session)
    summary = await service.get_product_shipment_summary(product.id, stock_date=ist_today())
    assert summary.in_transit == 0
    assert summary.out_for_delivery == 0
    assert summary.delivered_on_date == 0
    assert summary.rto == 0


# --- HTTP endpoints: RBAC ------------------------------------------------


async def test_platform_stock_endpoint_requires_inventory_read(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _ = await _make_variant(db_session, sku="PLAT-EP-READ-1")
    async with await make_authenticated_client(
        db_session, permission_codes=["analytics.read"], email="noperm-read@example.com"
    ) as client:
        response = await client.get(f"/api/v1/inventory/products/{product.id}/platform-stock")
        assert response.status_code == 403


async def test_add_platform_stock_endpoint_requires_inventory_manage(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-EP-MANAGE-1")
    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"], email="readonly@example.com"
    ) as client:
        response = await client.post(
            f"/api/v1/inventory/stock/{variant.id}/platform-stock/movements",
            json={"platform": "amazon", "movement_type": "stock_added", "quantity": 10},
        )
        assert response.status_code == 403


async def test_add_platform_stock_endpoint_end_to_end(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-EP-E2E-1")
    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read", "inventory.manage"]
    ) as client:
        response = await client.post(
            f"/api/v1/inventory/stock/{variant.id}/platform-stock/movements",
            json={"platform": "amazon", "movement_type": "stock_added", "quantity": 100},
        )
        assert response.status_code == 201
        body = response.json()["data"]
        assert body["platform"] == "amazon"
        assert body["quantity_delta"] == 100
        assert body["quantity_after"] == 100

        # never trust a client-supplied total -- posting a bare "50" adds
        # to 100, it doesn't set the balance to 50.
        second = await client.post(
            f"/api/v1/inventory/stock/{variant.id}/platform-stock/movements",
            json={"platform": "amazon", "movement_type": "stock_added", "quantity": 50},
        )
        assert second.json()["data"]["quantity_after"] == 150


async def test_add_platform_stock_endpoint_rejects_negative_quantity_with_422(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-EP-422-1")
    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read", "inventory.manage"]
    ) as client:
        response = await client.post(
            f"/api/v1/inventory/stock/{variant.id}/platform-stock/movements",
            json={"platform": "amazon", "movement_type": "stock_added", "quantity": -5},
        )
        assert response.status_code == 422  # Pydantic Field(gt=0) rejects at the schema layer


async def test_shipment_summary_endpoint_requires_inventory_read(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _ = await _make_variant(db_session, sku="PLAT-EP-SHIP-1")
    async with await make_authenticated_client(
        db_session, permission_codes=["analytics.read"], email="noperm-ship@example.com"
    ) as client:
        response = await client.get(f"/api/v1/inventory/products/{product.id}/shipment-summary")
        assert response.status_code == 403


# --- BUG FIX: Shopify date filtering -------------------------------------
# Root cause: the Shopify row's `current_stock` always read the LIVE
# `ProductVariant.available_quantity` regardless of `stock_date`, and
# `opening_stock` was hardcoded `None` even when the ledger could
# reconstruct it. These tests lock in the fix: for a past date, Shopify's
# balance is reconstructed from `InventoryMovement.quantity_after` (the
# same technique already used for the manual platform ledger); "today"
# still uses the true live column.


async def test_shopify_today_returns_the_live_current_stock(db_session: AsyncSession) -> None:
    """TEST A."""
    product, variant = await _make_variant(db_session, sku="SHOP-TODAY-1", available_quantity=1200)
    # A stale old movement exists -- "today" must use the live column,
    # never this ledger row's balance.
    await _add_shopify_movement(
        db_session,
        variant_id=variant.id,
        movement_type=InventoryMovementType.DISPATCH,
        quantity_delta=-50,
        quantity_after=500,
        on_date=ist_today() - timedelta(days=5),
    )

    service = PlatformInventoryService(db_session)
    summary = await service.get_product_platform_stock(product.id, stock_date=ist_today())
    shopify = _shopify_row(summary)

    assert shopify.current_stock == 1200


async def test_shopify_previous_date_returns_historical_stock_not_zero(
    db_session: AsyncSession,
) -> None:
    """TEST B -- the reported bug: a previous date must show the real
    historical balance, never 0/blank.
    """
    product, variant = await _make_variant(db_session, sku="SHOP-HIST-1", available_quantity=1200)
    target_date = ist_today() - timedelta(days=2)
    await _add_shopify_movement(
        db_session,
        variant_id=variant.id,
        movement_type=InventoryMovementType.DISPATCH,
        quantity_delta=-35,
        quantity_after=955,
        on_date=target_date,
        hour=10,
    )
    # A LATER movement (after target_date, before today) exists too --
    # must never leak into target_date's own balance.
    await _add_shopify_movement(
        db_session,
        variant_id=variant.id,
        movement_type=InventoryMovementType.DISPATCH,
        quantity_delta=-45,
        quantity_after=910,
        on_date=ist_today() - timedelta(days=1),
        hour=9,
    )

    service = PlatformInventoryService(db_session)
    summary = await service.get_product_platform_stock(product.id, stock_date=target_date)
    shopify = _shopify_row(summary)

    assert shopify.current_stock == 955  # not 0, not the live 1200, not the later 910


async def test_shopify_stock_added_counts_only_positive_movements_on_the_selected_date(
    db_session: AsyncSession,
) -> None:
    """TEST C."""
    product, variant = await _make_variant(db_session, sku="SHOP-ADD-1", available_quantity=1200)
    target_date = ist_today() - timedelta(days=3)
    other_date = ist_today() - timedelta(days=1)

    await _add_shopify_movement(
        db_session,
        variant_id=variant.id,
        movement_type=InventoryMovementType.RTO_RESTOCK,
        quantity_delta=20,
        quantity_after=920,
        on_date=target_date,
        hour=11,
    )
    # A positive movement on a DIFFERENT date must not be counted.
    await _add_shopify_movement(
        db_session,
        variant_id=variant.id,
        movement_type=InventoryMovementType.RTO_RESTOCK,
        quantity_delta=15,
        quantity_after=935,
        on_date=other_date,
        hour=11,
    )

    service = PlatformInventoryService(db_session)
    summary = await service.get_product_platform_stock(product.id, stock_date=target_date)
    shopify = _shopify_row(summary)

    assert shopify.stock_added == 20
    assert shopify.stock_deducted == 0


async def test_shopify_sold_deducted_counts_only_negative_movements_on_the_selected_date(
    db_session: AsyncSession,
) -> None:
    """TEST D."""
    product, variant = await _make_variant(db_session, sku="SHOP-DED-1", available_quantity=1200)
    target_date = ist_today() - timedelta(days=3)
    other_date = ist_today() - timedelta(days=1)

    await _add_shopify_movement(
        db_session,
        variant_id=variant.id,
        movement_type=InventoryMovementType.DISPATCH,
        quantity_delta=-35,
        quantity_after=865,
        on_date=target_date,
        hour=15,
    )
    # A negative movement on a DIFFERENT date must not be counted.
    await _add_shopify_movement(
        db_session,
        variant_id=variant.id,
        movement_type=InventoryMovementType.DISPATCH,
        quantity_delta=-12,
        quantity_after=853,
        on_date=other_date,
        hour=15,
    )

    service = PlatformInventoryService(db_session)
    summary = await service.get_product_platform_stock(product.id, stock_date=target_date)
    shopify = _shopify_row(summary)

    assert shopify.stock_deducted == 35
    assert shopify.stock_added == 0


async def test_shopify_manual_adjustment_counts_toward_added_and_deducted_too(
    db_session: AsyncSession,
) -> None:
    """`stock_added`/`stock_deducted` must include EVERY movement type
    with the right sign, not just DISPATCH/RTO_RESTOCK -- a staff manual
    adjustment through the existing (non-platform) Add Stock action on
    the selected date must be reflected too.
    """
    product, variant = await _make_variant(db_session, sku="SHOP-MANUAL-1", available_quantity=1200)
    target_date = ist_today() - timedelta(days=2)
    await _add_shopify_movement(
        db_session,
        variant_id=variant.id,
        movement_type=InventoryMovementType.MANUAL_ADJUSTMENT,
        quantity_delta=100,
        quantity_after=1100,
        on_date=target_date,
        hour=17,
    )

    service = PlatformInventoryService(db_session)
    summary = await service.get_product_platform_stock(product.id, stock_date=target_date)
    shopify = _shopify_row(summary)

    assert shopify.stock_added == 100
    assert shopify.current_stock == 1100


async def test_shopify_current_stock_is_the_balance_at_the_end_of_the_selected_date(
    db_session: AsyncSession,
) -> None:
    """TEST E -- with two movements on the SAME day, `current_stock` must
    reflect the LAST one (end-of-day balance), not the first.
    """
    product, variant = await _make_variant(db_session, sku="SHOP-EOD-1", available_quantity=1200)
    target_date = ist_today() - timedelta(days=2)
    await _add_shopify_movement(
        db_session,
        variant_id=variant.id,
        movement_type=InventoryMovementType.DISPATCH,
        quantity_delta=-10,
        quantity_after=990,
        on_date=target_date,
        hour=9,
    )
    await _add_shopify_movement(
        db_session,
        variant_id=variant.id,
        movement_type=InventoryMovementType.DISPATCH,
        quantity_delta=-35,
        quantity_after=955,
        on_date=target_date,
        hour=15,
    )

    service = PlatformInventoryService(db_session)
    summary = await service.get_product_platform_stock(product.id, stock_date=target_date)
    shopify = _shopify_row(summary)

    assert shopify.current_stock == 955  # the 15:00 movement's balance, not the 09:00 one
    assert shopify.stock_deducted == 45  # both movements' magnitude combined


async def test_date_with_no_shopify_movement_still_reconstructs_the_correct_balance(
    db_session: AsyncSession,
) -> None:
    """TEST F -- a day with zero movements of its own must still carry
    forward the balance from the most recent PRIOR movement, never 0.
    """
    product, variant = await _make_variant(db_session, sku="SHOP-CARRY-1", available_quantity=1200)
    earlier_date = ist_today() - timedelta(days=5)
    quiet_date = ist_today() - timedelta(days=3)  # no movement happens on this date

    await _add_shopify_movement(
        db_session,
        variant_id=variant.id,
        movement_type=InventoryMovementType.DISPATCH,
        quantity_delta=-300,
        quantity_after=900,
        on_date=earlier_date,
        hour=10,
    )

    service = PlatformInventoryService(db_session)
    summary = await service.get_product_platform_stock(product.id, stock_date=quiet_date)
    shopify = _shopify_row(summary)

    assert shopify.current_stock == 900  # carried forward from the earlier movement
    assert shopify.stock_added == 0
    assert shopify.stock_deducted == 0


async def test_date_before_the_first_ever_movement_is_reported_as_unavailable_not_zero(
    db_session: AsyncSession,
) -> None:
    """TEST F (continued) / Requirement 11 -- when the ledger genuinely
    cannot reconstruct a date (it predates the variant's first-ever
    movement), the balance must be reported as unavailable (`None`),
    never silently guessed as 0.
    """
    product, variant = await _make_variant(db_session, sku="SHOP-NODATA-1", available_quantity=1200)
    first_movement_date = ist_today() - timedelta(days=2)
    before_history_date = ist_today() - timedelta(days=10)

    await _add_shopify_movement(
        db_session,
        variant_id=variant.id,
        movement_type=InventoryMovementType.DISPATCH,
        quantity_delta=-300,
        quantity_after=900,
        on_date=first_movement_date,
        hour=10,
    )

    service = PlatformInventoryService(db_session)
    summary = await service.get_product_platform_stock(product.id, stock_date=before_history_date)
    shopify = _shopify_row(summary)

    assert shopify.current_stock is None
    assert shopify.opening_stock is None


async def test_ist_midnight_boundary_buckets_shopify_movements_to_the_correct_day(
    db_session: AsyncSession,
) -> None:
    """TEST G -- a movement 5 minutes before IST midnight belongs to the
    earlier day; 5 minutes after belongs to the next day. Never bucketed
    by the raw UTC calendar date.
    """
    product, variant = await _make_variant(db_session, sku="SHOP-IST-1", available_quantity=1200)
    day = ist_today() - timedelta(days=3)
    next_day = day + timedelta(days=1)
    _, day_end = ist_day_bounds_for_date(day)

    await InventoryMovementRepository(db_session).create(
        product_variant_id=variant.id,
        movement_type=InventoryMovementType.DISPATCH,
        quantity_delta=-5,
        quantity_after=995,
        created_at=day_end - timedelta(minutes=5),
    )
    await InventoryMovementRepository(db_session).create(
        product_variant_id=variant.id,
        movement_type=InventoryMovementType.DISPATCH,
        quantity_delta=-3,
        quantity_after=992,
        created_at=day_end + timedelta(minutes=5),
    )
    await db_session.commit()

    service = PlatformInventoryService(db_session)
    day_summary = await service.get_product_platform_stock(product.id, stock_date=day)
    next_day_summary = await service.get_product_platform_stock(product.id, stock_date=next_day)

    assert _shopify_row(day_summary).stock_deducted == 5  # only the pre-midnight movement
    assert _shopify_row(day_summary).current_stock == 995
    assert _shopify_row(next_day_summary).stock_deducted == 3  # only the post-midnight movement
    assert _shopify_row(next_day_summary).current_stock == 992


async def test_amazon_platform_data_is_unaffected_by_the_shopify_fix(
    db_session: AsyncSession,
) -> None:
    """TEST H -- the manual marketplace ledger's own calculation path was
    never touched by this fix; verify it end-to-end alongside a Shopify
    historical read on the same product/date.
    """
    product, variant = await _make_variant(
        db_session, sku="SHOP-AMZ-ISO-1", available_quantity=1200
    )
    target_date = ist_today() - timedelta(days=2)
    await _add_shopify_movement(
        db_session,
        variant_id=variant.id,
        movement_type=InventoryMovementType.DISPATCH,
        quantity_delta=-35,
        quantity_after=955,
        on_date=target_date,
        hour=10,
    )

    service = PlatformInventoryService(db_session)
    await service.record_product_movement(
        product.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=ProductMarketplaceMovementType.RTO,
        quantity_packets=500,
        reason=None,
        stock_date=target_date,
        actor=None,
    )

    summary = await service.get_product_platform_stock(product.id, stock_date=target_date)
    rows = {row.platform: row for row in summary.platforms}

    assert rows["shopify"].current_stock == 955
    assert rows[InventoryPlatform.AMAZON].current_stock == 500
    assert rows[InventoryPlatform.AMAZON].opening_stock == 0
    assert rows[InventoryPlatform.FLIPKART].current_stock == 0  # untouched, still a real 0 not None


# --- Product-level marketplace movements (no SKU selection) --------------
# See app.models.platform_inventory.ProductMarketplaceMovement and
# PlatformInventoryService.record_product_movement.


async def _make_product_with_variants(session: AsyncSession, *, key: str, variants: list[dict]):
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
            price=Decimal("100.00"),
            available_quantity=spec.get("available_quantity", 0),
            pack_size=spec.get("pack_size", 1),
            packets_per_box=spec.get("packets_per_box", 1),
        )
        made.append(variant)
    await session.commit()
    return product, made


async def test_amazon_sale_of_20_packets_creates_product_level_negative_adjustment(
    db_session: AsyncSession,
) -> None:
    product, _ = await _make_variant(db_session, sku="MKT-SALE-1")
    service = PlatformInventoryService(db_session)
    today = ist_today()

    await service.record_product_movement(
        product.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=ProductMarketplaceMovementType.SALE,
        quantity_packets=20,
        reason="Marketplace sale",
        stock_date=today,
        actor=None,
    )

    summary = await service.get_product_platform_stock(product.id, stock_date=today)
    amazon = {row.platform: row for row in summary.platforms}[InventoryPlatform.AMAZON]
    assert amazon.current_stock == -20
    assert amazon.stock_deducted == 20
    assert amazon.stock_added == 0


async def test_flipkart_sale_works_the_same_way_as_amazon(db_session: AsyncSession) -> None:
    product, _ = await _make_variant(db_session, sku="MKT-SALE-FK-1")
    service = PlatformInventoryService(db_session)
    today = ist_today()

    await service.record_product_movement(
        product.id,
        platform=InventoryPlatform.FLIPKART,
        movement_type=ProductMarketplaceMovementType.SALE,
        quantity_packets=7,
        reason=None,
        stock_date=today,
        actor=None,
    )

    summary = await service.get_product_platform_stock(product.id, stock_date=today)
    rows = {row.platform: row for row in summary.platforms}
    assert rows[InventoryPlatform.FLIPKART].current_stock == -7
    # untouched -- the platform is preserved, never leaked into another row
    assert rows[InventoryPlatform.AMAZON].current_stock == 0


async def test_amazon_rto_of_2_packets_creates_product_level_positive_adjustment(
    db_session: AsyncSession,
) -> None:
    product, _ = await _make_variant(db_session, sku="MKT-RTO-1")
    service = PlatformInventoryService(db_session)
    today = ist_today()

    movement = await service.record_product_movement(
        product.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=ProductMarketplaceMovementType.RTO,
        quantity_packets=2,
        reason="Customer return",
        stock_date=today,
        actor=None,
    )
    assert movement.quantity_delta == 2
    assert movement.quantity_after == 2

    summary = await service.get_product_platform_stock(product.id, stock_date=today)
    amazon = {row.platform: row for row in summary.platforms}[InventoryPlatform.AMAZON]
    assert amazon.current_stock == 2
    assert amazon.stock_added == 2  # RTO counts toward "added", same bucket as stock_added


async def test_sale_and_rto_are_separate_history_events_never_merged(
    db_session: AsyncSession,
) -> None:
    product, _ = await _make_variant(db_session, sku="MKT-HIST-1")
    service = PlatformInventoryService(db_session)
    today = ist_today()

    await service.record_product_movement(
        product.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=ProductMarketplaceMovementType.SALE,
        quantity_packets=20,
        reason="Marketplace sale",
        stock_date=today,
        actor=None,
    )
    await service.record_product_movement(
        product.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=ProductMarketplaceMovementType.RTO,
        quantity_packets=2,
        reason="Customer return",
        stock_date=today,
        actor=None,
    )

    rows, total = await service.get_product_marketplace_history(
        product.id, platform=None, date_from=None, date_to=None, page_params=_page_params()
    )
    assert total == 2
    by_type = {r.movement_type.value: r for r in rows}
    assert set(by_type) == {"sale", "rto"}  # never netted into one row
    assert by_type["sale"].quantity_delta == -20
    assert by_type["sale"].quantity_packets == 20
    assert by_type["sale"].reason == "Marketplace sale"
    assert by_type["rto"].quantity_delta == 2
    assert by_type["rto"].quantity_packets == 2
    assert by_type["rto"].reason == "Customer return"


async def test_net_marketplace_adjustment_after_sale_and_rto_matches_the_example(
    db_session: AsyncSession,
) -> None:
    """20 Amazon packets sold -> -20; 2 returned -> +2; net = -18."""
    product, _ = await _make_variant(db_session, sku="MKT-NET-1")
    service = PlatformInventoryService(db_session)
    today = ist_today()

    await service.record_product_movement(
        product.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=ProductMarketplaceMovementType.SALE,
        quantity_packets=20,
        reason=None,
        stock_date=today,
        actor=None,
    )
    await service.record_product_movement(
        product.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=ProductMarketplaceMovementType.RTO,
        quantity_packets=2,
        reason=None,
        stock_date=today,
        actor=None,
    )

    summary = await service.get_product_platform_stock(product.id, stock_date=today)
    amazon = {row.platform: row for row in summary.platforms}[InventoryPlatform.AMAZON]
    assert amazon.current_stock == -18


async def test_product_level_movement_never_touches_any_product_variant_row(
    db_session: AsyncSession,
) -> None:
    """No ProductVariant is arbitrarily selected or modified -- exact
    snapshot equality of every underlying row before/after.
    """
    product, variants = await _make_product_with_variants(
        db_session,
        key="MKT-NOSKU",
        variants=[
            {"sku": "MKT-NOSKU-A", "available_quantity": 111, "pack_size": 1},
            {"sku": "MKT-NOSKU-B", "available_quantity": 222, "pack_size": 1},
        ],
    )
    before = {
        v.id: (v.available_quantity, v.inventory_quantity, v.pack_size, v.packets_per_box)
        for v in variants
    }

    service = PlatformInventoryService(db_session)
    await service.record_product_movement(
        product.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=ProductMarketplaceMovementType.SALE,
        quantity_packets=20,
        reason=None,
        stock_date=ist_today(),
        actor=None,
    )

    refreshed = await ProductVariantRepository(db_session).list_for_product(product.id)
    after = {
        v.id: (v.available_quantity, v.inventory_quantity, v.pack_size, v.packets_per_box)
        for v in refreshed
    }
    assert after == before  # not one field on any SKU moved


async def test_product_level_movement_never_modifies_shopify_inventory_quantity(
    db_session: AsyncSession,
) -> None:
    product, variant = await _make_variant(db_session, sku="MKT-SHOPIFY-1", available_quantity=50)
    await ProductVariantRepository(db_session).update(variant, inventory_quantity=999)
    await db_session.commit()

    service = PlatformInventoryService(db_session)
    await service.record_product_movement(
        product.id,
        platform=InventoryPlatform.MEESHO,
        movement_type=ProductMarketplaceMovementType.RTO,
        quantity_packets=5,
        reason=None,
        stock_date=ist_today(),
        actor=None,
    )

    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.inventory_quantity == 999  # Shopify's own field, completely untouched
    assert refreshed.available_quantity == 50  # OMS stock also untouched


async def test_existing_dispatch_still_works_unaffected_by_product_level_ledger(
    db_session: AsyncSession,
) -> None:
    """Existing Dispatch/RTO-restock SKU-level inventory logic is
    completely independent of the new product-level ledger.
    """
    product, variant = await _make_variant(db_session, sku="MKT-DISPATCH-1", available_quantity=100)
    order = await _make_order_with_item(
        db_session,
        order_number="MKT-DISPATCH-ORD-1",
        sku="MKT-DISPATCH-1",
        quantity=3,
        product_variant_id=variant.id,
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="MKT-DISPATCH-AWB-1")

    service = PlatformInventoryService(db_session)
    await service.record_product_movement(
        product.id,
        platform=InventoryPlatform.BLINKIT,
        movement_type=ProductMarketplaceMovementType.SALE,
        quantity_packets=10,
        reason=None,
        stock_date=ist_today(),
        actor=None,
    )

    for status in (ShipmentStatus.PICKED_UP, ShipmentStatus.IN_TRANSIT):
        await apply_tracking_event(
            db_session,
            shipment,
            _tracking_event(status=status),
            shipment_service=ShipmentService(db_session),
            rto_service=RTOService(db_session),
            inventory_service=InventoryService(db_session),
        )

    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.available_quantity == 97  # 100 - 3, Shopify-side dispatch, unaffected


async def test_marketplace_platform_is_preserved_across_multiple_platforms(
    db_session: AsyncSession,
) -> None:
    product, _ = await _make_variant(db_session, sku="MKT-MULTI-PLATFORM-1")
    service = PlatformInventoryService(db_session)
    today = ist_today()

    for platform, qty in (
        (InventoryPlatform.AMAZON, 10),
        (InventoryPlatform.FLIPKART, 20),
        (InventoryPlatform.BLINKIT, 30),
        (InventoryPlatform.MEESHO, 40),
        (InventoryPlatform.MANUAL_OTHER, 50),
    ):
        await service.record_product_movement(
            product.id,
            platform=platform,
            movement_type=ProductMarketplaceMovementType.RTO,
            quantity_packets=qty,
            reason=None,
            stock_date=today,
            actor=None,
        )

    summary = await service.get_product_platform_stock(product.id, stock_date=today)
    rows = {row.platform: row for row in summary.platforms}
    assert rows[InventoryPlatform.AMAZON].current_stock == 10
    assert rows[InventoryPlatform.FLIPKART].current_stock == 20
    assert rows[InventoryPlatform.BLINKIT].current_stock == 30
    assert rows[InventoryPlatform.MEESHO].current_stock == 40
    assert rows[InventoryPlatform.MANUAL_OTHER].current_stock == 50


async def test_reason_is_optional_and_trimmed(db_session: AsyncSession) -> None:
    product, _ = await _make_variant(db_session, sku="MKT-REASON-1")
    service = PlatformInventoryService(db_session)

    no_reason = await service.record_product_movement(
        product.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=ProductMarketplaceMovementType.RTO,
        quantity_packets=1,
        reason="   ",
        stock_date=ist_today(),
        actor=None,
    )
    assert no_reason.reason is None  # whitespace-only reason normalized to None

    with_reason = await service.record_product_movement(
        product.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=ProductMarketplaceMovementType.RTO,
        quantity_packets=1,
        reason="  Warehouse received  ",
        stock_date=ist_today(),
        actor=None,
    )
    assert with_reason.reason == "Warehouse received"  # trimmed


async def test_zero_or_negative_quantity_packets_is_rejected(db_session: AsyncSession) -> None:
    product, _ = await _make_variant(db_session, sku="MKT-INVALID-QTY-1")
    service = PlatformInventoryService(db_session)

    for bad_qty in (0, -5):
        with pytest.raises(ValidationError):
            await service.record_product_movement(
                product.id,
                platform=InventoryPlatform.AMAZON,
                movement_type=ProductMarketplaceMovementType.SALE,
                quantity_packets=bad_qty,
                reason=None,
                stock_date=ist_today(),
                actor=None,
            )


async def test_non_uniform_pack_size_rejects_the_product_level_movement(
    db_session: AsyncSession,
) -> None:
    """THE critical safety gap this feature must never silently guess
    past: once a product's real SKUs disagree on pack_size (e.g. the
    approved 60/120/180 -> 1/2/3 boxes-per-unit backfill), a bare
    "N packets sold" has no deterministic outer-count without knowing
    which pack size was sold -- must be REJECTED, never averaged/
    defaulted/allocated to one arbitrary SKU.
    """
    product, _ = await _make_product_with_variants(
        db_session,
        key="MKT-NONUNIFORM",
        variants=[
            {"sku": "MKT-NU-60", "available_quantity": 100, "pack_size": 1},
            {"sku": "MKT-NU-120", "available_quantity": 100, "pack_size": 2},
            {"sku": "MKT-NU-180", "available_quantity": 100, "pack_size": 3},
        ],
    )
    service = PlatformInventoryService(db_session)

    with pytest.raises(ValidationError):
        await service.record_product_movement(
            product.id,
            platform=InventoryPlatform.AMAZON,
            movement_type=ProductMarketplaceMovementType.SALE,
            quantity_packets=20,
            reason=None,
            stock_date=ist_today(),
            actor=None,
        )


async def test_uniform_pack_size_greater_than_one_converts_packets_to_outers(
    db_session: AsyncSession,
) -> None:
    """When a product's SKUs DO agree (e.g. every SKU pack_size=2), the
    conversion is deterministic and must actually apply -- not silently
    treated as 1:1.
    """
    product, _ = await _make_product_with_variants(
        db_session,
        key="MKT-UNIFORM2",
        variants=[
            {"sku": "MKT-U2-A", "available_quantity": 50, "pack_size": 2},
            {"sku": "MKT-U2-B", "available_quantity": 50, "pack_size": 2},
        ],
    )
    service = PlatformInventoryService(db_session)

    movement = await service.record_product_movement(
        product.id,
        platform=InventoryPlatform.AMAZON,
        movement_type=ProductMarketplaceMovementType.RTO,
        quantity_packets=10,
        reason=None,
        stock_date=ist_today(),
        actor=None,
    )
    assert movement.quantity_packets == 10
    assert movement.quantity_delta == 20  # 10 packets * pack_size 2 = 20 outers
    assert movement.quantity_after == 20


# --- Product-level marketplace movement endpoints -------------------------


async def test_record_product_marketplace_movement_endpoint_end_to_end(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _ = await _make_variant(db_session, sku="MKT-EP-E2E-1")
    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read", "inventory.manage"]
    ) as client:
        sale = await client.post(
            f"/api/v1/inventory/products/{product.id}/marketplace-movements",
            json={"platform": "amazon", "movement_type": "sale", "quantity_packets": 20},
        )
        assert sale.status_code == 201
        body = sale.json()["data"]
        assert body["platform"] == "amazon"
        assert body["movement_type"] == "sale"
        assert body["quantity_packets"] == 20
        assert body["quantity_delta"] == -20
        assert body["quantity_after"] == -20

        rto = await client.post(
            f"/api/v1/inventory/products/{product.id}/marketplace-movements",
            json={"platform": "amazon", "movement_type": "rto", "quantity_packets": 2},
        )
        assert rto.status_code == 201
        assert rto.json()["data"]["quantity_after"] == -18

        stock = await client.get(f"/api/v1/inventory/products/{product.id}/platform-stock")
        rows = {r["platform"]: r for r in stock.json()["data"]["platforms"]}
        assert rows["amazon"]["current_stock"] == -18
        # ONE table per product -- a flat platforms list, never a per-SKU breakdown
        assert "variants" not in stock.json()["data"]


async def test_record_product_marketplace_movement_requires_inventory_manage(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _ = await _make_variant(db_session, sku="MKT-EP-MANAGE-1")
    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"], email="mkt-readonly@example.com"
    ) as client:
        response = await client.post(
            f"/api/v1/inventory/products/{product.id}/marketplace-movements",
            json={"platform": "amazon", "movement_type": "sale", "quantity_packets": 5},
        )
        assert response.status_code == 403


async def test_record_product_marketplace_movement_rejects_non_positive_quantity(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _ = await _make_variant(db_session, sku="MKT-EP-422-1")
    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read", "inventory.manage"]
    ) as client:
        response = await client.post(
            f"/api/v1/inventory/products/{product.id}/marketplace-movements",
            json={"platform": "amazon", "movement_type": "sale", "quantity_packets": -1},
        )
        assert response.status_code == 422  # Pydantic Field(gt=0) rejects at the schema layer


async def test_record_product_marketplace_movement_rejects_non_uniform_pack_size_with_422(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _ = await _make_product_with_variants(
        db_session,
        key="MKT-EP-NONUNIFORM",
        variants=[
            {"sku": "MKT-EP-NU-60", "available_quantity": 100, "pack_size": 1},
            {"sku": "MKT-EP-NU-120", "available_quantity": 100, "pack_size": 2},
        ],
    )
    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read", "inventory.manage"]
    ) as client:
        response = await client.post(
            f"/api/v1/inventory/products/{product.id}/marketplace-movements",
            json={"platform": "amazon", "movement_type": "sale", "quantity_packets": 20},
        )
        assert response.status_code == 422


async def test_list_product_marketplace_movements_endpoint(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _ = await _make_variant(db_session, sku="MKT-EP-HIST-1")
    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read", "inventory.manage"]
    ) as client:
        await client.post(
            f"/api/v1/inventory/products/{product.id}/marketplace-movements",
            json={
                "platform": "amazon",
                "movement_type": "sale",
                "quantity_packets": 20,
                "reason": "Marketplace sale",
            },
        )
        await client.post(
            f"/api/v1/inventory/products/{product.id}/marketplace-movements",
            json={
                "platform": "amazon",
                "movement_type": "rto",
                "quantity_packets": 2,
                "reason": "Customer return",
            },
        )

        history = await client.get(f"/api/v1/inventory/products/{product.id}/marketplace-movements")
        assert history.status_code == 200
        rows = history.json()["data"]
        assert len(rows) == 2
        by_type = {r["movement_type"]: r for r in rows}
        assert by_type["sale"]["quantity_delta"] == -20
        assert by_type["rto"]["quantity_delta"] == 2


async def test_list_product_marketplace_movements_requires_inventory_read(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _ = await _make_variant(db_session, sku="MKT-EP-HIST-READ-1")
    async with await make_authenticated_client(
        db_session, permission_codes=["analytics.read"], email="mkt-hist-noperm@example.com"
    ) as client:
        response = await client.get(
            f"/api/v1/inventory/products/{product.id}/marketplace-movements"
        )
        assert response.status_code == 403
