"""Module 1.2: order cancellation vs. confirmed physical dispatch.

Business rule (approved): a confirmed physical dispatch ALWAYS deducts
stock, regardless of `Order.status` -- a late webhook or a courier
picking up a package after the OMS already cancelled the order must not
silently lose that stock movement. Each such movement is additionally
flagged (structured log + `AuditLog`,
`action="inventory.dispatch_after_cancellation"`) for manual
reconciliation. Symmetrically, an ORDINARY cancellation attempt is
blocked once the shipment has reached a confirmed-pickup-or-later
status, consistent with `unconfirm_order`'s existing protection, for
both cancellation entry points (`transition_status` and
`upsert_synced_order`'s Shopify-driven auto-cancel).

No real Shiprocket account; shipment status is set directly via
`ShipmentService` to simulate what a webhook/pull-sync/manual-refresh
would otherwise produce (already covered by test_shiprocket_sync.py /
test_shiprocket_webhooks.py / test_shiprocket_operations.py -- this file
is scoped to the order-cancellation interaction only).
"""

from __future__ import annotations

import contextlib
import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.core.exceptions import ConflictError
from app.models.audit_log import AuditLog
from app.models.enums import (
    InventoryMovementType,
    OrderStatus,
    PaymentStatus,
    PaymentType,
    RTOStatus,
    ShipmentStatus,
)
from app.models.inventory import InventoryMovement
from app.models.product import ProductVariant
from app.repositories.inventory import InventoryMovementRepository
from app.repositories.order import OrderRepository
from app.repositories.product import ProductRepository, ProductVariantRepository
from app.schemas.order import OrderItemCreateRequest
from app.services.inventory_service import InventoryService
from app.services.order_service import OrderService
from app.services.rto_service import RTOService
from app.services.shipment_service import ShipmentService
from sqlalchemy import func, select
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
    session: AsyncSession, *, order_number: str, sku: str, quantity: int = 2
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


async def _dispatch_audit_rows(session: AsyncSession, order_id) -> list[AuditLog]:
    rows = await session.execute(
        select(AuditLog).where(
            AuditLog.entity_id == str(order_id),
            AuditLog.action == "inventory.dispatch_after_cancellation",
        )
    )
    return list(rows.scalars().all())


# --- 1. cancellation before any shipment exists -----------------------------


async def test_cancellation_before_any_shipment_exists_succeeds(db_session: AsyncSession) -> None:
    await _make_variant(db_session, sku="CANCEL1")
    order = await _make_order_with_item(db_session, order_number="OMS-CANCEL1", sku="CANCEL1")

    cancelled = await OrderService(db_session).transition_status(
        order.id, new_status=OrderStatus.CANCELLED, actor=None, description=None
    )
    assert cancelled.status == OrderStatus.CANCELLED


# --- 2. cancellation while a shipment exists but is still PENDING -----------


async def test_cancellation_while_shipment_pending_succeeds(db_session: AsyncSession) -> None:
    await _make_variant(db_session, sku="CANCEL2")
    order = await _make_order_with_item(db_session, order_number="OMS-CANCEL2", sku="CANCEL2")
    await _make_shipment(db_session, order_id=order.id, awb="AWBCANCEL2")  # defaults to PENDING

    cancelled = await OrderService(db_session).transition_status(
        order.id, new_status=OrderStatus.CANCELLED, actor=None, description=None
    )
    assert cancelled.status == OrderStatus.CANCELLED


# --- 3. cancellation blocked after confirmed pickup -------------------------


async def test_cancellation_blocked_after_confirmed_pickup(db_session: AsyncSession) -> None:
    await _make_variant(db_session, sku="CANCEL3")
    order = await _make_order_with_item(db_session, order_number="OMS-CANCEL3", sku="CANCEL3")
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWBCANCEL3")
    await ShipmentService(db_session).update_shipment(
        shipment.id, actor=None, current_status=ShipmentStatus.PICKED_UP
    )

    with pytest.raises(ConflictError) as exc_info:
        await OrderService(db_session).transition_status(
            order.id, new_status=OrderStatus.CANCELLED, actor=None, description=None
        )
    assert "already reached 'picked_up'" in str(exc_info.value)

    refreshed = await OrderRepository(db_session).get_by_id(order.id)
    assert refreshed.status != OrderStatus.CANCELLED  # unchanged

    audit = await db_session.execute(
        select(AuditLog).where(
            AuditLog.entity_id == str(order.id), AuditLog.action == "order.cancellation_blocked"
        )
    )
    assert audit.scalar_one_or_none() is not None  # attempt+outcome recorded


# --- 4. delayed/late dispatch after cancellation ----------------------------


