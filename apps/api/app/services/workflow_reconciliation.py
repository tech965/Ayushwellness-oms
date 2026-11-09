"""Reconcile historical `orders` with `order_workflow` (OMS-owned state only).

Before live mode is enabled, `orders` is the authority for OMS-owned order
state and `order_workflow` mirrors it (migration backfill + the legacy
dual-write listeners). Drift can still exist -- e.g. rows changed between
the migration and the cut-over, or written by a path that bypassed the
listeners. This utility finds and (optionally) repairs that drift.

Guarantees:
  * Dry run by default; `apply=True` must be explicit.
  * Only these OMS-owned columns are ever copied (`MIRRORED_FIELDS`).
    No provider-owned field is read into, or written to, the workflow.
  * Missing workflow rows are created with the order's own id, so every
    existing foreign key keeps matching. Nothing is ever deleted.
  * Workflow rows with no `orders` row (live-only orders) are counted and
    left alone.
  * Idempotent: a second run reports zero changes.
  * Refuses to apply while live mode is on: from then on the workflow is
    the authority and copying `orders` over it would destroy newer state.
  * The report holds counts and order ids only -- no provider payloads.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ConflictError
from app.models.order import Order
from app.models.order_workflow import OrderWorkflow
from app.services.audit_service import AuditService

MIRRORED_FIELDS = (
    "status",
    "notes",
    "confirmed_by_telecaller_id",
    "confirmed_at",
    "shopify_confirmation_sync_status",
    "shopify_confirmation_sync_error",
    "shopify_confirmation_synced_at",
)

_SAMPLE_LIMIT = 25


@dataclass
class ReconciliationReport:
    applied: bool
    orders_checked: int = 0
    missing_workflow: int = 0
    mismatched: int = 0
    mismatches_by_field: dict[str, int] = field(default_factory=dict)
    workflow_without_order: int = 0
    created: int = 0
    updated: int = 0
    errors: list[str] = field(default_factory=list)
    sample_missing: list[str] = field(default_factory=list)
    sample_mismatched: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "applied": self.applied,
            "orders_checked": self.orders_checked,
            "missing_workflow": self.missing_workflow,
            "mismatched": self.mismatched,
            "mismatches_by_field": self.mismatches_by_field,
            "workflow_without_order": self.workflow_without_order,
            "created": self.created,
            "updated": self.updated,
            "errors": self.errors,
            "sample_missing": self.sample_missing,
            "sample_mismatched": self.sample_mismatched,
        }


def _external_key(order: Order) -> str:
    return order.shopify_order_id or order.external_id or f"local:{order.id}"


async def reconcile(
    session: AsyncSession, *, apply: bool = False, batch_size: int = 500
) -> ReconciliationReport:
    if apply and settings.SHOPIFY_LIVE_MODE:
        raise ConflictError(
            "Reconciliation cannot be applied while live-data mode is on: the workflow table is "
            "then the source of truth and would be overwritten with older order values."
        )

    report = ReconciliationReport(applied=apply)
    last_id: uuid.UUID | None = None
    taken_keys: set[str] = set()

    while True:
        stmt = select(Order).order_by(Order.id).limit(batch_size)
        if last_id is not None:
            stmt = stmt.where(Order.id > last_id)
        orders = list((await session.execute(stmt)).scalars().all())
        if not orders:
            break
        last_id = orders[-1].id
        workflows = {
            w.id: w
            for w in (
                await session.execute(
                    select(OrderWorkflow).where(OrderWorkflow.id.in_([o.id for o in orders]))
                )
            )
            .scalars()
            .all()
        }

        for order in orders:
            report.orders_checked += 1
            workflow = workflows.get(order.id)
            if workflow is None:
                report.missing_workflow += 1
                if len(report.sample_missing) < _SAMPLE_LIMIT:
                    report.sample_missing.append(str(order.id))
                if apply:
                    key = _external_key(order)
                    clash = await session.execute(
                        select(OrderWorkflow.id).where(OrderWorkflow.external_order_id == key)
                    )
                    if key in taken_keys or clash.first() is not None:
                        report.errors.append(
                            f"order {order.id}: external id already used by another workflow row"
                        )
                        continue
                    taken_keys.add(key)
                    session.add(
                        OrderWorkflow(
                            id=order.id,
                            source_system=order.source_system or "manual",
                            external_order_id=key,
                            **{name: getattr(order, name) for name in MIRRORED_FIELDS},
                        )
                    )
                    report.created += 1
                continue

            changed = [
                name for name in MIRRORED_FIELDS if getattr(order, name) != getattr(workflow, name)
            ]
            if not changed:
                continue
            report.mismatched += 1
            for name in changed:
                report.mismatches_by_field[name] = report.mismatches_by_field.get(name, 0) + 1
            if len(report.sample_mismatched) < _SAMPLE_LIMIT:
                report.sample_mismatched.append(str(order.id))
            if apply:
                for name in changed:
                    setattr(workflow, name, getattr(order, name))
                report.updated += 1

        if apply:
            await session.flush()

    order_ids = select(Order.id)
    orphan_rows = await session.execute(
        select(OrderWorkflow.id).where(OrderWorkflow.id.not_in(order_ids))
    )
    report.workflow_without_order = len(orphan_rows.all())

    if apply:
        await AuditService(session).record(
            user=None,
            action="workflow.reconciled",
            entity_type="order_workflow",
            entity_id="batch",
            new_value=None,
            metadata={
                "orders_checked": report.orders_checked,
                "created": report.created,
                "updated": report.updated,
                "errors": len(report.errors),
            },
        )
        await session.commit()
    return report
