"""Module 1.1: the inventory dashboard — every figure is a live aggregate
over `ProductVariant`/`InventoryMovement`/`RTO` (the legacy architecture
Module 1.2 also operates on), never a hardcoded or sample value, and
never the separate IMS module's own warehouse ledger.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.models.enums import PaymentType, ProductStatus, RTOStatus
from app.models.product import ProductVariant
from app.repositories.product import ProductRepository, ProductVariantRepository
from app.schemas.order import OrderItemCreateRequest
from app.services.inventory_service import InventoryService
from app.services.order_service import OrderService
from app.services.rto_service import RTOService
from app.services.shipment_service import ShipmentService
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _make_variant(
    session: AsyncSession,
    *,
    sku: str,
    available_quantity: int = 10,
    packets_per_box: int = 2,
    status: ProductStatus = ProductStatus.ACTIVE,
):
    product, _ = await ProductRepository(session).upsert_by_external_id(
        source_system="shopify", external_id=f"prod-{sku}", title=f"Product {sku}", status=status
    )
    variant, _ = await ProductVariantRepository(session).upsert_by_external_id(
        source_system="shopify",
        external_id=f"var-{sku}",
        product_id=product.id,
        sku=sku,
        price=Decimal("100.00"),
        available_quantity=available_quantity,
        packets_per_box=packets_per_box,
        pack_size=1,
    )
    await session.commit()
    return product, variant


async def _make_order_with_item(
    session: AsyncSession, *, order_number: str, sku: str, quantity: int = 1
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
                product_variant_id=None,
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


# --- 1. totals -----------------------------------------------------------


async def test_dashboard_totals_reflect_actual_variants_and_exclude_drafts(
    db_session: AsyncSession,
) -> None:
    await _make_variant(db_session, sku="DASH1", available_quantity=5, packets_per_box=60)
    await _make_variant(db_session, sku="DASH2", available_quantity=3, packets_per_box=10)
    await _make_variant(
        db_session, sku="DASH-DRAFT", available_quantity=999, status=ProductStatus.DRAFT
    )

    dashboard = await InventoryService(db_session).get_dashboard(date_from=None, date_to=None)

    assert dashboard["total_products"] == 2  # draft excluded
    assert dashboard["total_skus"] == 2
    assert dashboard["total_available_boxes"] == 8  # 5 + 3, draft's 999 excluded
    assert dashboard["total_available_packets"] == 330  # 5*60 + 3*10


# --- 2. opening stock, derived not stored ---------------------------------


async def test_opening_stock_is_derived_from_current_minus_all_time_delta(
    db_session: AsyncSession,
) -> None:
    _, variant = await _make_variant(db_session, sku="DASH3", available_quantity=20)
    order = await _make_order_with_item(
        db_session, order_number="OMS-DASH3", sku="DASH3", quantity=2
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWBDASH3")

    # A dispatch (-2 boxes, since packets_per_box=2 default here means
    # quantity=2 packets / packets_per_box=2 -> 1 box) and a manual add
    # (+5 boxes) -- opening stock must equal today's total MINUS both of
    # these, i.e. the value before either was ever recorded.
    await InventoryService(db_session).apply_dispatch(order_id=order.id, shipment_id=shipment.id)
    await InventoryService(db_session).add_stock(
        variant.id, quantity_to_add=5, reason="test restock", actor=None
    )

    refreshed = await db_session.get(ProductVariant, variant.id)
    dashboard = await InventoryService(db_session).get_dashboard(date_from=None, date_to=None)

    all_time_delta = refreshed.available_quantity - 20  # net change vs. the original seed
    assert dashboard["opening_stock_boxes"] == dashboard["total_available_boxes"] - all_time_delta
    assert dashboard["opening_stock_boxes"] == 20  # the value before any movement existed


# --- 3. inward/outward scoped to a date range, never combined with opening


async def test_inward_outward_scoped_to_date_range_and_never_mixed_with_opening(
    db_session: AsyncSession,
) -> None:
    _, variant = await _make_variant(db_session, sku="DASH4", available_quantity=10)

    far_past = datetime(2020, 1, 1, tzinfo=UTC)
    await InventoryService(db_session).add_stock(
        variant.id, quantity_to_add=7, reason="old stock, out of range", actor=None
    )

    now = datetime.now(UTC)
    window_from, window_to = now - timedelta(minutes=5), now + timedelta(minutes=5)
    dashboard_in_range = await InventoryService(db_session).get_dashboard(
        date_from=window_from, date_to=window_to
    )
    dashboard_all_time = await InventoryService(db_session).get_dashboard(
        date_from=far_past, date_to=now + timedelta(days=1)
    )

    # the add_stock just happened, in-window
    assert dashboard_in_range["inward_boxes_in_range"] == 7
    assert dashboard_all_time["inward_boxes_in_range"] == 7
    # opening_stock_boxes is all-time/unscoped and must be identical regardless
    # of the date window used for the in-range figures.
    assert dashboard_in_range["opening_stock_boxes"] == dashboard_all_time["opening_stock_boxes"]
    assert dashboard_in_range["opening_stock_boxes"] == 10


async def test_outward_is_reported_as_a_positive_magnitude(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(
        db_session, sku="DASH5", available_quantity=10, packets_per_box=1
    )
    order = await _make_order_with_item(
        db_session, order_number="OMS-DASH5", sku="DASH5", quantity=4
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWBDASH5")
    await InventoryService(db_session).apply_dispatch(order_id=order.id, shipment_id=shipment.id)

    dashboard = await InventoryService(db_session).get_dashboard(date_from=None, date_to=None)
    assert dashboard["outward_boxes_in_range"] == 4  # positive, not -4
    assert dashboard["inward_boxes_in_range"] == 0


# --- 4. RTO-in-transit, no double counting --------------------------------


async def test_rto_in_transit_counts_dispatched_not_yet_restocked(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(
        db_session, sku="DASH6", available_quantity=10, packets_per_box=1
    )
    order = await _make_order_with_item(
        db_session, order_number="OMS-DASH6", sku="DASH6", quantity=3
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWBDASH6")
    await InventoryService(db_session).apply_dispatch(order_id=order.id, shipment_id=shipment.id)

    rto, _ = await RTOService(db_session).upsert_synced_rto(
        source_system="shiprocket",
        external_id=shipment.awb,
        shipment_id=shipment.id,
        order_id=order.id,
        courier_id=None,
        status=RTOStatus.INITIATED,
        external_reason="Customer refused",
    )

    dashboard = await InventoryService(db_session).get_dashboard(date_from=None, date_to=None)
    assert dashboard["rto_in_transit_boxes"] == 3
    assert dashboard["rto_in_transit_count"] == 1

    # Once actually restocked, it must drop out of "in transit" -- the
    # stock is already back in available_quantity, so counting it here
    # too would double-count it.
    await InventoryService(db_session).apply_rto_restock(order_id=order.id, rto_id=rto.id)
    after_restock = await InventoryService(db_session).get_dashboard(date_from=None, date_to=None)
    assert after_restock["rto_in_transit_boxes"] == 0
    assert after_restock["rto_in_transit_count"] == 0


async def test_rto_not_yet_initiated_is_not_counted_in_transit(db_session: AsyncSession) -> None:
    _, variant = await _make_variant(
        db_session, sku="DASH7", available_quantity=10, packets_per_box=1
    )
    order = await _make_order_with_item(
        db_session, order_number="OMS-DASH7", sku="DASH7", quantity=2
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWBDASH7")
    await InventoryService(db_session).apply_dispatch(order_id=order.id, shipment_id=shipment.id)
    # No RTO row at all for this order -- a normal, still-in-transit-to-
    # the-customer dispatch must never be mistaken for an RTO.

    dashboard = await InventoryService(db_session).get_dashboard(date_from=None, date_to=None)
    assert dashboard["rto_in_transit_boxes"] == 0
    assert dashboard["rto_in_transit_count"] == 0


# --- 5. low/out-of-stock counts -------------------------------------------


async def test_low_and_out_of_stock_counts(db_session: AsyncSession) -> None:
    await _make_variant(db_session, sku="DASH8", available_quantity=0)  # out of stock
    # low stock (default threshold=5)
    await _make_variant(db_session, sku="DASH9", available_quantity=5)
    await _make_variant(db_session, sku="DASH10", available_quantity=50)  # healthy

    dashboard = await InventoryService(db_session).get_dashboard(date_from=None, date_to=None)
    assert dashboard["out_of_stock_count"] == 1
    assert dashboard["low_stock_count"] == 1


# --- 6. HTTP endpoints -----------------------------------------------------


async def test_dashboard_endpoint_returns_live_values(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    await _make_variant(db_session, sku="DASH-HTTP1", available_quantity=9, packets_per_box=3)

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as auth_client:
        response = await auth_client.get("/api/v1/inventory/dashboard")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["total_available_boxes"] >= 9
        assert data["total_available_packets"] >= 27
        assert "opening_stock_boxes" in data
        assert "rto_in_transit_boxes" in data


async def test_movements_endpoint_respects_date_range(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    _, variant = await _make_variant(db_session, sku="DASH-HTTP2", available_quantity=10)
    await InventoryService(db_session).add_stock(
        variant.id, quantity_to_add=1, reason="in range", actor=None
    )

    now = datetime.now(UTC)
    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as auth_client:
        in_range = await auth_client.get(
            "/api/v1/inventory/movements",
            params={
                "product_variant_id": str(variant.id),
                "date_from": (now - timedelta(minutes=5)).isoformat(),
                "date_to": (now + timedelta(minutes=5)).isoformat(),
            },
        )
        out_of_range = await auth_client.get(
            "/api/v1/inventory/movements",
            params={
                "product_variant_id": str(variant.id),
                "date_from": "2020-01-01T00:00:00+00:00",
                "date_to": "2020-01-02T00:00:00+00:00",
            },
        )
    assert in_range.status_code == 200
    assert len(in_range.json()["data"]) == 1
    assert out_of_range.status_code == 200
    assert len(out_of_range.json()["data"]) == 0


async def test_movements_endpoint_sku_search_is_partial_and_case_insensitive(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    _, matching = await _make_variant(db_session, sku="DASH-SKU-MATCH", available_quantity=10)
    _, other = await _make_variant(db_session, sku="DASH-OTHER", available_quantity=10)
    await InventoryService(db_session).add_stock(
        matching.id, quantity_to_add=1, reason="x", actor=None
    )
    await InventoryService(db_session).add_stock(
        other.id, quantity_to_add=1, reason="x", actor=None
    )

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"]
    ) as auth_client:
        response = await auth_client.get(
            "/api/v1/inventory/movements", params={"sku": "sku-match"}
        )
    assert response.status_code == 200
    rows = response.json()["data"]
    assert len(rows) == 1
    assert rows[0]["sku"] == "DASH-SKU-MATCH"
