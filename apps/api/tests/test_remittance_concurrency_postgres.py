"""Two allocations of the SAME bank credit running at the same time on a REAL
PostgreSQL: together they must never exceed the credit.

SQLite cannot validate this (no `SELECT ... FOR UPDATE`), so these tests are
skipped unless `OMS_TEST_PG_ADMIN_URL` explicitly names a DISPOSABLE server,
e.g. postgresql://postgres@127.0.0.1:5432/postgres (the same variable as
`test_migrations_postgres.py`; the role needs CREATEDB). Each test creates its
own randomly named database and drops it. No other database setting is ever
read; never point this at a shared or production server.

The overlap is forced: after reading how much of the credit is used, each
transaction waits, so without row locks both would see it as free. The control
test removes the lock and expects over-allocation, which proves the race is
really exercised.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit

import app.models  # noqa: F401  (register every model)
import pytest
import pytest_asyncio
from app.core.exceptions import ValidationError
from app.db.base import Base
from app.models.auth import User
from app.models.cod_remittance import CodRemittance
from app.models.remittance_reconciliation import (
    BankCredit,
    BankCreditLink,
    CodRemittanceLine,
    GatewaySettlementLine,
)
from app.services import cod_reconciliation_service, remittance_bank_service, remittance_common
from app.services.cod_reconciliation_service import CodReconciliationService
from app.services.remittance_bank_service import BankStatementService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.telecalling_test_utils import make_user

ADMIN_URL = os.environ.get("OMS_TEST_PG_ADMIN_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not ADMIN_URL, reason="set OMS_TEST_PG_ADMIN_URL to a disposable PostgreSQL to run"
    ),
]

D = Decimal
CREDIT = D("700.00")
HOLD_SECONDS = 0.6


@pytest_asyncio.fixture
async def sessions():
    import asyncpg

    parts = urlsplit(ADMIN_URL.replace("postgresql+asyncpg://", "postgresql://"))
    name = f"oms_race_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(urlunsplit(parts))
    await admin.execute(f'create database "{name}"')
    url = urlunsplit(("postgresql+asyncpg", parts.netloc, f"/{name}", parts.query, ""))
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        await admin.execute(f'drop database if exists "{name}" with (force)')
        await admin.close()


async def _seed(sessions, *, credit_reference="STMT-1") -> tuple[uuid.UUID, uuid.UUID]:
    async with sessions() as db:
        user = await make_user(db, email="race@example.com", is_superuser=True)
        credit = BankCredit(amount=CREDIT, reference=credit_reference, dedupe_key="race-credit")
        db.add(credit)
        for ref in ("RA", "RB"):
            db.add(
                CodRemittanceLine(
                    partner_name="Delhivery",
                    remittance_reference=ref,
                    remittance_amount=CREDIT,
                    awb=f"AWB-{ref}",
                    dedupe_key=f"line-{ref}",
                )
            )
        await db.commit()
        return user.id, credit.id


def _hold_after_reading(monkeypatch) -> None:
    """Widen the window between "how much is free?" and "write the allocation"."""
    real = remittance_common.load_ledger

    async def slow(*args, **kwargs):
        ledger = await real(*args, **kwargs)
        await asyncio.sleep(HOLD_SECONDS)
        return ledger

    for module in (remittance_common, remittance_bank_service, cod_reconciliation_service):
        monkeypatch.setattr(module, "load_ledger", slow)


def _manual_link(sessions, user_id, credit_id, reference, amount):
    async def run():
        async with sessions() as db:
            user = await db.get(User, user_id)
            await BankStatementService(db).link_bank_credit(
                module="cod",
                bank_credit_id=credit_id,
                reference=reference,
                bank_reference="BANK-UTR",
                amount=D(amount),
                reason="Concurrency test",
                user=user,
            )

    return run()


async def _linked_total(sessions, credit_id) -> Decimal:
    async with sessions() as db:
        rows = (
            await db.execute(
                select(BankCreditLink.amount).where(
                    BankCreditLink.bank_credit_id == credit_id,
                    BankCreditLink.reversed_at.is_(None),
                )
            )
        ).scalars()
        return sum(rows, D(0))


async def _race(*calls) -> list:
    return await asyncio.wait_for(asyncio.gather(*calls, return_exceptions=True), timeout=60)


def _only_one_refused(results: list) -> None:
    refused = [r for r in results if isinstance(r, BaseException)]
    assert len(refused) == 1 and isinstance(refused[0], ValidationError), results
    assert "unallocated" in str(refused[0])


@pytest.mark.parametrize("attempt", range(3))
async def test_two_simultaneous_manual_links_cannot_exceed_the_credit(
    sessions, monkeypatch, attempt
):
    user_id, credit_id = await _seed(sessions)
    _hold_after_reading(monkeypatch)
    results = await _race(
        _manual_link(sessions, user_id, credit_id, "RA", "700"),
        _manual_link(sessions, user_id, credit_id, "RB", "700"),
    )
    _only_one_refused(results)
    assert await _linked_total(sessions, credit_id) == CREDIT


async def test_two_simultaneous_links_that_fit_both_succeed(sessions, monkeypatch):
    user_id, credit_id = await _seed(sessions)
    _hold_after_reading(monkeypatch)
    results = await _race(
        _manual_link(sessions, user_id, credit_id, "RA", "400"),
        _manual_link(sessions, user_id, credit_id, "RB", "300"),
    )
    assert not [r for r in results if isinstance(r, BaseException)], results
    assert await _linked_total(sessions, credit_id) == CREDIT


async def test_cod_and_prepaid_auto_links_cannot_both_spend_the_credit(sessions, monkeypatch):
    _user_id, credit_id = await _seed(sessions, credit_reference="RA")
    async with sessions() as db:
        db.add(
            GatewaySettlementLine(
                gateway="Razorpay",
                settlement_reference="RA",
                net_amount=CREDIT,
                line_status="success",
                dedupe_key="race-gateway",
            )
        )
        await db.commit()
    _hold_after_reading(monkeypatch)

    async def auto(module):
        async with sessions() as db:
            made = await remittance_common.auto_link_bank_credits(db, module, ["RA"], user=None)
            await db.commit()
            return made

    results = await _race(auto("cod"), auto("prepaid"))
    assert sorted(results) == [0, 1], results
    assert await _linked_total(sessions, credit_id) == CREDIT


async def test_manual_link_and_shiprocket_verification_cannot_share_the_money(
    sessions, monkeypatch
):
    user_id, credit_id = await _seed(sessions, credit_reference="SR-UTR")
    async with sessions() as db:
        db.add(CodRemittance(awb="AWB-SR", remitted_amount=CREDIT))
        await db.commit()
    _hold_after_reading(monkeypatch)

    async def verify():
        async with sessions() as db:
            user = await db.get(User, user_id)
            await CodReconciliationService(db).verify_bank_credit(
                "AWB-SR",
                amount=CREDIT,
                reference="SR-UTR",
                credited_on=datetime.now(UTC),
                reason="Concurrency test",
                user=user,
            )

    results = await _race(_manual_link(sessions, user_id, credit_id, "RA", "700"), verify())
    _only_one_refused(results)
    async with sessions() as db:
        ledger = await remittance_common.load_ledger(db)
        credit = await db.get(BankCredit, credit_id)
        used = ledger.linked.get(credit_id, D(0)) + ledger.claimed.get(credit_id, D(0))
        verified = (await db.execute(select(CodRemittance.bank_credit_amount))).scalars()
        stored = sum((amount or D(0) for amount in verified), D(0))
    assert used <= credit.amount
    assert await _linked_total(sessions, credit_id) + stored == CREDIT


async def test_control_without_the_row_lock_the_race_does_over_allocate(sessions, monkeypatch):
    """If this ever stops over-allocating, the tests above are not proving anything."""
    user_id, credit_id = await _seed(sessions)
    _hold_after_reading(monkeypatch)

    async def no_lock(*_args, **_kwargs):
        return None

    monkeypatch.setattr(remittance_bank_service, "lock_bank_credits", no_lock)
    results = await _race(
        _manual_link(sessions, user_id, credit_id, "RA", "700"),
        _manual_link(sessions, user_id, credit_id, "RB", "700"),
    )
    assert not [r for r in results if isinstance(r, BaseException)], results
    assert await _linked_total(sessions, credit_id) == CREDIT * 2