async def test_dispatch_after_cancellation_still_deducts_and_flags(
    db_session: AsyncSession,
) -> None:
    variant = await _make_variant(db_session, sku="CANCEL4", available_quantity=10)
    order = await _make_order_with_item(
        db_session, order_number="OMS-CANCEL4", sku="CANCEL4", quantity=3
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWBCANCEL4")

    await OrderService(db_session).transition_status(
        order.id, new_status=OrderStatus.CANCELLED, actor=None, description=None
    )

    # A dispatch event now arrives (late webhook, or courier picked up anyway).
    await InventoryService(db_session).apply_dispatch(order_id=order.id, shipment_id=shipment.id)

    refreshed_variant = await db_session.get(ProductVariant, variant.id)
    assert refreshed_variant.available_quantity == 7  # 10 - 3, deducted regardless

    movements = await InventoryMovementRepository(db_session).exists_for_order(
        order_id=order.id,
        product_variant_id=variant.id,
        movement_type=InventoryMovementType.DISPATCH,
    )
    assert movements is True

    flags = await _dispatch_audit_rows(db_session, order.id)
    assert len(flags) == 1
    assert flags[0].new_value["boxes_deducted"] == 3


# --- 5. pickup physically occurring after cancellation (same effect as #4) --


async def test_pickup_physically_after_cancellation_also_deducts(
    db_session: AsyncSession,
) -> None:
    variant = await _make_variant(db_session, sku="CANCEL5", available_quantity=5)
    order = await _make_order_with_item(
        db_session, order_number="OMS-CANCEL5", sku="CANCEL5", quantity=1
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWBCANCEL5")
    await OrderService(db_session).transition_status(
        order.id, new_status=OrderStatus.CANCELLED, actor=None, description=None
    )

    await ShipmentService(db_session).update_shipment(
        shipment.id, actor=None, current_status=ShipmentStatus.PICKED_UP
    )
    await InventoryService(db_session).apply_dispatch(order_id=order.id, shipment_id=shipment.id)

    refreshed_variant = await db_session.get(ProductVariant, variant.id)
    assert refreshed_variant.available_quantity == 4
    assert len(await _dispatch_audit_rows(db_session, order.id)) == 1


# --- 6. duplicate dispatch, including on a cancelled order ------------------


async def test_duplicate_dispatch_on_cancelled_order_deducts_and_flags_once(
    db_session: AsyncSession,
) -> None:
    variant = await _make_variant(db_session, sku="CANCEL6", available_quantity=10)
    order = await _make_order_with_item(
        db_session, order_number="OMS-CANCEL6", sku="CANCEL6", quantity=2
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWBCANCEL6")
    await OrderService(db_session).transition_status(
        order.id, new_status=OrderStatus.CANCELLED, actor=None, description=None
    )

    await InventoryService(db_session).apply_dispatch(order_id=order.id, shipment_id=shipment.id)
    await InventoryService(db_session).apply_dispatch(order_id=order.id, shipment_id=shipment.id)

    refreshed_variant = await db_session.get(ProductVariant, variant.id)
    assert refreshed_variant.available_quantity == 8  # deducted exactly once

    total_movements = await db_session.execute(
        select(func.count())
        .select_from(InventoryMovement)
        .where(InventoryMovement.order_id == order.id)
    )
    assert total_movements.scalar_one() == 1
    assert len(await _dispatch_audit_rows(db_session, order.id)) == 1  # not re-flagged


# --- 7. RTO received after dispatch on a cancelled order --------------------


async def test_rto_after_dispatch_on_cancelled_order_still_restocks(
    db_session: AsyncSession,
) -> None:
    variant = await _make_variant(db_session, sku="CANCEL7", available_quantity=10)
    order = await _make_order_with_item(
        db_session, order_number="OMS-CANCEL7", sku="CANCEL7", quantity=2
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWBCANCEL7")
    await InventoryService(db_session).apply_dispatch(order_id=order.id, shipment_id=shipment.id)
    await OrderService(db_session).transition_status(
        order.id, new_status=OrderStatus.CANCELLED, actor=None, description=None
    )

    rto, _ = await RTOService(db_session).upsert_synced_rto(
        source_system="shiprocket",
        external_id=shipment.awb,
        shipment_id=shipment.id,
        order_id=order.id,
        courier_id=None,
        status=RTOStatus.RECEIVED,
        external_reason="Customer refused",
    )
    await InventoryService(db_session).apply_rto_restock(order_id=order.id, rto_id=rto.id)

    refreshed_variant = await db_session.get(ProductVariant, variant.id)
    assert refreshed_variant.available_quantity == 10  # dispatched then fully restocked


# --- 8. duplicate RTO --------------------------------------------------------


async def test_duplicate_rto_restocks_exactly_once(db_session: AsyncSession) -> None:
    variant = await _make_variant(db_session, sku="CANCEL8", available_quantity=10)
    order = await _make_order_with_item(
        db_session, order_number="OMS-CANCEL8", sku="CANCEL8", quantity=2
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWBCANCEL8")
    await InventoryService(db_session).apply_dispatch(order_id=order.id, shipment_id=shipment.id)
    rto, _ = await RTOService(db_session).upsert_synced_rto(
        source_system="shiprocket",
        external_id=shipment.awb,
        shipment_id=shipment.id,
        order_id=order.id,
        courier_id=None,
        status=RTOStatus.RECEIVED,
        external_reason="Customer refused",
    )

    await InventoryService(db_session).apply_rto_restock(order_id=order.id, rto_id=rto.id)
    await InventoryService(db_session).apply_rto_restock(order_id=order.id, rto_id=rto.id)

    refreshed_variant = await db_session.get(ProductVariant, variant.id)
    assert refreshed_variant.available_quantity == 10  # restocked exactly once


# --- 9. Shopify-driven auto-cancel respects the same guard ------------------


def _shopify_sync_payload(**overrides) -> dict:
    base = {
        "source_system": "shopify",
        "external_id": "shopify-cancel9",
        "order_number": "OMS-CANCEL9",
        "is_cancelled": False,
        "order_datetime": datetime.now(UTC),
        "currency": "INR",
        "payment_type": PaymentType.PREPAID,
        "payment_status": PaymentStatus.PENDING,
        "shipping_charge": Decimal("0"),
        "total_amount": Decimal("100.00"),
        "notes": None,
        "external_updated_at": None,
        "customer_external_id": None,
        "shipping_address": None,
        "billing_address": None,
        "items": [],
    }
    base.update(overrides)
    return base


async def test_shopify_auto_cancel_blocked_when_shipment_progressed(
    db_session: AsyncSession,
) -> None:
    order, _created = await OrderService(db_session).upsert_synced_order(
        **_shopify_sync_payload()
    )
    await OrderService(db_session).transition_status(
        order.id, new_status=OrderStatus.CONFIRMED, actor=None, description=None
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWBCANCEL9")
    await ShipmentService(db_session).update_shipment(
        shipment.id, actor=None, current_status=ShipmentStatus.IN_TRANSIT
    )

    # Simulates Shopify reporting this same order as cancelled via resync.
    synced, _created = await OrderService(db_session).upsert_synced_order(
        **_shopify_sync_payload(is_cancelled=True)
    )
    assert synced.status == OrderStatus.CONFIRMED  # never auto-cancelled

    audit = await db_session.execute(
        select(AuditLog).where(
            AuditLog.entity_id == str(order.id),
            AuditLog.action == "order.auto_cancel_blocked_shipment_progress",
        )
    )
    assert audit.scalar_one_or_none() is not None


# --- 9b. concurrent cancellation vs. confirmed dispatch (real Postgres only) -


@pytest.mark.skipif(
    not os.environ.get("OMS_TEST_DATABASE_URL"),
    reason="set OMS_TEST_DATABASE_URL to a disposable PostgreSQL to run",
)
async def test_concurrent_cancellation_vs_dispatch_serializes_via_order_lock(
    db_session: AsyncSession,
) -> None:
    """Two independent sessions: one cancels the order, the other confirms
    dispatch, at the same time. Whichever commits first is fully visible
    to the other (the `SELECT ... FOR UPDATE` on the `Order` row, taken by
    both `apply_dispatch` and `transition_status`, serializes them) -- the
    only invariant checked is the one the business rule actually requires:
    dispatch ALWAYS deducts stock exactly once, no matter which order the
    two transactions land in, and a dispatch that lands after a committed
    cancellation is flagged.
    """
    import asyncio

    from tests.conftest import _TestSessionLocal

    async with _TestSessionLocal() as session:
        variant = await _make_variant(session, sku="CANCELRACE", available_quantity=10)
        order = await _make_order_with_item(
            session, order_number="OMS-CANCELRACE", sku="CANCELRACE", quantity=2
        )
        shipment = await _make_shipment(session, order_id=order.id, awb="AWBCANCELRACE")
        order_id, shipment_id, variant_id = order.id, shipment.id, variant.id

    async def cancel() -> None:
        # The other side may have already progressed the shipment by the
        # time this transaction gets the order lock -- a refused
        # cancellation is the correct, expected outcome then, not a bug.
        with contextlib.suppress(ConflictError):
            async with _TestSessionLocal() as session:
                await OrderService(session).transition_status(
                    order_id, new_status=OrderStatus.CANCELLED, actor=None, description=None
                )

    async def dispatch() -> None:
        async with _TestSessionLocal() as session:
            await InventoryService(session).apply_dispatch(
                order_id=order_id, shipment_id=shipment_id
            )

    await asyncio.gather(cancel(), dispatch())

    async with _TestSessionLocal() as session:
        refreshed_variant = await session.get(ProductVariant, variant_id)
        total_movements = await session.execute(
            select(func.count())
            .select_from(InventoryMovement)
            .where(InventoryMovement.order_id == order_id)
        )
    # Deducted exactly once, regardless of interleaving.
    assert refreshed_variant.available_quantity == 8
    assert total_movements.scalar_one() == 1
