"""Internal-workflow side effects of Shopify webhooks (live mode).

Each handler reads at most the order's numeric `id` and `cancelled_at`
from the delivery and writes ONLY `order_workflow` (external id + OMS
state). No payload field is stored. All handlers are idempotent: a
duplicate or concurrent delivery converges to the same row.

  orders/create, orders/updated -> ensure a workflow row exists
  orders/cancelled              -> move an existing workflow row to
                                   CANCELLED if the OMS transition rules
                                   allow it (never creates a row)
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.shopify.live_webhooks import register_live_handler
from app.models.enums import OrderStatus
from app.services.order_ref import ensure_workflow, get_workflow_by_external_ids
from app.services.order_service import ORDER_STATUS_TRANSITIONS


def _external_id(payload: dict[str, Any]) -> str | None:
    raw = payload.get("id")
    return str(raw) if isinstance(raw, (int, str)) and str(raw).isdigit() else None


async def on_order_upsert(session: AsyncSession, topic: str, payload: dict[str, Any]) -> None:
    external_id = _external_id(payload)
    if external_id is None:
        return
    try:
        async with session.begin_nested():
            await ensure_workflow(session, external_id)
    except IntegrityError:
        # Concurrent delivery created it first -- same end state.
        pass


async def on_order_cancelled(session: AsyncSession, topic: str, payload: dict[str, Any]) -> None:
    external_id = _external_id(payload)
    if external_id is None:
        return
    workflow = (await get_workflow_by_external_ids(session, [external_id])).get(external_id)
    if workflow is None or workflow.status == OrderStatus.CANCELLED:
        return
    if OrderStatus.CANCELLED in ORDER_STATUS_TRANSITIONS.get(workflow.status, set()):
        workflow.status = OrderStatus.CANCELLED


def register_default_live_handlers() -> None:
    register_live_handler("orders/create", on_order_upsert)
    register_live_handler("orders/updated", on_order_upsert)
    register_live_handler("orders/cancelled", on_order_cancelled)
