"""Order reference ids for API-first (live) mode.

The UI identifies an order by a UUID. A live Shopify order has no local
row to supply one, so the UUID is derived *reversibly* from the numeric
Shopify order id: a fixed 64-bit tag in the high half, the id in the low
half. That lets any endpoint turn the UUID back into the external id
with no database lookup and no write-on-read.

Orders that already have an `order_workflow` row (backfilled from the
legacy `orders` table) keep that row's id instead, so existing
assignments/call logs keep matching -- `resolve_ref` checks the table
first.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checkout_workflow import CheckoutWorkflow
from app.models.order_workflow import OrderWorkflow

_TAG = 0x53484F5049465900  # ASCII "SHOPIFY" followed by a NUL byte
_MAX_ID = (1 << 64) - 1


def encode_shopify_order_ref(external_order_id: str) -> uuid.UUID:
    value = int(external_order_id)
    if not 0 <= value <= _MAX_ID:
        raise ValueError("Shopify order id out of range.")
    return uuid.UUID(int=(_TAG << 64) | value)


def decode_shopify_order_ref(ref: uuid.UUID) -> str | None:
    if (ref.int >> 64) != _TAG:
        return None
    return str(ref.int & _MAX_ID)


@dataclass(frozen=True)
class OrderRef:
    ref_id: uuid.UUID
    external_order_id: str
    workflow: OrderWorkflow | None


async def get_workflow_by_external_ids(
    session: AsyncSession, external_ids: list[str]
) -> dict[str, OrderWorkflow]:
    if not external_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(OrderWorkflow).where(OrderWorkflow.external_order_id.in_(external_ids))
            )
        )
        .scalars()
        .all()
    )
    return {w.external_order_id: w for w in rows}


def ref_id_for(external_order_id: str, workflow: OrderWorkflow | None) -> uuid.UUID:
    return workflow.id if workflow is not None else encode_shopify_order_ref(external_order_id)


async def resolve_ref(session: AsyncSession, ref_id: uuid.UUID) -> OrderRef | None:
    """Reference UUID -> external order id, or None if it is neither a
    workflow row nor an encoded live-order reference."""
    workflow = await session.get(OrderWorkflow, ref_id)
    if workflow is not None:
        return OrderRef(workflow.id, workflow.external_order_id, workflow)
    external = decode_shopify_order_ref(ref_id)
    if external is None:
        return None
    existing = (await get_workflow_by_external_ids(session, [external])).get(external)
    return OrderRef(existing.id if existing else ref_id, external, existing)


async def ensure_workflow(session: AsyncSession, external_order_id: str) -> OrderWorkflow:
    """Idempotent: returns the existing row or creates one holding only
    the external id and default OMS state. Caller commits."""
    existing = (await get_workflow_by_external_ids(session, [external_order_id])).get(
        external_order_id
    )
    if existing is not None:
        return existing
    workflow = OrderWorkflow(
        id=encode_shopify_order_ref(external_order_id), external_order_id=external_order_id
    )
    session.add(workflow)
    await session.flush()
    return workflow


# --- Abandoned checkouts -----------------------------------------------------------------
# Same reversible-encoding idea as orders, with its own tag so an order
# reference can never be mistaken for a checkout reference (or vice versa).

_CHECKOUT_TAG = 0x53484F5043484B00  # ASCII "SHOPCHK" followed by a NUL byte


def encode_checkout_ref(external_checkout_id: str) -> uuid.UUID:
    value = int(external_checkout_id)
    if not 0 <= value <= _MAX_ID:
        raise ValueError("Shopify checkout id out of range.")
    return uuid.UUID(int=(_CHECKOUT_TAG << 64) | value)


def decode_checkout_ref(ref: uuid.UUID) -> str | None:
    if (ref.int >> 64) != _CHECKOUT_TAG:
        return None
    return str(ref.int & _MAX_ID)


async def get_checkout_workflows_by_external_ids(
    session: AsyncSession, external_ids: list[str]
) -> dict[str, CheckoutWorkflow]:
    if not external_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(CheckoutWorkflow).where(
                    CheckoutWorkflow.external_checkout_id.in_(external_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    return {w.external_checkout_id: w for w in rows}


def checkout_ref_id_for(external_id: str, workflow: CheckoutWorkflow | None) -> uuid.UUID:
    return workflow.id if workflow is not None else encode_checkout_ref(external_id)


async def resolve_checkout_ref(
    session: AsyncSession, ref_id: uuid.UUID
) -> tuple[str, CheckoutWorkflow | None] | None:
    """Reference UUID -> (external checkout id, workflow row or None)."""
    workflow = await session.get(CheckoutWorkflow, ref_id)
    if workflow is not None:
        return workflow.external_checkout_id, workflow
    external = decode_checkout_ref(ref_id)
    if external is None:
        return None
    existing = (await get_checkout_workflows_by_external_ids(session, [external])).get(external)
    return external, existing


async def ensure_checkout_workflow(session: AsyncSession, external_id: str) -> CheckoutWorkflow:
    existing = (await get_checkout_workflows_by_external_ids(session, [external_id])).get(
        external_id
    )
    if existing is not None:
        return existing
    workflow = CheckoutWorkflow(
        id=encode_checkout_ref(external_id), external_checkout_id=external_id
    )
    session.add(workflow)
    await session.flush()
    return workflow
