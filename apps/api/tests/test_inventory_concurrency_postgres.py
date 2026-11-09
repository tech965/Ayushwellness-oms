"""Real concurrency checks for stock on PostgreSQL.

Needs OMS_TEST_DATABASE_URL (a DISPOSABLE PostgreSQL) because SQLite has no
row locks: these tests open several independent sessions that race for the
same variant and assert the invariants that matter:

* stock is never reserved beyond what exists,
* concurrent additions never lose an update,
* a replayed / racing dispatch for one order deducts exactly once,
* two requests reserving for the same order never hold stock twice.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest
from app.core.exceptions import ConflictError
from app.models.inventory import InventoryMovement
from app.models.inventory_architecture import InventoryReservation
from app.models.order_workflow import OrderWorkflow
from app.models.product import Product, ProductVariant
from app.services.inventory_reservation_service import InventoryReservationService, stock_position
from app.services.inventory_service import InventoryService
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import _TestSessionLocal

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("OMS_TEST_DATABASE_URL"),
        reason="set OMS_TEST_DATABASE_URL to a disposable PostgreSQL to run",
    ),
]


async def _seed(db_session: AsyncSession, *, stock: int, orders: int = 1):
    product = Product(title="P", status="active")
    db_session.add(product)
    await db_session.flush()
    variant = ProductVariant(
        product_id=product.id, sku=f"C-{uuid.uuid4().hex[:6]}", title="V", price=1,
        available_quantity=stock, packets_per_box=1, pack_size=1, status="active",
    )
    db_session.add(variant)
    workflows = [OrderWorkflow(external_order_id=str(uuid.uuid4().int % 10**12)) for _ in range(orders)]
    db_session.add_all(workflows)
    await db_session.commit()
    return variant.id, [w.id for w in workflows], variant.sku


async def test_concurrent_reservations_never_oversell(db_session: AsyncSession) -> None:
    variant_id, order_ids, _ = await _seed(db_session, stock=5, orders=12)

    async def attempt(order_id: uuid.UUID) -> bool:
        async with _TestSessionLocal() as session:
            try:
                await InventoryReservationService(session).reserve(order_id, [(variant_id, 1)])
                await session.commit()
                return True
            except ConflictError:
                await session.rollback()
                return False

    results = await asyncio.gather(*(attempt(o) for o in order_ids))

    assert sum(results) == 5  # exactly the stock that exists, no more
    async with _TestSessionLocal() as session:
        position = await stock_position(session, variant_id)
    assert (position.physical, position.reserved, position.available_to_promise) == (5, 5, 0)


async def test_same_order_reserved_concurrently_holds_stock_once(db_session: AsyncSession) -> None:
    variant_id, [order_id], _ = await _seed(db_session, stock=10)

    async def attempt() -> None:
        async with _TestSessionLocal() as session:
            try:
                await InventoryReservationService(session).reserve(order_id, [(variant_id, 3)])
                await session.commit()
            except ConflictError:
                await session.rollback()

    await asyncio.gather(*(attempt() for _ in range(6)))

    async with _TestSessionLocal() as session:
        rows = (await session.execute(select(InventoryReservation))).scalars().all()
        position = await stock_position(session, variant_id)
    assert len(rows) == 1 and position.reserved == 3


async def test_concurrent_stock_additions_do_not_lose_updates(db_session: AsyncSession) -> None:
    variant_id, _, _ = await _seed(db_session, stock=0)

    async def add() -> None:
        async with _TestSessionLocal() as session:
            await InventoryService(session).add_stock(
                variant_id, quantity_to_add=1, reason="receiving", actor=None
            )

    await asyncio.gather(*(add() for _ in range(25)))

    async with _TestSessionLocal() as session:
        variant = await session.get(ProductVariant, variant_id)
        movements = (await session.execute(select(func.count()).select_from(InventoryMovement))).scalar_one()
    assert variant.available_quantity == 25 and movements == 25


async def test_racing_dispatch_for_one_order_deducts_exactly_once(db_session: AsyncSession) -> None:
    variant_id, [order_id], sku = await _seed(db_session, stock=10)
    async with _TestSessionLocal() as session:
        await InventoryReservationService(session).reserve(order_id, [(variant_id, 4)])
        await session.commit()
    items = [SimpleNamespace(quantity=4, sku=sku, product_variant_id=None, product_name="x")]

    async def dispatch() -> None:
        async with _TestSessionLocal() as session:
            await InventoryService(session).apply_dispatch(
                order_id=order_id, shipment_id=None, items=items
            )

    await asyncio.gather(*(dispatch() for _ in range(5)))

    async with _TestSessionLocal() as session:
        variant = await session.get(ProductVariant, variant_id)
        movements = (await session.execute(select(InventoryMovement))).scalars().all()
        reservation = (await session.execute(select(InventoryReservation))).scalar_one()
    assert variant.available_quantity == 6  # 10 - 4, once
    assert len(movements) == 1
    assert reservation.status == "consumed"


async def test_failed_reservation_rolls_back_every_line(db_session: AsyncSession) -> None:
    variant_id, [order_id], _ = await _seed(db_session, stock=3)
    product = Product(title="Q", status="active")
    db_session.add(product)
    await db_session.flush()
    scarce = ProductVariant(
        product_id=product.id, sku=f"S-{uuid.uuid4().hex[:6]}", title="V", price=1,
        available_quantity=0, packets_per_box=1, pack_size=1, status="active",
    )
    db_session.add(scarce)
    await db_session.commit()
    scarce_id = scarce.id

    async with _TestSessionLocal() as session:
        with pytest.raises(ConflictError):
            await InventoryReservationService(session).reserve(
                order_id, [(variant_id, 2), (scarce_id, 1)]
            )
        await session.rollback()

    async with _TestSessionLocal() as session:
        assert (await session.execute(select(func.count()).select_from(InventoryReservation))).scalar_one() == 0
        assert (await stock_position(session, variant_id)).available_to_promise == 3
