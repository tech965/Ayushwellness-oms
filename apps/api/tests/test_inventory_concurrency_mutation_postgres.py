"""Proves the PostgreSQL concurrency tests have teeth.

Each test disables ONE protection in a test-scoped way (monkeypatch for the
row lock, a DDL statement on the throw-away test database for a unique
constraint) and asserts that the invariant the real implementation upholds
is then VIOLATED. The production code is never edited: monkeypatches are
undone when the test ends and the test database is dropped after each test.

If one of these tests starts failing, the matching test in
`test_inventory_concurrency_postgres.py` no longer proves what it claims.
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
from app.repositories.product import ProductVariantRepository
from app.services import inventory_reservation_service as reservation_module
from app.services.inventory_reservation_service import InventoryReservationService, stock_position
from app.services.inventory_service import InventoryService
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import _TestSessionLocal

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("OMS_TEST_DATABASE_URL"),
        reason="set OMS_TEST_DATABASE_URL to a disposable PostgreSQL to run",
    ),
]

ATTEMPTS = 4  # races are probabilistic: the broken version must misbehave in at least one


async def _seed(db_session: AsyncSession, *, stock: int, orders: int = 1):
    product = Product(title="P", status="active")
    db_session.add(product)
    await db_session.flush()
    variant = ProductVariant(
        product_id=product.id, sku=f"M-{uuid.uuid4().hex[:6]}", title="V", price=1,
        available_quantity=stock, packets_per_box=1, pack_size=1, status="active",
    )
    db_session.add(variant)
    workflows = [OrderWorkflow(external_order_id=str(uuid.uuid4().int % 10**12)) for _ in range(orders)]
    db_session.add_all(workflows)
    await db_session.commit()
    return variant.id, [w.id for w in workflows], variant.sku


def _remove_row_locks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same code paths, minus FOR UPDATE (test-scoped)."""

    async def unlocked_get(self, id_):  # noqa: ANN001
        return await self.session.get(ProductVariant, id_, populate_existing=True)

    async def unlocked_lock_variants(session, variant_ids):  # noqa: ANN001
        rows = (
            (
                await session.execute(
                    select(ProductVariant)
                    .where(ProductVariant.id.in_(sorted(set(variant_ids))))
                    .order_by(ProductVariant.id)
                )
            )
            .scalars()
            .all()
        )
        return {v.id: v for v in rows}

    monkeypatch.setattr(ProductVariantRepository, "get_for_update", unlocked_get)
    monkeypatch.setattr(reservation_module, "lock_variants", unlocked_lock_variants)


async def test_without_row_locks_concurrent_reservations_oversell(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _remove_row_locks(monkeypatch)
    oversold = False
    for _ in range(ATTEMPTS):
        variant_id, order_ids, _ = await _seed(db_session, stock=5, orders=12)

        async def attempt(order_id: uuid.UUID, variant_id=variant_id) -> bool:
            async with _TestSessionLocal() as session:
                try:
                    await InventoryReservationService(session).reserve(order_id, [(variant_id, 1)])
                    await session.commit()
                    return True
                except ConflictError:
                    await session.rollback()
                    return False

        granted = await asyncio.gather(*(attempt(o) for o in order_ids))
        async with _TestSessionLocal() as session:
            position = await stock_position(session, variant_id)
        if sum(granted) > 5 or position.reserved > position.physical:
            oversold = True
            break
    assert oversold, "reservations did not oversell without locks: the lock test proves nothing"


async def test_without_row_locks_concurrent_additions_lose_updates(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _remove_row_locks(monkeypatch)
    lost = False
    for _ in range(ATTEMPTS):
        variant_id, _, _ = await _seed(db_session, stock=0)

        async def add(variant_id=variant_id) -> None:
            async with _TestSessionLocal() as session:
                await InventoryService(session).add_stock(
                    variant_id, quantity_to_add=1, reason="receiving", actor=None
                )

        await asyncio.gather(*(add() for _ in range(25)))
        async with _TestSessionLocal() as session:
            variant = await session.get(ProductVariant, variant_id)
        if variant.available_quantity != 25:
            lost = True
            break
    assert lost, "additions did not lose an update without locks: the lock test proves nothing"


async def _race_dispatches(db_session: AsyncSession, *, drop_constraint: bool) -> int:
    """Highest number of DISPATCH movements recorded for one order across
    `ATTEMPTS` races of five concurrent dispatches."""
    if drop_constraint:
        await db_session.execute(
            text(
                "ALTER TABLE inventory_movements "
                "DROP CONSTRAINT IF EXISTS uq_inventory_movements_variant_order_type"
            )
        )
        await db_session.commit()
    worst = 0
    for _ in range(ATTEMPTS):
        variant_id, [order_id], sku = await _seed(db_session, stock=10)
        items = [SimpleNamespace(quantity=4, sku=sku, product_variant_id=None, product_name="x")]

        async def dispatch(order_id=order_id, items=items) -> None:
            async with _TestSessionLocal() as session:
                await InventoryService(session).apply_dispatch(
                    order_id=order_id, shipment_id=None, items=items
                )

        await asyncio.gather(*(dispatch() for _ in range(5)))
        async with _TestSessionLocal() as session:
            movements = (
                await session.execute(
                    select(InventoryMovement).where(InventoryMovement.order_id == order_id)
                )
            ).scalars().all()
        worst = max(worst, len(movements))
    return worst


async def test_dispatch_replay_protection_has_two_independent_layers(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dispatch is protected by (a) the row lock + exists-check and (b) the
    unique constraint. Removing ONE layer must still leave a single deduction;
    removing BOTH must expose the double deduction (so the test can detect it)."""
    # Constraint dropped, lock present -> still exactly one.
    assert await _race_dispatches(db_session, drop_constraint=True) == 1
    # Lock removed as well -> the race is real and the test sees it.
    _remove_row_locks(monkeypatch)
    assert await _race_dispatches(db_session, drop_constraint=True) > 1, (
        "dispatch did not double-deduct with both protections removed: that test proves nothing"
    )


async def test_constraint_alone_still_blocks_double_deduction_without_the_lock(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _remove_row_locks(monkeypatch)
    assert await _race_dispatches(db_session, drop_constraint=False) == 1


async def test_sequential_replays_remain_idempotent_with_real_protections(
    db_session: AsyncSession,
) -> None:
    """Control: with the real lock and constraints, duplicate reserve, dispatch
    and release leave exactly one reservation, one deduction, and a clean state."""
    variant_id, [order_id], sku = await _seed(db_session, stock=10)
    items = [SimpleNamespace(quantity=4, sku=sku, product_variant_id=None, product_name="x")]

    async with _TestSessionLocal() as session:
        service = InventoryReservationService(session)
        for _ in range(3):
            await service.reserve(order_id, [(variant_id, 4)])
            await session.commit()
    async with _TestSessionLocal() as session:
        for _ in range(3):
            await InventoryService(session).apply_dispatch(
                order_id=order_id, shipment_id=None, items=items
            )
    async with _TestSessionLocal() as session:
        released_after_consume = await InventoryReservationService(session).release(order_id)
        await session.commit()

    async with _TestSessionLocal() as session:
        reservations = (await session.execute(select(InventoryReservation))).scalars().all()
        movements = (await session.execute(select(InventoryMovement))).scalars().all()
        variant = await session.get(ProductVariant, variant_id)
    assert len(reservations) == 1 and reservations[0].status == "consumed"
    assert len(movements) == 1 and variant.available_quantity == 6
    assert released_after_consume == 0  # a consumed reservation is never "released" back
