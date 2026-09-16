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
from app.core.timezone import ist_today
from app.integrations.shiprocket.sync import apply_tracking_event
from app.models.enums import PaymentType, PlatformStockMovementType, ShipmentStatus
from app.models.platform_inventory import InventoryPlatform
from app.repositories.order import OrderItemRepository
from app.repositories.product import ProductRepository, ProductVariantRepository
from app.schemas.common import PageParams
from app.schemas.order import OrderItemCreateRequest
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


async def _make_order_with_item(session: AsyncSession, *, order_number: str, sku: str, quantity: int, product_variant_id):
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
        variant.id, platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=500, reason="Initial warehouse stock", stock_date=None, actor=None,
    )
    assert first.quantity_delta == 500
    assert first.quantity_after == 500

    second = await service.record_movement(
        variant.id, platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=100, reason=None, stock_date=None, actor=None,
    )
    # current(500) + 100 = 600, never the client's raw "100" as a total.
    assert second.quantity_delta == 100
    assert second.quantity_after == 600


async def test_add_flipkart_stock_is_additive(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-FLP-1")
    service = PlatformInventoryService(db_session)
    await service.record_movement(
        variant.id, platform=InventoryPlatform.FLIPKART,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=350, reason=None, stock_date=None, actor=None,
    )
    second = await service.record_movement(
        variant.id, platform=InventoryPlatform.FLIPKART,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=10, reason=None, stock_date=None, actor=None,
    )
    assert second.quantity_after == 360


async def test_add_blinkit_stock_is_additive(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-BLK-1")
    service = PlatformInventoryService(db_session)
    await service.record_movement(
        variant.id, platform=InventoryPlatform.BLINKIT,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=200, reason=None, stock_date=None, actor=None,
    )
    second = await service.record_movement(
        variant.id, platform=InventoryPlatform.BLINKIT,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=15, reason=None, stock_date=None, actor=None,
    )
    assert second.quantity_after == 215


async def test_add_meesho_stock_is_additive(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-MSH-1")
    service = PlatformInventoryService(db_session)
    await service.record_movement(
        variant.id, platform=InventoryPlatform.MEESHO,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=150, reason=None, stock_date=None, actor=None,
    )
    second = await service.record_movement(
        variant.id, platform=InventoryPlatform.MEESHO,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=5, reason=None, stock_date=None, actor=None,
    )
    assert second.quantity_after == 155


async def test_add_stock_calculates_from_actual_current_stock_not_a_hardcoded_value(
    db_session: AsyncSession,
) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-CALC-1")
    service = PlatformInventoryService(db_session)
    await service.record_movement(
        variant.id, platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=37, reason=None, stock_date=None, actor=None,
    )
    result = await service.record_movement(
        variant.id, platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=13, reason=None, stock_date=None, actor=None,
    )
    assert result.quantity_after == 50  # 37 + 13, never a hardcoded/guessed number


async def test_add_stock_rejects_negative_quantity(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-NEG-1")
    service = PlatformInventoryService(db_session)
    with pytest.raises(ValidationError):
        await service.record_movement(
            variant.id, platform=InventoryPlatform.AMAZON,
            movement_type=PlatformStockMovementType.STOCK_ADDED,
            quantity=-5, reason=None, stock_date=None, actor=None,
        )


async def test_add_stock_rejects_zero_quantity(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-ZERO-1")
    service = PlatformInventoryService(db_session)
    with pytest.raises(ValidationError):
        await service.record_movement(
            variant.id, platform=InventoryPlatform.AMAZON,
            movement_type=PlatformStockMovementType.STOCK_ADDED,
            quantity=0, reason=None, stock_date=None, actor=None,
        )


async def test_reason_is_optional_for_platform_stock_unlike_shopify_adjustment(
    db_session: AsyncSession,
) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-REASON-1")
    service = PlatformInventoryService(db_session)
    movement = await service.record_movement(
        variant.id, platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=10, reason=None, stock_date=None, actor=None,
    )
    assert movement.reason is None  # never required to raise ValidationError


async def test_unknown_platform_is_rejected(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-UNK-1")
    service = PlatformInventoryService(db_session)
    with pytest.raises(ValidationError):
        await service.record_movement(
            variant.id, platform="ebay", movement_type=PlatformStockMovementType.STOCK_ADDED,
            quantity=10, reason=None, stock_date=None, actor=None,
        )


# --- Record deduction (Sold/Dispatched) -------------------------------


async def test_record_deduction_subtracts_from_current_stock(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-DED-1")
    service = PlatformInventoryService(db_session)
    await service.record_movement(
        variant.id, platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=500, reason=None, stock_date=None, actor=None,
    )
    result = await service.record_movement(
        variant.id, platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_DEDUCTED,
        quantity=18, reason="Marketplace sale", stock_date=None, actor=None,
    )
    assert result.quantity_delta == -18
    assert result.quantity_after == 482


async def test_deduction_below_zero_is_rejected(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-DED-NEG-1")
    service = PlatformInventoryService(db_session)
    await service.record_movement(
        variant.id, platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=10, reason=None, stock_date=None, actor=None,
    )
    with pytest.raises(ValidationError):
        await service.record_movement(
            variant.id, platform=InventoryPlatform.AMAZON,
            movement_type=PlatformStockMovementType.STOCK_DEDUCTED,
            quantity=11, reason=None, stock_date=None, actor=None,
        )


# --- Platform / variant isolation -------------------------------------


async def test_platform_isolation_amazon_stock_never_affects_flipkart(
    db_session: AsyncSession,
) -> None:
    _, variant = await _make_variant(db_session, sku="PLAT-ISO-1")
    service = PlatformInventoryService(db_session)
    await service.record_movement(
        variant.id, platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=999, reason=None, stock_date=None, actor=None,
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
        variant_a.id, platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=100, reason=None, stock_date=None, actor=None,
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
        variant.id, platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=500, reason=None, stock_date=None, actor=None,
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
        variant.id, platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=500, reason=None, stock_date=day1, actor=None,
    )
    await service.record_movement(
        variant.id, platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=30, reason=None, stock_date=day2, actor=None,
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
        variant.id, platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=50, reason=None, stock_date=today, actor=None,
    )
    await service.record_movement(
        variant.id, platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_DEDUCTED,
        quantity=20, reason=None, stock_date=today, actor=None,
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

    await service.record_movement(
        variant.id, platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=500, reason=None, stock_date=yesterday, actor=None,
    )
    await service.record_movement(
        variant.id, platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=50, reason=None, stock_date=today, actor=None,
    )
    await service.record_movement(
        variant.id, platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_DEDUCTED,
        quantity=20, reason=None, stock_date=today, actor=None,
    )

    summary = await service.get_product_platform_stock(product.id, stock_date=today)
    assert len(summary.variants) == 1
    rows = {row.platform: row for row in summary.variants[0].platforms}

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
        variant.id, platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=530, reason=None, stock_date=day2, actor=None,
    )
    # Backfilling day1 AFTER day2 already exists.
    await service.record_movement(
        variant.id, platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=500, reason=None, stock_date=day1, actor=None,
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
        db_session, order_number="PLAT-HIST-ORD-1", sku="PLAT-HIST-MERGE-1", quantity=1,
        product_variant_id=variant.id,
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="PLAT-HIST-AWB-1")
    await apply_tracking_event(
        db_session, shipment, _tracking_event(status=ShipmentStatus.PICKED_UP),
        shipment_service=ShipmentService(db_session), rto_service=RTOService(db_session),
        inventory_service=InventoryService(db_session),
    )
    await service.record_movement(
        variant.id, platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=50, reason="Warehouse stock", stock_date=None, actor=None,
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
        variant.id, platform=InventoryPlatform.AMAZON,
        movement_type=PlatformStockMovementType.STOCK_ADDED,
        quantity=50, reason=None, stock_date=None, actor=None,
    )
    rows, total = await service.get_movement_history(
        variant.id, platform=InventoryPlatform.AMAZON, date_from=None, date_to=None,
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
        db_session, order_number="PLAT-SHIP-ORD-1", sku="PLAT-SHIP-1", quantity=3,
        product_variant_id=variant.id,
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="PLAT-SHIP-AWB-1")
    for status in (ShipmentStatus.PICKED_UP, ShipmentStatus.IN_TRANSIT):
        await apply_tracking_event(
            db_session, shipment, _tracking_event(status=status),
            shipment_service=ShipmentService(db_session), rto_service=RTOService(db_session),
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
