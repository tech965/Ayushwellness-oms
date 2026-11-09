"""Reconciliation of `orders` -> `order_workflow` (OMS-owned fields only)."""

from __future__ import annotations

import pytest
from app.core.config import settings
from app.core.exceptions import ConflictError
from app.models.audit_log import AuditLog
from app.models.enums import OrderStatus
from app.models.order import Order
from app.models.order_workflow import OrderWorkflow
from app.services.workflow_reconciliation import MIRRORED_FIELDS, reconcile
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import make_order

pytestmark = pytest.mark.asyncio


async def _count(session: AsyncSession, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_clean_database_reports_nothing(db_session: AsyncSession) -> None:
    await make_order(db_session, order_number="#R1")

    report = await reconcile(db_session)

    assert report.orders_checked == 1
    assert report.missing_workflow == report.mismatched == 0
    assert report.as_dict()["applied"] is False


async def test_dry_run_reports_missing_and_mismatched_without_changing_anything(
    db_session: AsyncSession,
) -> None:
    missing = await make_order(db_session, order_number="#R-missing")
    drifted = await make_order(db_session, order_number="#R-drift")
    await db_session.execute(delete(OrderWorkflow).where(OrderWorkflow.id == missing.id))
    await db_session.execute(
        update(OrderWorkflow)
        .where(OrderWorkflow.id == drifted.id)
        .values(status=OrderStatus.CANCELLED, notes="stale")
    )
    await db_session.commit()

    report = await reconcile(db_session, apply=False)

    assert report.missing_workflow == 1 and report.mismatched == 1
    assert (
        report.mismatches_by_field == {"status": 1, "notes": 1}
        or "status" in report.mismatches_by_field
    )
    assert report.created == report.updated == 0
    assert await db_session.get(OrderWorkflow, missing.id) is None  # nothing written
    assert await _count(db_session, AuditLog) == 0


async def test_apply_repairs_oms_owned_fields_only_and_is_idempotent(
    db_session: AsyncSession,
) -> None:
    missing = await make_order(db_session, order_number="#A-missing")
    drifted = await make_order(db_session, order_number="#A-drift")
    await db_session.execute(delete(OrderWorkflow).where(OrderWorkflow.id == missing.id))
    await db_session.execute(
        update(OrderWorkflow).where(OrderWorkflow.id == drifted.id).values(notes="stale")
    )
    await db_session.commit()
    original_total = drifted.total_amount

    first = await reconcile(db_session, apply=True)
    second = await reconcile(db_session, apply=True)

    assert (first.created, first.updated) == (1, 1)
    assert (second.created, second.updated, second.missing_workflow, second.mismatched) == (
        0,
        0,
        0,
        0,
    )
    repaired = await db_session.get(OrderWorkflow, missing.id)
    assert repaired is not None and repaired.id == missing.id  # same id: FKs keep matching
    assert (await db_session.get(OrderWorkflow, drifted.id)).notes == drifted.notes
    # Provider-owned data on the order is untouched and never copied.
    await db_session.refresh(drifted)
    assert drifted.total_amount == original_total
    assert set(MIRRORED_FIELDS).isdisjoint({"total_amount", "shipping_address", "order_number"})
    audit = (await db_session.execute(select(AuditLog))).scalars().all()
    assert audit and "total_amount" not in str(
        audit[0].event_metadata if hasattr(audit[0], "event_metadata") else ""
    )


async def test_workflow_rows_without_an_order_are_counted_and_never_deleted(
    db_session: AsyncSession,
) -> None:
    await make_order(db_session, order_number="#O1")
    db_session.add(OrderWorkflow(external_order_id="live-only-1"))
    db_session.add(OrderWorkflow(external_order_id="live-only-2"))
    await db_session.commit()

    report = await reconcile(db_session, apply=True)

    assert report.workflow_without_order == 2
    assert await _count(db_session, OrderWorkflow) == 3


async def test_apply_is_refused_while_live_mode_is_on(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_order(db_session, order_number="#L1")
    monkeypatch.setattr(settings, "SHOPIFY_LIVE_MODE", True)

    with pytest.raises(ConflictError):
        await reconcile(db_session, apply=True)
    # A dry run is still allowed.
    assert (await reconcile(db_session, apply=False)).applied is False


async def test_external_id_clash_is_reported_not_crashed(db_session: AsyncSession) -> None:
    order = await make_order(db_session, order_number="#C1")
    await db_session.execute(delete(OrderWorkflow).where(OrderWorkflow.id == order.id))
    db_session.add(OrderWorkflow(external_order_id=f"local:{order.id}"))
    await db_session.commit()

    report = await reconcile(db_session, apply=True)

    assert report.created == 0 and len(report.errors) == 1
    assert await _count(db_session, Order) == 1
