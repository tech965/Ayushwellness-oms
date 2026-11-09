"""Shopify order refunds (read) and cancellation, via the live Shopify API.

Both operations are implemented from Shopify's current official Admin
GraphQL documentation (see the notes in `integrations/shopify/queries.py`
and `mutations.py`). They have NOT been run against a real store, and
refund CREATION is deliberately not offered (its input shape and the
version-dependent idempotency requirement are not verified).

Cancellation is asynchronous at Shopify: `POST .../shopify-cancel` only
submits the job; `GET .../shopify-cancel/status` reads the order's live
`cancelledAt` and, once Shopify confirms, records the OMS-side transition.
Nothing from Shopify's responses is stored.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.integrations.registry import get_adapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.models.auth import User
from app.models.enums import OrderStatus
from app.models.integration import IntegrationCode
from app.models.order import OrderEvent
from app.schemas.response import ApiResponse
from app.services.audit_service import AuditService
from app.services.live_orders import LOCAL_PREFIX
from app.services.order_ref import ensure_workflow, resolve_ref
from app.services.order_service import ORDER_STATUS_TRANSITIONS

router = APIRouter()

CancelReason = Literal["CUSTOMER", "DECLINED", "FRAUD", "INVENTORY", "OTHER", "STAFF"]


class ShopifyCancelRequest(BaseModel):
    reason: CancelReason
    restock: bool
    notify_customer: bool = False
    staff_note: str | None = Field(default=None, max_length=255)
    confirm: bool = Field(
        default=False,
        description="Must be true. Cancelling a customer's order in Shopify is not reversible.",
    )


def _adapter() -> ShopifyAdapter:
    adapter = get_adapter(IntegrationCode.SHOPIFY)
    if not isinstance(adapter, ShopifyAdapter):
        raise ConflictError("Shopify integration is not configured.")
    return adapter


async def _shopify_gid(session: AsyncSession, order_id: uuid.UUID) -> tuple[str, str]:
    ref = await resolve_ref(session, order_id)
    if ref is None or ref.external_order_id.startswith(LOCAL_PREFIX):
        raise NotFoundError("This order does not exist in Shopify.")
    return ref.external_order_id, f"gid://shopify/Order/{ref.external_order_id}"


@router.get("/{order_id}/refunds", response_model=ApiResponse[dict])
async def get_order_refunds(
    order_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("orders.read")),
) -> ApiResponse[dict[str, Any]]:
    """Refunds recorded on the Shopify order, read live."""
    _external_id, gid = await _shopify_gid(session, order_id)
    data = await _adapter().get_order_refunds(gid)
    if data is None:
        raise NotFoundError("Order not found in Shopify.")
    return ApiResponse(data=data)


@router.post("/{order_id}/shopify-cancel", response_model=ApiResponse[dict], status_code=202)
async def request_shopify_cancel(
    order_id: uuid.UUID,
    payload: ShopifyCancelRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("orders.cancel")),
) -> ApiResponse[dict[str, Any]]:
    """Submits Shopify's asynchronous order cancellation. Requires an
    explicit `confirm: true`; refuses an order Shopify already shows as
    cancelled. Poll `GET .../shopify-cancel/status` for confirmation."""
    if not payload.confirm:
        raise ValidationError("Set confirm=true to cancel this order in Shopify.")
    external_id, gid = await _shopify_gid(session, order_id)
    adapter = _adapter()
    current = await adapter.get_order_refunds(gid)
    if current is None:
        raise NotFoundError("Order not found in Shopify.")
    if current["cancelled_at"]:
        raise ConflictError("This order is already cancelled in Shopify.")

    job = await adapter.cancel_order(
        gid,
        reason=payload.reason,
        restock=payload.restock,
        notify_customer=payload.notify_customer,
        staff_note=payload.staff_note,
    )

    workflow = await ensure_workflow(session, external_id)
    session.add(
        OrderEvent(
            order_id=workflow.id,
            event_type="shopify_cancel_requested",
            status=None,
            description=f"Cancellation submitted to Shopify (reason {payload.reason}).",
            source="user",
            actor_user_id=current_user.id,
        )
    )
    await AuditService(session).record(
        user=current_user,
        action="order.shopify_cancel_requested",
        entity_type="order",
        entity_id=str(workflow.id),
        new_value={"reason": payload.reason, "restock": payload.restock},
    )
    await session.commit()
    return ApiResponse(
        data={
            "submitted": True,
            "confirmed": False,
            "job_id": job.get("id"),
            "done": job.get("done"),
        },
        message="Cancellation submitted. Check its status to confirm.",
    )


@router.get("/{order_id}/shopify-cancel/status", response_model=ApiResponse[dict])
async def get_shopify_cancel_status(
    order_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("orders.cancel")),
) -> ApiResponse[dict[str, Any]]:
    """Reads the order's live cancellation state. On first confirmation the
    OMS workflow moves to CANCELLED (if its transition rules allow it) and
    one activity event is recorded; repeat calls change nothing."""
    external_id, gid = await _shopify_gid(session, order_id)
    live = await _adapter().get_order_refunds(gid)
    if live is None:
        raise NotFoundError("Order not found in Shopify.")
    cancelled = bool(live["cancelled_at"])
    if cancelled:
        workflow = await ensure_workflow(session, external_id)
        already = (
            await session.execute(
                select(OrderEvent.id).where(
                    OrderEvent.order_id == workflow.id,
                    OrderEvent.event_type == "shopify_cancel_confirmed",
                )
            )
        ).first()
        if already is None:
            if OrderStatus.CANCELLED in ORDER_STATUS_TRANSITIONS.get(workflow.status, set()):
                workflow.status = OrderStatus.CANCELLED
            session.add(
                OrderEvent(
                    order_id=workflow.id,
                    event_type="shopify_cancel_confirmed",
                    status=OrderStatus.CANCELLED.value,
                    description="Shopify confirmed the cancellation.",
                    source="system",
                    actor_user_id=current_user.id,
                )
            )
            await session.commit()
    return ApiResponse(
        data={
            "cancelled": cancelled,
            "cancelled_at": live["cancelled_at"],
            "cancel_reason": live["cancel_reason"],
        }
    )
