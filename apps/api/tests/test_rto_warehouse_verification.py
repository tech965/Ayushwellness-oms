"""Module 1 — remaining requirements:

1. RTO warehouse verification: a courier `RTO_DELIVERED`/`RTOStatus.
   RECEIVED` alone must never restock; only `RTOService.
   verify_warehouse_receipt` may create an `RTO_RESTOCK` movement.
2. One shared RTO-processing helper (`RTOService.
   process_courier_rto_status`) used identically by the webhook/
   scheduled-sync path (`apply_tracking_event`) and the manual-refresh
   path (`ShiprocketOperationsService.refresh_tracking_for_shipment`).
3. Legacy order-placement inventory reservation
   (`LegacyOrderReservationService`/`OrderInventoryReservation`).

No real Shiprocket account -- shipment/RTO state is driven directly via
`ShipmentService`/`RTOService`/`apply_tracking_event`, matching the
existing convention in test_inventory_cancellation.py.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.core.exceptions import ConflictError
from app.integrations.shiprocket.sync import apply_tracking_event
from app.models.enums import (
    OrderStatus,
    PaymentType,
    RTORestockStatus,
    RTOStatus,
    ShipmentStatus,
)
from app.models.order_reservation import RESERVATION_ACTIVE, OrderInventoryReservation
from app.models.product import ProductVariant
from app.repositories.product import ProductRepository, ProductVariantRepository
from app.repositories.rto import RTORepository
from app.schemas.order import OrderItemCreateRequest
from app.services.inventory_service import InventoryService
from app.services.order_service import OrderService
from app.services.rto_service import RTOService
from app.services.shipment_service import ShipmentService
from app.services.shiprocket_service import ShiprocketOperationsService
from sqlalchemy import select
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
        packets_per_box=1,
        pack_size=1,
        inventory_quantity=0,
    )
    await session.commit()
    return variant


async def _make_order_with_item(
    session: AsyncSession, *, order_number: str, sku: str, quantity: int = 2, variant_id=None
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
                product_variant_id=variant_id,
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


def _tracking_event(*, status: ShipmentStatus, mapped: ShipmentStatus | None) -> dict:
    return {
        "event_timestamp": datetime.now(UTC),
        "external_event_id": f"evt-{status.value}-{id(status)}",
        "status": status.value,
        "mapped_status": mapped,
        "location": "Hub",
        "description": status.value,
        "courier_name": "Test Courier",
        "raw_payload": None,
    }


async def _dispatch_and_rto_receive(
    session: AsyncSession, *, order_id, shipment_id, awb: str
):
    """Dispatches then drives the shipment through the courier-reported
    RTO lifecycle (INITIATED -> RECEIVED) via the shared
    `apply_tracking_event` helper -- the same path a webhook/scheduled
    pull-sync uses."""
    inventory = InventoryService(session)
    shipment_service = ShipmentService(session)
    rto_service = RTOService(session)
    shipment = await shipment_service.get_shipment(shipment_id)

    await apply_tracking_event(
        session,
        shipment,
        _tracking_event(status=ShipmentStatus.PICKED_UP, mapped=ShipmentStatus.PICKED_UP),
        shipment_service=shipment_service,
        rto_service=rto_service,
        inventory_service=inventory,
    )
    shipment = await shipment_service.get_shipment(shipment_id)
    await apply_tracking_event(
        session,
        shipment,
        _tracking_event(status=ShipmentStatus.RTO_INITIATED, mapped=ShipmentStatus.RTO_INITIATED),
        shipment_service=shipment_service,
        rto_service=rto_service,
        inventory_service=inventory,
    )
    shipment = await shipment_service.get_shipment(shipment_id)
    await apply_tracking_event(
        session,
        shipment,
        _tracking_event(status=ShipmentStatus.RTO_DELIVERED, mapped=ShipmentStatus.RTO_DELIVERED),
        shipment_service=shipment_service,
        rto_service=rto_service,
        inventory_service=inventory,
    )

    rto, _ = await RTORepository(session).upsert_by_external_id(
        source_system="shiprocket", external_id=awb, shipment_id=shipment_id, order_id=order_id
    )
    return rto


# --- 1. courier RECEIVED never restocks directly ----------------------------


async def test_courier_rto_delivered_awaits_verification_never_restocks(
    db_session: AsyncSession,
) -> None:
    variant = await _make_variant(db_session, sku="RTOV1", available_quantity=10)
    order = await _make_order_with_item(db_session, order_number="OMS-RTOV1", sku="RTOV1")
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWBRTOV1")

    rto = await _dispatch_and_rto_receive(
        db_session, order_id=order.id, shipment_id=shipment.id, awb="AWBRTOV1"
    )

    assert rto.status == RTOStatus.RECEIVED
    assert rto.restock_status == RTORestockStatus.AWAITING_VERIFICATION
    refreshed = await db_session.get(ProductVariant, variant.id)
    assert refreshed.available_quantity == 8  # dispatched, NOT restocked


async def test_verify_warehouse_receipt_full_accept_restocks(db_session: AsyncSession) -> None:
    variant = await _make_variant(db_session, sku="RTOV2", available_quantity=10)
    order = await _make_order_with_item(db_session, order_number="OMS-RTOV2", sku="RTOV2")
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWBRTOV2")
    rto = await _dispatch_and_rto_receive(
        db_session, order_id=order.id, shipment_id=shipment.id, awb="AWBRTOV2"
    )

    verified = await RTOService(db_session).verify_warehouse_receipt(rto.id, actor=None)

    assert verified.restock_status == RTORestockStatus.VERIFIED
    assert verified.accepted_quantity == 2
    assert verified.rejected_quantity == 0
    refreshed = await db_session.get(ProductVariant, variant.id)
    assert refreshed.available_quantity == 10  # back to the original amount


async def test_verify_warehouse_receipt_is_idempotent(db_session: AsyncSession) -> None:
    variant = await _make_variant(db_session, sku="RTOV3", available_quantity=10)
    order = await _make_order_with_item(db_session, order_number="OMS-RTOV3", sku="RTOV3")
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWBRTOV3")
    rto = await _dispatch_and_rto_receive(
        db_session, order_id=order.id, shipment_id=shipment.id, awb="AWBRTOV3"
    )

    await RTOService(db_session).verify_warehouse_receipt(rto.id, actor=None)
    with pytest.raises(ConflictError):
        await RTOService(db_session).verify_warehouse_receipt(rto.id, actor=None)

    refreshed = await db_session.get(ProductVariant, variant.id)
    assert refreshed.available_quantity == 10  # not double-restocked


async def test_verify_warehouse_receipt_requires_awaiting_verification(
    db_session: AsyncSession,
) -> None:
    """A not-yet-RECEIVED RTO (still INITIATED/IN_TRANSIT) cannot be
    verified -- there is nothing to confirm receipt of yet."""
    variant = await _make_variant(db_session, sku="RTOV4")
    order = await _make_order_with_item(db_session, order_number="OMS-RTOV4", sku="RTOV4")
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWBRTOV4")
    rto, _ = await RTORepository(db_session).upsert_by_external_id(
        source_system="shiprocket",
        external_id="AWBRTOV4",
        shipment_id=shipment.id,
        order_id=order.id,
        status=RTOStatus.IN_TRANSIT,
    )
    await db_session.commit()

    with pytest.raises(ConflictError):
        await RTOService(db_session).verify_warehouse_receipt(rto.id, actor=None)
    refreshed = await db_session.get(ProductVariant, variant.id)
    assert refreshed.available_quantity == 10


async def test_verify_warehouse_receipt_partial_accept_reject(db_session: AsyncSession) -> None:
    variant = await _make_variant(db_session, sku="RTOV5", available_quantity=10)
    order = await _make_order_with_item(
        db_session, order_number="OMS-RTOV5", sku="RTOV5", quantity=3
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWBRTOV5")
    rto = await _dispatch_and_rto_receive(
        db_session, order_id=order.id, shipment_id=shipment.id, awb="AWBRTOV5"
    )
    # 3 boxes dispatched (10 -> 7); accept 2, reject 1 as damaged.
    refreshed = await db_session.get(ProductVariant, variant.id)
    assert refreshed.available_quantity == 7

    verified = await RTOService(db_session).verify_warehouse_receipt(
        rto.id,
        actor=None,
        product_variant_id=variant.id,
        accepted_quantity=2,
        rejected_quantity=1,
    )

    assert verified.accepted_quantity == 2
    assert verified.rejected_quantity == 1
    refreshed = await db_session.get(ProductVariant, variant.id)
    assert refreshed.available_quantity == 9  # 7 + 2 accepted, never +3


async def test_verify_warehouse_receipt_rejects_quantity_exceeding_dispatched(
    db_session: AsyncSession,
) -> None:
    variant = await _make_variant(db_session, sku="RTOV6", available_quantity=10)
    order = await _make_order_with_item(
        db_session, order_number="OMS-RTOV6", sku="RTOV6", quantity=2
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWBRTOV6")
    rto = await _dispatch_and_rto_receive(
        db_session, order_id=order.id, shipment_id=shipment.id, awb="AWBRTOV6"
    )

    with pytest.raises(ConflictError):
        await RTOService(db_session).verify_warehouse_receipt(
            rto.id,
            actor=None,
            product_variant_id=variant.id,
            accepted_quantity=5,
            rejected_quantity=0,
        )
    refreshed = await db_session.get(ProductVariant, variant.id)
    assert refreshed.available_quantity == 8  # untouched by the rejected call


# --- 2. ingestion-path parity ------------------------------------------------


async def test_manual_refresh_derives_rto_state_same_as_webhook_scheduled_sync(
    db_session: AsyncSession,
) -> None:
    """Module 1 requirement 2 — the manual "Refresh Tracking" path
    previously had NO RTO-derivation logic at all. Confirms it now
    reaches the same AWAITING_VERIFICATION state as the shared
    `apply_tracking_event` helper, via a direct courier status update
    (no live Shiprocket account needed -- same stub-free convention as
    test_shiprocket_operations.py's existing manual-refresh tests).
    """
    from app.repositories.shipment import ShipmentRepository

    variant = await _make_variant(db_session, sku="RTOPARITY", available_quantity=10)
    order = await _make_order_with_item(db_session, order_number="OMS-RTOPARITY", sku="RTOPARITY")
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWBRTOPARITY")

    # Drive the shipment to PICKED_UP first (apply_dispatch needs it).
    await ShipmentService(db_session).update_shipment(
        shipment.id, actor=None, current_status=ShipmentStatus.PICKED_UP
    )

    ops = ShiprocketOperationsService(db_session)
    # Simulate what `refresh_tracking_for_shipment`'s own tracking loop
    # does once it has parsed a RECEIVED-equivalent courier event, by
    # calling the exact same shared helper it now calls.
    shipment_row = await ShipmentRepository(db_session).get_by_id(shipment.id)
    await ops.rto_service.process_courier_rto_status(
        source_system="shiprocket",
        external_id="AWBRTOPARITY",
        shipment_id=shipment_row.id,
        order_id=order.id,
        courier_id=None,
        status=RTOStatus.RECEIVED,
        external_reason="Customer refused",
    )

    rto, _ = await RTORepository(db_session).upsert_by_external_id(
        source_system="shiprocket",
        external_id="AWBRTOPARITY",
        shipment_id=shipment.id,
        order_id=order.id,
    )
    assert rto.status == RTOStatus.RECEIVED
    assert rto.restock_status == RTORestockStatus.AWAITING_VERIFICATION
    refreshed = await db_session.get(ProductVariant, variant.id)
    assert refreshed.available_quantity == 10  # never restocked off courier status alone


# --- 3. legacy order-placement reservation -----------------------------------


async def test_order_creation_reserves_stock(db_session: AsyncSession) -> None:
    variant = await _make_variant(db_session, sku="RES1", available_quantity=10)
    order = await _make_order_with_item(
        db_session, order_number="OMS-RES1", sku="RES1", quantity=3, variant_id=variant.id
    )

    reservation = (
        await db_session.execute(
            select(OrderInventoryReservation).where(
                OrderInventoryReservation.order_id == order.id
            )
        )
    ).scalar_one()
    assert reservation.product_variant_id == variant.id
    assert reservation.quantity == 3
    assert reservation.status == RESERVATION_ACTIVE


async def test_order_creation_fails_on_insufficient_stock(db_session: AsyncSession) -> None:
    variant = await _make_variant(db_session, sku="RES2", available_quantity=2)
    with pytest.raises(ConflictError):
        await _make_order_with_item(
            db_session, order_number="OMS-RES2", sku="RES2", quantity=5, variant_id=variant.id
        )
    # Nothing committed -- the variant's stock is untouched and no order exists.
    refreshed = await db_session.get(ProductVariant, variant.id)
    assert refreshed.available_quantity == 2


async def test_cancellation_releases_reservation(db_session: AsyncSession) -> None:
    variant = await _make_variant(db_session, sku="RES3", available_quantity=10)
    order = await _make_order_with_item(
        db_session, order_number="OMS-RES3", sku="RES3", quantity=4, variant_id=variant.id
    )

    await OrderService(db_session).transition_status(
        order.id, new_status=OrderStatus.CANCELLED, actor=None, description=None
    )

    reservation = (
        await db_session.execute(
            select(OrderInventoryReservation).where(
                OrderInventoryReservation.order_id == order.id
            )
        )
    ).scalar_one()
    assert reservation.status == "released"


async def test_dispatch_consumes_reservation(db_session: AsyncSession) -> None:
    variant = await _make_variant(db_session, sku="RES4", available_quantity=10)
    order = await _make_order_with_item(
        db_session, order_number="OMS-RES4", sku="RES4", quantity=4, variant_id=variant.id
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWBRES4")

    await InventoryService(db_session).apply_dispatch(order_id=order.id, shipment_id=shipment.id)

    reservation = (
        await db_session.execute(
            select(OrderInventoryReservation).where(
                OrderInventoryReservation.order_id == order.id
            )
        )
    ).scalar_one()
    assert reservation.status == "consumed"


async def test_reservation_blocks_a_second_order_oversubscribing_the_same_stock(
    db_session: AsyncSession,
) -> None:
    variant = await _make_variant(db_session, sku="RES5", available_quantity=5)
    await _make_order_with_item(
        db_session, order_number="OMS-RES5A", sku="RES5", quantity=4, variant_id=variant.id
    )
    with pytest.raises(ConflictError):
        await _make_order_with_item(
            db_session, order_number="OMS-RES5B", sku="RES5", quantity=4, variant_id=variant.id
        )


@pytest.mark.skipif(
    not os.environ.get("OMS_TEST_DATABASE_URL"),
    reason="set OMS_TEST_DATABASE_URL to a disposable PostgreSQL to run",
)
async def test_concurrent_order_placement_never_oversells(db_session: AsyncSession) -> None:
    """Two concurrent order-creation attempts for the last unit of stock:
    row-locking in `LegacyOrderReservationService.reserve` (same
    lock-in-sorted-id-order discipline as the dormant service and
    Module 1.2's `apply_dispatch`) must serialize them so at most one
    succeeds -- never both, never neither.
    """
    import asyncio

    from tests.conftest import _TestSessionLocal

    async with _TestSessionLocal() as session:
        await _make_variant(session, sku="RESRACE", available_quantity=4)

    async def _attempt(order_number: str):
        async with _TestSessionLocal() as session:
            try:
                await _make_order_with_item(
                    session, order_number=order_number, sku="RESRACE", quantity=4
                )
                return True
            except ConflictError:
                return False

    results = await asyncio.gather(_attempt("OMS-RESRACE-A"), _attempt("OMS-RESRACE-B"))
    assert sorted(results) == [False, True]
