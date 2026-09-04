"""InventoryService: dispatch decrement, RTO restock, manual adjustment.

Idempotency is the highest-risk area here -- `apply_dispatch`/
`apply_rto_restock` can legitimately be called more than once for the
same order (repeated tracking events, a pull-sync re-scan, a shipment
advancing through several dispatched-or-later statuses), and must only
ever move stock once per (order, variant, movement type).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.integrations.shiprocket.sync import apply_tracking_event
from app.models.enums import (
    InventoryMovementType,
    PaymentType,
    RTOStatus,
    ShipmentStatus,
)
from app.repositories.product import ProductRepository, ProductVariantRepository
from app.repositories.rto import RTORepository
from app.schemas.order import OrderItemCreateRequest
from app.services.inventory_service import InventoryService
from app.services.order_service import OrderService
from app.services.rto_service import RTOService
from app.services.shipment_service import ShipmentService
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _make_variant(session: AsyncSession, *, sku: str, available_quantity: int = 10):
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
    return variant


async def _make_order_with_item(
    session: AsyncSession, *, order_number: str, sku: str, quantity: int, product_variant_id=None
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
                product_variant_id=product_variant_id,
                sku=sku,
                product_name=f"Product {sku}",
                quantity=quantity,
                unit_price=Decimal("100.00"),
            )
        ],
    )
    return order


async def test_apply_dispatch_decrements_stock_once(db_session: AsyncSession) -> None:
    variant = await _make_variant(db_session, sku="SKU-1", available_quantity=10)
    order = await _make_order_with_item(
        db_session, order_number="ORD-1", sku="SKU-1", quantity=3, product_variant_id=variant.id
    )
    shipment = await ShipmentService(db_session).create_shipment(
        actor=None, order_id=order.id, awb="AWB-1", courier_id=None, expected_delivery_date=None
    )

    service = InventoryService(db_session)
    await service.apply_dispatch(order_id=order.id, shipment_id=shipment.id)
    # Repeated call -- same order, same shipment (the real pattern when a
    # shipment advances PICKED_UP -> IN_TRANSIT -> DELIVERED and each
    # transition re-triggers `apply_dispatch`).
    await service.apply_dispatch(order_id=order.id, shipment_id=shipment.id)

    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.available_quantity == 7

    movements, total = await service.list_movements(
        page_params=_page_params(), sort_params=_sort_params()
    )
    dispatch_movements = [m for m in movements if m.movement_type == InventoryMovementType.DISPATCH]
    assert len(dispatch_movements) == 1
    assert dispatch_movements[0].quantity_delta == -3


async def test_apply_dispatch_resolves_by_sku_when_variant_id_missing(
    db_session: AsyncSession,
) -> None:
    variant = await _make_variant(db_session, sku="SKU-2", available_quantity=5)
    order = await _make_order_with_item(
        db_session, order_number="ORD-2", sku="SKU-2", quantity=2, product_variant_id=None
    )
    shipment = await ShipmentService(db_session).create_shipment(
        actor=None, order_id=order.id, awb="AWB-2", courier_id=None, expected_delivery_date=None
    )

    await InventoryService(db_session).apply_dispatch(order_id=order.id, shipment_id=shipment.id)

    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.available_quantity == 3


async def test_apply_dispatch_skips_unresolvable_sku_without_raising(
    db_session: AsyncSession,
) -> None:
    order = await _make_order_with_item(
        db_session, order_number="ORD-3", sku="SKU-DOES-NOT-EXIST", quantity=1
    )
    shipment = await ShipmentService(db_session).create_shipment(
        actor=None, order_id=order.id, awb="AWB-3", courier_id=None, expected_delivery_date=None
    )

    # Must not raise.
    await InventoryService(db_session).apply_dispatch(order_id=order.id, shipment_id=shipment.id)


async def test_apply_rto_restock_increments_stock_once(db_session: AsyncSession) -> None:
    variant = await _make_variant(db_session, sku="SKU-4", available_quantity=5)
    order = await _make_order_with_item(
        db_session, order_number="ORD-4", sku="SKU-4", quantity=2, product_variant_id=variant.id
    )
    shipment = await ShipmentService(db_session).create_shipment(
        actor=None, order_id=order.id, awb="AWB-4", courier_id=None, expected_delivery_date=None
    )
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
    assert refreshed.available_quantity == 7


async def test_manual_rto_status_update_triggers_restock(db_session: AsyncSession) -> None:
    variant = await _make_variant(db_session, sku="SKU-5", available_quantity=4)
    order = await _make_order_with_item(
        db_session, order_number="ORD-5", sku="SKU-5", quantity=1, product_variant_id=variant.id
    )
    shipment = await ShipmentService(db_session).create_shipment(
        actor=None, order_id=order.id, awb="AWB-5", courier_id=None, expected_delivery_date=None
    )
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


async def test_apply_tracking_event_dispatches_on_picked_up_only_once(
    db_session: AsyncSession,
) -> None:
    variant = await _make_variant(db_session, sku="SKU-6", available_quantity=8)
    order = await _make_order_with_item(
        db_session, order_number="ORD-6", sku="SKU-6", quantity=2, product_variant_id=variant.id
    )
    shipment = await ShipmentService(db_session).create_shipment(
        actor=None, order_id=order.id, awb="AWB-6", courier_id=None, expected_delivery_date=None
    )

    shipment_service = ShipmentService(db_session)
    rto_service = RTOService(db_session)
    inventory_service = InventoryService(db_session)

    for status, mapped in (
        (ShipmentStatus.PICKED_UP, ShipmentStatus.PICKED_UP),
        (ShipmentStatus.IN_TRANSIT, ShipmentStatus.IN_TRANSIT),
    ):
        await apply_tracking_event(
            db_session,
            shipment,
            {
                "event_timestamp": datetime.now(UTC),
                "external_event_id": f"evt-{status.value}",
                "status": status.value,
                "mapped_status": mapped,
                "location": "Hub",
                "description": status.value,
                "courier_name": "Test Courier",
                "raw_payload": None,
            },
            shipment_service=shipment_service,
            rto_service=rto_service,
            inventory_service=inventory_service,
        )

    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.available_quantity == 6


async def test_manual_adjustment_endpoint(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    variant = await _make_variant(db_session, sku="SKU-7", available_quantity=10)

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read", "inventory.manage"]
    ) as auth_client:
        response = await auth_client.post(
            f"/api/v1/inventory/stock/{variant.id}/adjust",
            json={"delta": -2, "reason": "Damaged in warehouse"},
        )
        assert response.status_code == 200
        assert response.json()["data"]["quantity_after"] == 8

        stock = await auth_client.get(f"/api/v1/inventory/stock/{variant.id}")
        assert stock.status_code == 200
        assert stock.json()["data"]["available_quantity"] == 8


async def test_list_stock_endpoint_search_and_low_stock_filter(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    in_stock = await _make_variant(db_session, sku="SKU-8-INSTOCK", available_quantity=5)
    out_of_stock = await _make_variant(db_session, sku="SKU-8-OUT", available_quantity=0)

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as auth_client:
        by_sku = await auth_client.get("/api/v1/inventory/stock", params={"q": "INSTOCK"})
        assert by_sku.status_code == 200
        skus = {row["sku"] for row in by_sku.json()["data"]}
        assert skus == {"SKU-8-INSTOCK"}

        low_stock = await auth_client.get(
            "/api/v1/inventory/stock", params={"low_stock_only": "true"}
        )
        assert low_stock.status_code == 200
        low_stock_ids = {row["id"] for row in low_stock.json()["data"]}
        assert str(out_of_stock.id) in low_stock_ids
        assert str(in_stock.id) not in low_stock_ids


def _page_params():
    from app.schemas.common import PageParams

    return PageParams(page=1, page_size=50)


def _sort_params():
    from app.schemas.common import SortParams

    return SortParams(sort_by=None, sort_order="desc")
