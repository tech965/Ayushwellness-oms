"""Real row-lock concurrency checks for the IMS on PostgreSQL.

Needs OMS_TEST_DATABASE_URL (a DISPOSABLE PostgreSQL); SQLite has no row
locks. Several independent sessions race for the same stock and the tests
assert stock is never overdrawn, never double-applied and the ledger always
reconciles with the balances.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from app.core.exceptions import ConflictError
from app.models.auth import User
from app.models.ims import ImsBalance, ImsItem, ImsLedger, ImsPackagingProduct, ImsTransfer
from app.models.inventory_architecture import Warehouse
from app.services.ims_service import IMSService
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


async def _seed(db_session: AsyncSession, stock: int):
    user = User(name="U", email=f"{uuid.uuid4().hex}@x.com", password_hash="x", is_active=True)
    a = Warehouse(code=uuid.uuid4().hex[:8], name="A", is_ims=True)
    b = Warehouse(code=uuid.uuid4().hex[:8], name="B", is_ims=True)
    prod = ImsPackagingProduct(name=uuid.uuid4().hex)
    db_session.add_all([user, a, b, prod])
    await db_session.flush()
    item = ImsItem(kind="packaging", name="P — BOX", packaging_product_id=prod.id, unit="BOX")
    db_session.add(item)
    await db_session.flush()
    await IMSService(db_session, user).stock_in({
        "idempotency_key": uuid.uuid4().hex, "warehouse_id": a.id, "item_id": item.id,
        "quantity": stock, "reason": "seed"})
    return user.id, a.id, b.id, item.id


async def _transfer(user_id, src, dst, item, qty, key=None):
    async with _TestSessionLocal() as session:
        user = await session.get(User, user_id)
        try:
            await IMSService(session, user).transfer({
                "idempotency_key": key or uuid.uuid4().hex, "source_warehouse_id": src,
                "destination_warehouse_id": dst, "lines": [{"item_id": item, "quantity": qty}]})
            return True
        except ConflictError:
            return False


async def _balance(db_session, wh, item) -> int:
    db_session.expire_all()
    return int((await db_session.execute(
        select(ImsBalance.quantity).where(ImsBalance.warehouse_id == wh, ImsBalance.item_id == item)
    )).scalar_one_or_none() or 0)


async def test_concurrent_transfers_never_overdraw(db_session: AsyncSession) -> None:
    user, a, b, item = await _seed(db_session, stock=100)
    results = await asyncio.gather(*[_transfer(user, a, b, item, 30) for _ in range(8)])
    assert sum(results) == 3  # 100 // 30
    assert await _balance(db_session, a, item) == 10
    assert await _balance(db_session, b, item) == 90
    ledger = (await db_session.execute(select(func.sum(ImsLedger.quantity_delta)))).scalar_one()
    assert ledger == 100


async def test_opposite_direction_transfers_do_not_deadlock(db_session: AsyncSession) -> None:
    user, a, b, item = await _seed(db_session, stock=100)
    await _transfer(user, a, b, item, 50)
    jobs = [_transfer(user, a, b, item, 1) if i % 2 else _transfer(user, b, a, item, 1)
            for i in range(20)]
    assert all(await asyncio.wait_for(asyncio.gather(*jobs), timeout=30))
    assert await _balance(db_session, a, item) + await _balance(db_session, b, item) == 100


async def test_same_idempotency_key_races_apply_once(db_session: AsyncSession) -> None:
    user, a, b, item = await _seed(db_session, stock=100)
    key = uuid.uuid4().hex
    await asyncio.gather(*[_transfer(user, a, b, item, 40, key=key) for _ in range(6)])
    assert await _balance(db_session, a, item) == 60
    count = (await db_session.execute(select(func.count()).select_from(ImsTransfer))).scalar_one()
    assert count == 1
