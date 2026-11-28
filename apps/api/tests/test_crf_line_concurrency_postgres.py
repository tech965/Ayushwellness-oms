"""Two different bank credits allocated to the SAME Shiprocket CRF line at the
same time on a REAL PostgreSQL: together they must never exceed the line.

Skipped unless `OMS_TEST_PG_ADMIN_URL` names a DISPOSABLE server (see
`test_remittance_concurrency_postgres.py`, whose fixtures and helpers are
reused). SQLite has no `SELECT ... FOR UPDATE`, so it cannot validate this.
The control test removes the locks and expects over-allocation, proving the
race is really exercised.
"""

# ruff: noqa: F811  (the imported `sessions` fixture is used as a test parameter)
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from app.core.exceptions import ValidationError
from app.models.auth import User
from app.models.remittance_reconciliation import BankCredit, BankCreditLink, CodRemittanceLine
from app.services import remittance_bank_service
from app.services.remittance_bank_service import BankStatementService, crf_line_reference
from sqlalchemy import select

from tests.telecalling_test_utils import make_user
from tests.test_remittance_concurrency_postgres import (  # noqa: F401  (fixture + helpers)
    ADMIN_URL,
    _hold_after_reading,
    _race,
    sessions,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not ADMIN_URL, reason="set OMS_TEST_PG_ADMIN_URL to a disposable PostgreSQL to run"
    ),
]

D = Decimal
LINE_AMOUNT = D("700.00")


async def _seed(
    sessions, *, preassigned: bool = False
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """`preassigned=True` seeds the line as it is after ANY earlier link: its
    internal reference already exists. Without it, the first link's reference
    assignment is an UPDATE of the line row, and PostgreSQL's own write lock on
    that row makes the two transactions queue even with NO explicit lock -- so
    only a pre-assigned line exercises the real race the explicit lock closes."""
    async with sessions() as db:
        user = await make_user(db, email="crf-race@example.com", is_superuser=True)
        line = CodRemittanceLine(
            partner_name="Shiprocket (CRF)",
            awb="AWB-RACE",
            remittance_amount=LINE_AMOUNT,
            dedupe_key="shiprocket_cod:crf-race-line",
        )
        if preassigned:
            line.remittance_reference = crf_line_reference(line)
        credits = [
            BankCredit(
                amount=D("700.00"),
                narration=f"NEFT AWB-RACE part {i}",
                reference=f"BNK-RACE-{i}",
                dedupe_key=f"crf-race-credit-{i}",
            )
            for i in (1, 2)
        ]
        db.add_all([line, *credits])
        await db.commit()
        return user.id, line.id, credits[0].id, credits[1].id


def _link(sessions, user_id, line_id, credit_id, amount):
    async def run():
        async with sessions() as db:
            user = await db.get(User, user_id)
            await BankStatementService(db).link_bank_credit_to_crf_line(
                line_id=line_id,
                bank_credit_id=credit_id,
                bank_reference="BANK-UTR",
                amount=D(amount),
                reason="Concurrency test",
                confirm_ambiguous=True,  # both credits carry the AWB
                user=user,
            )

    return run()


async def _line_total(sessions, line_id) -> Decimal:
    async with sessions() as db:
        ref = (await db.get(CodRemittanceLine, line_id)).remittance_reference
        rows = (
            await db.execute(
                select(BankCreditLink.amount).where(
                    BankCreditLink.reference == ref, BankCreditLink.reversed_at.is_(None)
                )
            )
        ).scalars()
        return sum(rows, D(0))


@pytest.mark.parametrize("preassigned", [False, True])
@pytest.mark.parametrize("attempt", range(3))
async def test_two_credits_cannot_over_allocate_one_crf_line(
    sessions, monkeypatch, attempt, preassigned
):
    user_id, line_id, c1, c2 = await _seed(sessions, preassigned=preassigned)
    _hold_after_reading(monkeypatch)
    results = await _race(
        _link(sessions, user_id, line_id, c1, "700"),
        _link(sessions, user_id, line_id, c2, "700"),
    )
    refused = [r for r in results if isinstance(r, BaseException)]
    assert len(refused) == 1 and isinstance(refused[0], ValidationError), results
    assert await _line_total(sessions, line_id) == LINE_AMOUNT


async def test_two_credits_that_fit_the_line_both_succeed(sessions, monkeypatch):
    user_id, line_id, c1, c2 = await _seed(sessions)
    _hold_after_reading(monkeypatch)
    results = await _race(
        _link(sessions, user_id, line_id, c1, "400"),
        _link(sessions, user_id, line_id, c2, "300"),
    )
    assert not [r for r in results if isinstance(r, BaseException)], results
    assert await _line_total(sessions, line_id) == LINE_AMOUNT


async def test_control_without_the_locks_the_line_is_over_allocated(sessions, monkeypatch):
    """If this ever stops over-allocating, the tests above prove nothing.

    Uses a line whose reference is already assigned (see `_seed`): otherwise the
    reference-assignment UPDATE serialises the two transactions by itself."""
    user_id, line_id, c1, c2 = await _seed(sessions, preassigned=True)
    _hold_after_reading(monkeypatch)

    async def no_lock(*_args, **_kwargs):
        return None

    monkeypatch.setattr(remittance_bank_service, "lock_crf_line", no_lock)
    monkeypatch.setattr(remittance_bank_service, "lock_bank_credits", no_lock)
    results = await _race(
        _link(sessions, user_id, line_id, c1, "700"),
        _link(sessions, user_id, line_id, c2, "700"),
    )
    assert not [r for r in results if isinstance(r, BaseException)], results
    assert await _line_total(sessions, line_id) == LINE_AMOUNT * 2


async def test_control_without_only_the_line_lock_the_line_is_over_allocated(
    sessions, monkeypatch
):
    """Isolates `lock_crf_line`: the credit locks stay ACTIVE (they cannot help,
    the two requests use different credits), so over-allocation here proves the
    line lock alone is what protects the line."""
    user_id, line_id, c1, c2 = await _seed(sessions, preassigned=True)
    _hold_after_reading(monkeypatch)

    async def no_lock(*_args, **_kwargs):
        return None

    monkeypatch.setattr(remittance_bank_service, "lock_crf_line", no_lock)
    assert remittance_bank_service.lock_bank_credits is not no_lock  # still real
    results = await _race(
        _link(sessions, user_id, line_id, c1, "700"),
        _link(sessions, user_id, line_id, c2, "700"),
    )
    assert not [r for r in results if isinstance(r, BaseException)], results
    assert await _line_total(sessions, line_id) == LINE_AMOUNT * 2
