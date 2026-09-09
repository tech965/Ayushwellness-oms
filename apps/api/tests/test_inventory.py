"""Inventory (Product -> Variant -> Inventory, in BOXES).

Everything here is Shiprocket-driven and OMS-owned: `OrderItem.quantity`
(packets) converts to boxes via each variant's own `packets_per_box`
(ceiling division), dispatch/RTO-restock never read Shopify's
`inventory_quantity`, and idempotency is enforced by the existing
`InventoryMovement` ledger (one row per (order, variant, movement type),
no matter how many times a tracking event/webhook re-fires).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.core.exceptions import ValidationError
from app.integrations.shiprocket.sync import apply_tracking_event
from app.models.enums import (
    InventoryMovementType,
    PaymentType,
    RTOStatus,
    ShipmentStatus,
    StockStatus,
)
from app.repositories.inventory import InventoryMovementRepository
from app.repositories.order import OrderItemRepository
from app.repositories.product import ProductRepository, ProductVariantRepository
from app.repositories.rto import RTORepository
from app.schemas.common import PageParams, SortParams
from app.schemas.order import OrderItemCreateRequest
from app.services.inventory_service import InventoryService
from app.services.order_service import OrderService
from app.services.product_service import ProductService
from app.services.rto_service import RTOService
from app.services.shipment_service import ShipmentService
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _make_variant(
    session: AsyncSession,
    *,
    sku: str,
    available_quantity: int = 10,
    packets_per_box: int = 1,
    inventory_quantity: int = 0,
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
        packets_per_box=packets_per_box,
        inventory_quantity=inventory_quantity,
    )
    await session.commit()
    return product, variant


async def _make_order(
    session: AsyncSession,
    *,
    order_number: str,
    items: list[dict],
):
    order = await OrderService(session).create_order(
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
                product_variant_id=item.get("product_variant_id"),
                sku=item["sku"],
                product_name=f"Product {item['sku']}",
                quantity=item["quantity"],
                unit_price=Decimal("100.00"),
            )
            for item in items
        ],
    )
    return order


async def _make_order_with_item(
    session: AsyncSession, *, order_number: str, sku: str, quantity: int, product_variant_id=None
):
    return await _make_order(
        session,
        order_number=order_number,
        items=[{"sku": sku, "quantity": quantity, "product_variant_id": product_variant_id}],
    )


async def _make_shipment(session: AsyncSession, *, order_id, awb: str):
    return await ShipmentService(session).create_shipment(
        actor=None, order_id=order_id, awb=awb, courier_id=None, expected_delivery_date=None
    )


def _page_params() -> PageParams:
    return PageParams(page=1, page_size=50)


def _sort_params() -> SortParams:
    return SortParams(sort_by=None, sort_order="desc")


# --- packets -> boxes conversion (ceiling division) -------------------


async def test_dispatch_converts_packets_to_boxes_exact_division(db_session: AsyncSession) -> None:
    cases = [(60, 60, 1), (120, 60, 2), (180, 60, 3)]
    for i, (packets, packets_per_box, expected_boxes) in enumerate(cases):
        sku = f"SKU-EXACT-{i}"
        _, variant = await _make_variant(
            db_session, sku=sku, available_quantity=10, packets_per_box=packets_per_box
        )
        order = await _make_order_with_item(
            db_session,
            order_number=f"ORD-EXACT-{i}",
            sku=sku,
            quantity=packets,
            product_variant_id=variant.id,
        )
        shipment = await _make_shipment(db_session, order_id=order.id, awb=f"AWB-EXACT-{i}")
        await InventoryService(db_session).apply_dispatch(order_id=order.id, shipment_id=shipment.id)

        refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
        assert refreshed.available_quantity == 10 - expected_boxes


async def test_dispatch_rounds_up_partial_box_with_ceiling(db_session: AsyncSession) -> None:
    """130 packets / 60 per box = 3 boxes (ceil), not 2 (floor)."""
    _, variant = await _make_variant(
        db_session, sku="SKU-CEIL", available_quantity=10, packets_per_box=60
    )
    order = await _make_order_with_item(
        db_session, order_number="ORD-CEIL", sku="SKU-CEIL", quantity=130, product_variant_id=variant.id
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWB-CEIL")
    await InventoryService(db_session).apply_dispatch(order_id=order.id, shipment_id=shipment.id)

    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.available_quantity == 7  # 10 - 3


async def test_dispatch_uses_each_variants_own_packets_per_box(db_session: AsyncSession) -> None:
    """Never a hardcoded constant -- two variants, two different pack sizes,
    dispatched with the same packet quantity, produce different box counts.
    """
    _, variant_a = await _make_variant(
        db_session, sku="SKU-PPB-A", available_quantity=10, packets_per_box=30
    )
    order_a = await _make_order_with_item(
        db_session, order_number="ORD-PPB-A", sku="SKU-PPB-A", quantity=90, product_variant_id=variant_a.id
    )
    shipment_a = await _make_shipment(db_session, order_id=order_a.id, awb="AWB-PPB-A")
    await InventoryService(db_session).apply_dispatch(order_id=order_a.id, shipment_id=shipment_a.id)

    _, variant_b = await _make_variant(
        db_session, sku="SKU-PPB-B", available_quantity=10, packets_per_box=100
    )
    order_b = await _make_order_with_item(
        db_session, order_number="ORD-PPB-B", sku="SKU-PPB-B", quantity=90, product_variant_id=variant_b.id
    )
    shipment_b = await _make_shipment(db_session, order_id=order_b.id, awb="AWB-PPB-B")
    await InventoryService(db_session).apply_dispatch(order_id=order_b.id, shipment_id=shipment_b.id)

    refreshed_a = await ProductVariantRepository(db_session).get_by_id(variant_a.id)
    refreshed_b = await ProductVariantRepository(db_session).get_by_id(variant_b.id)
    assert refreshed_a.available_quantity == 7  # 90 / 30 = 3 boxes
    assert refreshed_b.available_quantity == 9  # ceil(90 / 100) = 1 box


async def test_multiple_variants_in_one_order_calculated_independently(
    db_session: AsyncSession,
) -> None:
    _, variant_1 = await _make_variant(
        db_session, sku="SKU-MULTI-1", available_quantity=10, packets_per_box=60
    )
    _, variant_2 = await _make_variant(
        db_session, sku="SKU-MULTI-2", available_quantity=10, packets_per_box=30
    )
    order = await _make_order(
        db_session,
        order_number="ORD-MULTI",
        items=[
            {"sku": "SKU-MULTI-1", "quantity": 120, "product_variant_id": variant_1.id},
            {"sku": "SKU-MULTI-2", "quantity": 90, "product_variant_id": variant_2.id},
        ],
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWB-MULTI")
    await InventoryService(db_session).apply_dispatch(order_id=order.id, shipment_id=shipment.id)

    refreshed_1 = await ProductVariantRepository(db_session).get_by_id(variant_1.id)
    refreshed_2 = await ProductVariantRepository(db_session).get_by_id(variant_2.id)
    assert refreshed_1.available_quantity == 8  # 10 - 2 boxes
    assert refreshed_2.available_quantity == 7  # 10 - 3 boxes


# --- Shiprocket lifecycle ------------------------------------------------


async def test_order_creation_does_not_move_inventory(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="SKU-CREATE", available_quantity=10)
    await _make_order_with_item(
        db_session, order_number="ORD-CREATE", sku="SKU-CREATE", quantity=5, product_variant_id=variant.id
    )
    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.available_quantity == 10


async def test_apply_dispatch_decrements_stock_once(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="SKU-1", available_quantity=10)
    order = await _make_order_with_item(
        db_session, order_number="ORD-1", sku="SKU-1", quantity=3, product_variant_id=variant.id
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWB-1")

    service = InventoryService(db_session)
    await service.apply_dispatch(order_id=order.id, shipment_id=shipment.id)
    # Repeated call -- same order, same shipment (the real pattern when a
    # shipment advances PICKED_UP -> IN_TRANSIT -> DELIVERED and each
    # transition re-triggers `apply_dispatch`).
    await service.apply_dispatch(order_id=order.id, shipment_id=shipment.id)

    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.available_quantity == 7

    movements, _total = await service.list_movements(
        page_params=_page_params(), sort_params=_sort_params()
    )
    dispatch_movements = [m for m in movements if m.movement_type == InventoryMovementType.DISPATCH]
    assert len(dispatch_movements) == 1
    assert dispatch_movements[0].quantity_delta == -3


async def test_apply_dispatch_resolves_by_sku_when_variant_id_missing(
    db_session: AsyncSession,
) -> None:
    _, variant = await _make_variant(db_session, sku="SKU-2", available_quantity=5)
    order = await _make_order_with_item(
        db_session, order_number="ORD-2", sku="SKU-2", quantity=2, product_variant_id=None
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWB-2")

    await InventoryService(db_session).apply_dispatch(order_id=order.id, shipment_id=shipment.id)

    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.available_quantity == 3


async def test_apply_dispatch_skips_unresolvable_sku_without_raising(
    db_session: AsyncSession,
) -> None:
    order = await _make_order_with_item(
        db_session, order_number="ORD-3", sku="SKU-DOES-NOT-EXIST", quantity=1
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWB-3")

    # Must not raise.
    await InventoryService(db_session).apply_dispatch(order_id=order.id, shipment_id=shipment.id)


async def test_zero_quantity_order_item_creates_no_movement(db_session: AsyncSession) -> None:
    """The request schema (`OrderItemCreateRequest.quantity: Field(gt=0)`)
    already blocks this at the API boundary -- this exercises the
    service-level defensive guard directly for any line item that reaches
    the service with quantity 0 by another path (a sync/import edge case).
    """
    _, variant = await _make_variant(db_session, sku="SKU-ZERO", available_quantity=10)
    order = await _make_order_with_item(
        db_session, order_number="ORD-ZERO", sku="SKU-ZERO", quantity=1, product_variant_id=variant.id
    )
    await OrderItemRepository(db_session).create(
        order_id=order.id,
        product_variant_id=variant.id,
        sku="SKU-ZERO",
        product_name="Zero qty line",
        quantity=0,
        unit_price=Decimal("10.00"),
        total_amount=Decimal("0.00"),
    )
    await db_session.commit()
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWB-ZERO")

    service = InventoryService(db_session)
    await service.apply_dispatch(order_id=order.id, shipment_id=shipment.id)

    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.available_quantity == 9  # only the qty=1 line moved anything

    movements, _total = await service.list_movements(
        page_params=_page_params(), sort_params=_sort_params()
    )
    assert len(movements) == 1


async def test_apply_rto_restock_increments_stock_once(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(
        db_session, sku="SKU-4", available_quantity=5, packets_per_box=60
    )
    order = await _make_order_with_item(
        db_session, order_number="ORD-4", sku="SKU-4", quantity=180, product_variant_id=variant.id
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWB-4")
    rto, _ = await RTORepository(db_session).upsert_by_external_id(
        source_system="shiprocket",
        external_id="AWB-4",
        shipment_id=shipment.id,
        order_id=order.id,
        status=RTOStatus.RECEIVED,
    )
    await db_session.commit()

    service = InventoryService(db_session)
    await service.apply_rto_restock(order_id=order.id, rto_id=rto.id)
    await service.apply_rto_restock(order_id=order.id, rto_id=rto.id)

    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.available_quantity == 8  # 5 + 3 boxes (180 / 60), only once


async def test_manual_rto_status_update_triggers_restock(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="SKU-5", available_quantity=4)
    order = await _make_order_with_item(
        db_session, order_number="ORD-5", sku="SKU-5", quantity=1, product_variant_id=variant.id
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWB-5")
    rto, _ = await RTORepository(db_session).upsert_by_external_id(
        source_system="shiprocket",
        external_id="AWB-5",
        shipment_id=shipment.id,
        order_id=order.id,
        status=RTOStatus.INITIATED,
    )
    await db_session.commit()

    await RTOService(db_session).update_rto(rto.id, actor=None, status=RTOStatus.RECEIVED)

    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.available_quantity == 5


def _tracking_event(*, status: ShipmentStatus, mapped: ShipmentStatus | None) -> dict:
    return {
        "event_timestamp": datetime.now(UTC),
        "external_event_id": f"evt-{status.value}",
        "status": status.value,
        "mapped_status": mapped,
        "location": "Hub",
        "description": status.value,
        "courier_name": "Test Courier",
        "raw_payload": None,
    }


async def test_dispatch_transit_delivered_only_move_inventory_once(
    db_session: AsyncSession,
) -> None:
    """PICKED_UP deducts; IN_TRANSIT and DELIVERED must not deduct again."""
    _, variant = await _make_variant(db_session, sku="SKU-6", available_quantity=8)
    order = await _make_order_with_item(
        db_session, order_number="ORD-6", sku="SKU-6", quantity=2, product_variant_id=variant.id
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWB-6")

    shipment_service = ShipmentService(db_session)
    rto_service = RTOService(db_session)
    inventory_service = InventoryService(db_session)

    for status in (ShipmentStatus.PICKED_UP, ShipmentStatus.IN_TRANSIT, ShipmentStatus.DELIVERED):
        await apply_tracking_event(
            db_session,
            shipment,
            _tracking_event(status=status, mapped=status),
            shipment_service=shipment_service,
            rto_service=rto_service,
            inventory_service=inventory_service,
        )

    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.available_quantity == 6  # 8 - 2, exactly once

    movements, _total = await inventory_service.list_movements(
        page_params=_page_params(), sort_params=_sort_params(), product_variant_id=variant.id
    )
    assert len(movements) == 1


async def test_rto_initiated_does_not_restock(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="SKU-RTO-INIT", available_quantity=5)
    order = await _make_order_with_item(
        db_session, order_number="ORD-RTO-INIT", sku="SKU-RTO-INIT", quantity=1, product_variant_id=variant.id
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWB-RTO-INIT")

    shipment_service = ShipmentService(db_session)
    rto_service = RTOService(db_session)
    inventory_service = InventoryService(db_session)

    await apply_tracking_event(
        db_session,
        shipment,
        _tracking_event(status=ShipmentStatus.RTO_INITIATED, mapped=ShipmentStatus.RTO_INITIATED),
        shipment_service=shipment_service,
        rto_service=rto_service,
        inventory_service=inventory_service,
    )

    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.available_quantity == 5  # unchanged -- RTO not yet received

    movements, _total = await inventory_service.list_movements(
        page_params=_page_params(), sort_params=_sort_params(), product_variant_id=variant.id
    )
    assert movements == []


async def test_duplicate_rto_received_event_does_not_restock_twice(
    db_session: AsyncSession,
) -> None:
    _, variant = await _make_variant(db_session, sku="SKU-RTO-DUP", available_quantity=5)
    order = await _make_order_with_item(
        db_session, order_number="ORD-RTO-DUP", sku="SKU-RTO-DUP", quantity=1, product_variant_id=variant.id
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWB-RTO-DUP")

    shipment_service = ShipmentService(db_session)
    rto_service = RTOService(db_session)
    inventory_service = InventoryService(db_session)

    for i in range(2):
        event = _tracking_event(
            status=ShipmentStatus.RTO_DELIVERED, mapped=ShipmentStatus.RTO_DELIVERED
        )
        event["external_event_id"] = f"evt-rto-received-{i}"  # distinct events, same outcome
        await apply_tracking_event(
            db_session,
            shipment,
            event,
            shipment_service=shipment_service,
            rto_service=rto_service,
            inventory_service=inventory_service,
        )

    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.available_quantity == 6  # +1, exactly once


# --- manual adjustment (absolute target) ---------------------------------


async def test_manual_adjustment_20_to_25_creates_plus_5(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="SKU-ADJ-UP", available_quantity=20)
    movement = await InventoryService(db_session).adjust_to_target(
        variant.id, target_boxes=25, reason="Stock received", actor=None
    )
    assert movement.quantity_delta == 5
    assert movement.quantity_after == 25
    assert movement.movement_type == InventoryMovementType.MANUAL_ADJUSTMENT

    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.available_quantity == 25


async def test_manual_adjustment_20_to_15_creates_minus_5(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="SKU-ADJ-DOWN", available_quantity=20)
    movement = await InventoryService(db_session).adjust_to_target(
        variant.id, target_boxes=15, reason="Correction", actor=None
    )
    assert movement.quantity_delta == -5
    assert movement.quantity_after == 15


async def test_manual_adjustment_requires_reason(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="SKU-ADJ-NOREASON", available_quantity=20)
    with pytest.raises(ValidationError):
        await InventoryService(db_session).adjust_to_target(
            variant.id, target_boxes=25, reason="   ", actor=None
        )


async def test_manual_adjustment_noop_target_rejected(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="SKU-ADJ-NOOP", available_quantity=20)
    with pytest.raises(ValidationError):
        await InventoryService(db_session).adjust_to_target(
            variant.id, target_boxes=20, reason="No real change", actor=None
        )


async def test_manual_adjustment_rejects_negative_target(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="SKU-ADJ-NEG", available_quantity=20)
    with pytest.raises(ValidationError):
        await InventoryService(db_session).adjust_to_target(
            variant.id, target_boxes=-1, reason="Invalid", actor=None
        )


# --- packets-per-box configuration ---------------------------------------


async def test_packets_per_box_must_be_positive(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(db_session, sku="SKU-PPB-INVALID", packets_per_box=60)
    with pytest.raises(ValidationError):
        await InventoryService(db_session).update_packets_per_box(
            variant.id, packets_per_box=0, actor=None
        )
    with pytest.raises(ValidationError):
        await InventoryService(db_session).update_packets_per_box(
            variant.id, packets_per_box=-5, actor=None
        )


async def test_changing_packets_per_box_does_not_change_available_boxes(
    db_session: AsyncSession,
) -> None:
    _, variant = await _make_variant(
        db_session, sku="SKU-PPB-CHANGE", available_quantity=20, packets_per_box=60
    )
    await InventoryService(db_session).update_packets_per_box(
        variant.id, packets_per_box=30, actor=None
    )
    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.packets_per_box == 30
    assert refreshed.available_quantity == 20  # box count itself never moves


# --- total packets + stock status -----------------------------------------


async def test_total_packets_calculation(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(
        db_session, sku="SKU-TOTAL", available_quantity=20, packets_per_box=60
    )
    service = InventoryService(db_session)
    fetched = await service.get_variant_stock(variant.id)
    assert fetched.available_quantity * fetched.packets_per_box == 1200


@pytest.mark.parametrize(
    ("available_boxes", "expected_status"),
    [
        (10, StockStatus.IN_STOCK),
        (5, StockStatus.LOW_STOCK),
        (1, StockStatus.LOW_STOCK),
        (0, StockStatus.OUT_OF_STOCK),
    ],
)
async def test_stock_status_thresholds(
    db_session: AsyncSession, available_boxes: int, expected_status: StockStatus
) -> None:
    # Default threshold (no AppSettings row yet) is 5 -- see
    # `InventorySettings.low_stock_threshold` in app/schemas/settings.py.
    service = InventoryService(db_session)
    threshold = await service.get_low_stock_threshold()
    assert threshold == 5
    assert service.compute_stock_status(available_boxes, threshold) == expected_status


# --- Shopify isolation -----------------------------------------------------


async def test_shopify_quantity_does_not_affect_dispatch_or_restock(
    db_session: AsyncSession,
) -> None:
    _, variant = await _make_variant(
        db_session,
        sku="SKU-SHOPIFY-ISOLATED",
        available_quantity=10,
        inventory_quantity=999,  # Shopify's own count -- must never be read
    )
    order = await _make_order_with_item(
        db_session,
        order_number="ORD-SHOPIFY",
        sku="SKU-SHOPIFY-ISOLATED",
        quantity=3,
        product_variant_id=variant.id,
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWB-SHOPIFY")
    await InventoryService(db_session).apply_dispatch(order_id=order.id, shipment_id=shipment.id)

    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.available_quantity == 7  # 10 - 3, unaffected by inventory_quantity
    assert refreshed.inventory_quantity == 999  # Shopify field is untouched, still just a mirror


# --- HTTP endpoints: Product -> Variant -> Inventory hierarchy -------------


async def test_product_list_endpoint_shows_products_not_skus(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    await _make_variant(db_session, sku="SKU-EP-1", available_quantity=5, packets_per_box=60)

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as auth_client:
        response = await auth_client.get("/api/v1/inventory/stock", params={"q": "SKU-EP-1"})
        assert response.status_code == 200
        rows = response.json()["data"]
        assert len(rows) == 1
        row = rows[0]
        assert row["title"] == "Product SKU-EP-1"
        assert row["variant_count"] == 1
        assert row["total_available_boxes"] == 5
        assert row["total_packets"] == 300  # 5 boxes * 60 packets/box
        assert "sku" not in row  # product-level row, not a flat SKU row


async def test_list_variants_for_product_endpoint(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, variant = await _make_variant(
        db_session, sku="SKU-EP-2", available_quantity=20, packets_per_box=60
    )

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as auth_client:
        response = await auth_client.get(f"/api/v1/inventory/products/{product.id}/variants")
        assert response.status_code == 200
        body = response.json()["data"]
        assert body["product_title"] == "Product SKU-EP-2"
        assert len(body["variants"]) == 1
        row = body["variants"][0]
        assert row["sku"] == "SKU-EP-2"
        assert row["packets_per_box"] == 60
        assert row["available_boxes"] == 20
        assert row["total_packets"] == 1200
        assert row["stock_status"] == "in_stock"


async def test_adjust_stock_endpoint_target_based(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    _, variant = await _make_variant(db_session, sku="SKU-EP-3", available_quantity=20)

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read", "inventory.manage"]
    ) as auth_client:
        response = await auth_client.post(
            f"/api/v1/inventory/stock/{variant.id}/adjust",
            json={"target_boxes": 25, "reason": "Stock received"},
        )
        assert response.status_code == 200
        body = response.json()["data"]
        assert body["quantity_delta"] == 5
        assert body["previous_balance"] == 20
        assert body["quantity_after"] == 25
        assert body["actor_label"]

        stock = await auth_client.get(f"/api/v1/inventory/stock/{variant.id}")
        assert stock.json()["data"]["available_boxes"] == 25

        negative = await auth_client.post(
            f"/api/v1/inventory/stock/{variant.id}/adjust",
            json={"target_boxes": -1, "reason": "Invalid"},
        )
        assert negative.status_code == 422


async def test_update_packets_per_box_endpoint(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    _, variant = await _make_variant(
        db_session, sku="SKU-EP-4", available_quantity=20, packets_per_box=60
    )

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read", "inventory.manage"]
    ) as auth_client:
        response = await auth_client.patch(
            f"/api/v1/inventory/stock/{variant.id}/settings", json={"packets_per_box": 30}
        )
        assert response.status_code == 200
        body = response.json()["data"]
        assert body["packets_per_box"] == 30
        assert body["available_boxes"] == 20  # unchanged by a packets_per_box edit

        invalid = await auth_client.patch(
            f"/api/v1/inventory/stock/{variant.id}/settings", json={"packets_per_box": 0}
        )
        assert invalid.status_code == 422


async def test_movement_history_endpoint_includes_product_and_variant_names(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    _, variant = await _make_variant(db_session, sku="SKU-EP-5", available_quantity=10)
    order = await _make_order_with_item(
        db_session, order_number="ORD-EP-5", sku="SKU-EP-5", quantity=3, product_variant_id=variant.id
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWB-EP-5")
    await InventoryService(db_session).apply_dispatch(order_id=order.id, shipment_id=shipment.id)

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as auth_client:
        response = await auth_client.get(
            "/api/v1/inventory/movements", params={"product_variant_id": str(variant.id)}
        )
        assert response.status_code == 200
        rows = response.json()["data"]
        assert len(rows) == 1
        row = rows[0]
        assert row["product_title"] == "Product SKU-EP-5"
        assert row["variant_title"] is None or isinstance(row["variant_title"], str)
        assert row["sku"] == "SKU-EP-5"
        assert row["movement_type"] == "dispatch"
        assert row["quantity_delta"] == -3
        assert row["previous_balance"] == 10
        assert row["quantity_after"] == 7
        assert row["actor_label"] == "Shiprocket"
        assert row["shipment_id"] == str(shipment.id)


# --- production-readiness review: strict Shopify decoupling ---------------


async def test_new_variant_starts_at_zero_boxes_never_shopify_quantity(
    db_session: AsyncSession,
) -> None:
    """`available_quantity` must be OMS-controlled from creation, never
    seeded from Shopify's `inventory_quantity` -- even when Shopify
    reports a large count for a brand-new variant.
    """
    product, created = await ProductService(db_session).upsert_synced_product(
        source_system="shopify",
        external_id="prod-shopify-seed",
        title="Shopify Seed Product",
        variants=[
            {
                "external_id": "var-shopify-seed",
                "sku": "SKU-SHOPIFY-SEED",
                "price": Decimal("50.00"),
                "inventory_quantity": 500,  # Shopify's own count -- must be ignored
            }
        ],
    )
    assert created is True

    variant = await ProductVariantRepository(db_session).get_by_sku("SKU-SHOPIFY-SEED")
    assert variant.inventory_quantity == 500  # Shopify mirror still recorded, for reference
    assert variant.available_quantity == 0  # OMS stock is NOT seeded from it


async def test_resync_never_touches_existing_available_quantity(db_session: AsyncSession) -> None:
    """A later Shopify resync of an EXISTING variant must not move OMS
    stock even if Shopify's own count changed.
    """
    await ProductService(db_session).upsert_synced_product(
        source_system="shopify",
        external_id="prod-resync",
        title="Resync Product",
        variants=[
            {
                "external_id": "var-resync",
                "sku": "SKU-RESYNC",
                "price": Decimal("50.00"),
                "inventory_quantity": 10,
            }
        ],
    )
    variant = await ProductVariantRepository(db_session).get_by_sku("SKU-RESYNC")
    await InventoryService(db_session).adjust_to_target(
        variant.id, target_boxes=7, reason="Manual count", actor=None
    )

    # Shopify's count changes on a later sync -- OMS stock must be unaffected.
    await ProductService(db_session).upsert_synced_product(
        source_system="shopify",
        external_id="prod-resync",
        title="Resync Product",
        variants=[
            {
                "external_id": "var-resync",
                "sku": "SKU-RESYNC",
                "price": Decimal("50.00"),
                "inventory_quantity": 999,
            }
        ],
    )

    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.inventory_quantity == 999  # Shopify mirror updates, as before
    assert refreshed.available_quantity == 7  # OMS stock untouched by the resync


# --- production-readiness review: concurrency / DB-level idempotency ------


async def test_inventory_movements_unique_constraint_rejects_duplicate_dispatch(
    db_session: AsyncSession,
) -> None:
    """Direct proof the DB-level safety net exists, independent of the
    application's own `exists_for_order` check -- two rows for the same
    (variant, order, movement_type) must never coexist, even if something
    bypasses the service-layer check (e.g. a genuine concurrent race).
    """
    _, variant = await _make_variant(db_session, sku="SKU-RACE", available_quantity=10)
    order = await _make_order_with_item(
        db_session, order_number="ORD-RACE", sku="SKU-RACE", quantity=1, product_variant_id=variant.id
    )
    movements = InventoryMovementRepository(db_session)

    await movements.create(
        product_variant_id=variant.id,
        movement_type=InventoryMovementType.DISPATCH,
        quantity_delta=-1,
        quantity_after=9,
        order_id=order.id,
    )
    await db_session.commit()

    variant_id = variant.id  # captured before rollback() expires `variant`

    with pytest.raises(IntegrityError):
        # `create()` flushes immediately -- the constraint violation
        # surfaces right here, no explicit commit needed.
        await movements.create(
            product_variant_id=variant_id,
            movement_type=InventoryMovementType.DISPATCH,
            quantity_delta=-1,
            quantity_after=8,
            order_id=order.id,
        )
    await db_session.rollback()

    all_movements, total = await InventoryService(db_session).list_movements(
        page_params=_page_params(), sort_params=_sort_params(), product_variant_id=variant_id
    )
    assert total == 1


async def test_apply_dispatch_survives_a_lost_race_without_double_deducting(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates losing a real concurrent race: `exists_for_order`
    reports "not yet moved" (a stale snapshot, exactly what the loser of
    a real race would have seen), so `apply_dispatch` proceeds to write
    -- and that write raises `IntegrityError`, standing in for the DB
    unique constraint a genuinely concurrent winning transaction would
    have triggered (not reproducible against SQLite/aiosqlite's SAVEPOINT
    handling in this single-connection test harness; the constraint
    itself, and its SQL, are separately verified in
    `test_inventory_movements_unique_constraint_rejects_duplicate_dispatch`
    and the migration dry-run). Must not raise, and must not apply the
    stock decrement it was attempting.
    """
    _, variant = await _make_variant(db_session, sku="SKU-RACE-2", available_quantity=10)
    order = await _make_order_with_item(
        db_session, order_number="ORD-RACE-2", sku="SKU-RACE-2", quantity=3, product_variant_id=variant.id
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWB-RACE-2")
    variant_id, order_id, shipment_id = variant.id, order.id, shipment.id

    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        InventoryMovementRepository, "exists_for_order", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        InventoryMovementRepository,
        "create",
        AsyncMock(side_effect=IntegrityError("INSERT", {}, Exception("UNIQUE constraint failed"))),
    )

    # Must not raise.
    await InventoryService(db_session).apply_dispatch(order_id=order_id, shipment_id=shipment_id)

    refreshed = await ProductVariantRepository(db_session).get_by_id(variant_id)
    assert refreshed.available_quantity == 10  # the failed attempt's decrement never took effect


# --- production-readiness review: RTO restores dispatch's own box amount --


async def test_rto_restock_uses_original_dispatch_boxes_after_packets_per_box_changes(
    db_session: AsyncSession,
) -> None:
    """120 packets dispatched at 60/box = -2 boxes. `packets_per_box` is
    then changed to 30. The RTO restock for the SAME order+variant must
    still be +2 boxes (what was actually removed), never +4 (a fresh
    120/30 conversion using the now-current pack size).
    """
    _, variant = await _make_variant(
        db_session, sku="SKU-RTO-CONSISTENCY", available_quantity=20, packets_per_box=60
    )
    order = await _make_order_with_item(
        db_session,
        order_number="ORD-RTO-CONSISTENCY",
        sku="SKU-RTO-CONSISTENCY",
        quantity=120,
        product_variant_id=variant.id,
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWB-RTO-CONSISTENCY")
    service = InventoryService(db_session)
    await service.apply_dispatch(order_id=order.id, shipment_id=shipment.id)

    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.available_quantity == 18  # 20 - 2 boxes

    await service.update_packets_per_box(variant.id, packets_per_box=30, actor=None)

    rto, _ = await RTORepository(db_session).upsert_by_external_id(
        source_system="shiprocket",
        external_id="AWB-RTO-CONSISTENCY",
        shipment_id=shipment.id,
        order_id=order.id,
        status=RTOStatus.RECEIVED,
    )
    await db_session.commit()
    await service.apply_rto_restock(order_id=order.id, rto_id=rto.id)

    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.available_quantity == 20  # 18 + 2 (the ORIGINAL dispatch amount), not 18 + 4

    movements, _total = await service.list_movements(
        page_params=_page_params(), sort_params=_sort_params(), product_variant_id=variant.id
    )
    restock = next(m for m in movements if m.movement_type == InventoryMovementType.RTO_RESTOCK)
    assert restock.quantity_delta == 2


async def test_rto_restock_falls_back_to_current_conversion_with_no_matching_dispatch(
    db_session: AsyncSession,
) -> None:
    """No DISPATCH movement was ever recorded through this OMS for this
    (order, variant) -- the best available information is a fresh
    conversion using the variant's current `packets_per_box`.
    """
    _, variant = await _make_variant(
        db_session, sku="SKU-RTO-NO-DISPATCH", available_quantity=5, packets_per_box=60
    )
    order = await _make_order_with_item(
        db_session,
        order_number="ORD-RTO-NO-DISPATCH",
        sku="SKU-RTO-NO-DISPATCH",
        quantity=120,
        product_variant_id=variant.id,
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWB-RTO-NO-DISPATCH")
    rto, _ = await RTORepository(db_session).upsert_by_external_id(
        source_system="shiprocket",
        external_id="AWB-RTO-NO-DISPATCH",
        shipment_id=shipment.id,
        order_id=order.id,
        status=RTOStatus.RECEIVED,
    )
    await db_session.commit()

    await InventoryService(db_session).apply_rto_restock(order_id=order.id, rto_id=rto.id)

    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.available_quantity == 7  # 5 + ceil(120 / 60) = 5 + 2


# --- production-readiness review: ceiling rule, explicit example ----------


async def test_ceiling_example_61_packets_60_per_box_is_2_boxes(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(
        db_session, sku="SKU-CEIL-61", available_quantity=10, packets_per_box=60
    )
    order = await _make_order_with_item(
        db_session, order_number="ORD-CEIL-61", sku="SKU-CEIL-61", quantity=61, product_variant_id=variant.id
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWB-CEIL-61")
    await InventoryService(db_session).apply_dispatch(order_id=order.id, shipment_id=shipment.id)

    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.available_quantity == 8  # 10 - 2 (ceil(61/60) = 2)


# --- product-level Inventory card: ONE per product ----------------------


async def _make_product_with_variants(
    session: AsyncSession, *, key: str, variants: list[dict]
):
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
            available_quantity=spec["available_quantity"],
            packets_per_box=spec.get("packets_per_box", 1),
            inventory_quantity=spec.get("inventory_quantity", 0),
        )
        made.append(variant)
    await session.commit()
    return product, made


async def test_product_stock_endpoint_ungrouped_falls_back_to_implicit_oms_variants(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """Before any CatalogVariant grouping is assigned, each ungrouped
    ProductVariant is its own implicit OMS-visible variant -- behaviour is
    unchanged from before the grouping layer. Product totals sum across
    every underlying row; records are preserved, never merged.
    """
    product, variants = await _make_product_with_variants(
        db_session,
        key="CARD1",
        variants=[
            {"sku": "PK-1", "available_quantity": 300, "packets_per_box": 1},
            {"sku": "PK-2", "available_quantity": 400, "packets_per_box": 1},
            {"sku": "PK-3", "available_quantity": 293, "packets_per_box": 1},
        ],
    )

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as client:
        resp = await client.get(f"/api/v1/inventory/products/{product.id}/stock")
        assert resp.status_code == 200
        body = resp.json()["data"]

        assert body["product_id"] == str(product.id)
        assert body["available_boxes"] == 993  # 300 + 400 + 293, not hardcoded
        assert body["total_packets"] == 993  # Σ boxes * packets_per_box (all 1)
        assert body["packets_per_box_uniform"] is True
        assert body["oms_variant_count"] == 3  # 3 ungrouped -> 3 implicit OMS variants
        assert body["underlying_variant_count"] == 3
        assert all(g["catalog_variant_id"] is None for g in body["oms_variants"])
        underlying = [u for g in body["oms_variants"] for u in g["underlying_variants"]]
        assert {u["sku"] for u in underlying} == {"PK-1", "PK-2", "PK-3"}
        assert body["stock_status"] == "in_stock"


async def test_grouped_oms_variant_flags_mixed_pack_sizes_without_merging_units(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """An OMS variant grouping rows with different `packets_per_box` does
    NOT collapse them to a fabricated ratio: `available_boxes` is a plain
    box sum, `total_packets` sums each row's own boxes*packets_per_box,
    and `packets_per_box_uniform` is False so the UI can say 'mixed pack
    sizes'.
    """
    from app.models.product import CatalogVariant

    product, variants = await _make_product_with_variants(
        db_session,
        key="MIX1",
        variants=[
            {"sku": "MIX-30", "available_quantity": 10, "packets_per_box": 30},
            {"sku": "MIX-60", "available_quantity": 5, "packets_per_box": 60},
            {"sku": "MIX-90", "available_quantity": 2, "packets_per_box": 90},
        ],
    )
    cv = CatalogVariant(product_id=product.id, name="Herbal Masala", display_order=0)
    db_session.add(cv)
    await db_session.flush()
    for v in variants:
        v.catalog_variant_id = cv.id
    await db_session.commit()

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as client:
        body = (
            await client.get(f"/api/v1/inventory/products/{product.id}/stock")
        ).json()["data"]

    assert body["oms_variant_count"] == 1
    assert body["underlying_variant_count"] == 3
    group = body["oms_variants"][0]
    assert group["available_boxes"] == 17  # 10 + 5 + 2 boxes
    assert group["total_packets"] == 10 * 30 + 5 * 60 + 2 * 90  # 780
    assert group["packets_per_box_uniform"] is False
    assert "packets_per_box" not in group  # no single ratio invented for the group
    per_variant = {u["sku"]: u for u in group["underlying_variants"]}
    assert per_variant["MIX-30"]["packets_per_box"] == 30
    assert per_variant["MIX-60"]["packets_per_box"] == 60


async def test_product_level_adjust_single_variant_creates_one_movement(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    _, [variant] = await _make_product_with_variants(
        db_session, key="ADJ1", variants=[{"sku": "ADJ-1", "available_quantity": 395}]
    )
    product = await ProductRepository(db_session).get_by_source_external_id(
        source_system="shopify", external_id="prod-ADJ1"
    )

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read", "inventory.manage"]
    ) as client:
        resp = await client.post(
            f"/api/v1/inventory/products/{product.id}/adjust",
            json={"target_boxes": 400, "reason": "Stock received"},
        )
        assert resp.status_code == 200
        mv = resp.json()["data"]
        assert mv["quantity_delta"] == 5
        assert mv["previous_balance"] == 395
        assert mv["quantity_after"] == 400
        assert mv["movement_type"] == "manual_adjustment"
        assert mv["actor_label"]

        # negative rejected
        neg = await client.post(
            f"/api/v1/inventory/products/{product.id}/adjust",
            json={"target_boxes": -1, "reason": "bad"},
        )
        assert neg.status_code == 422

        movements = (
            await client.get(
                "/api/v1/inventory/movements", params={"product_id": str(product.id)}
            )
        ).json()["data"]
        assert len(movements) == 1
        assert movements[0]["quantity_delta"] == 5

    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.available_quantity == 400


async def test_product_level_adjust_rejects_multi_variant_product(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """No invented product-level distribution rule: a multi-variant
    product's stock is edited per variant, so the product-level adjust
    endpoint refuses it (422) and changes nothing.
    """
    product, variants = await _make_product_with_variants(
        db_session,
        key="MULTIADJ",
        variants=[
            {"sku": "MA-1", "available_quantity": 100},
            {"sku": "MA-2", "available_quantity": 200},
        ],
    )

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.manage"]
    ) as client:
        resp = await client.post(
            f"/api/v1/inventory/products/{product.id}/adjust",
            json={"target_boxes": 500, "reason": "should be rejected"},
        )
        assert resp.status_code == 422
        assert "multiple variants" in resp.text.lower()

        # per-variant adjust still works for the same product
        ok = await client.post(
            f"/api/v1/inventory/stock/{variants[0].id}/adjust",
            json={"target_boxes": 120, "reason": "per-variant edit"},
        )
        assert ok.status_code == 200

    fresh = [await ProductVariantRepository(db_session).get_by_id(v.id) for v in variants]
    assert [v.available_quantity for v in fresh] == [120, 200]  # only the one row moved


async def test_product_stock_endpoint_requires_inventory_read(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _ = await _make_product_with_variants(
        db_session, key="PERM1", variants=[{"sku": "PERM-1", "available_quantity": 5}]
    )
    async with await make_authenticated_client(
        db_session, permission_codes=["analytics.read"], email="nope@example.com"
    ) as client:
        resp = await client.get(f"/api/v1/inventory/products/{product.id}/stock")
        assert resp.status_code == 403
