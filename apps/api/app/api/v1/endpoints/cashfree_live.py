"""Live Cashfree reads (no provider data stored)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.integrations.cashfree.config import CashfreeConfig
from app.models.auth import User
from app.repositories.order import OrderRepository
from app.schemas.response import ApiResponse
from app.services.live_cashfree import MAX_PAGES, LiveCashfreeService

router = APIRouter()


def _range(date_from: datetime | None, date_to: datetime | None) -> tuple[datetime, datetime]:
    end = date_to or datetime.now(UTC)
    return (date_from or end - timedelta(days=7)), end


@router.get("/orders/{order_id}", response_model=ApiResponse[dict])
async def get_live_order_payment(
    order_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("payments.read")),
) -> ApiResponse[dict[str, Any]]:
    """Payment details for one order, read from Cashfree, plus a read-only
    verification against the payment status Shopify reports."""
    order = await OrderRepository(session).get_by_id(order_id)
    if order is None:
        raise NotFoundError("Order not found.")
    result = await LiveCashfreeService().verify(order.order_number, order.payment_status)
    return ApiResponse(data=result)


@router.get("/transactions", response_model=ApiResponse[dict])
async def list_live_transactions(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    max_pages: int = Query(default=MAX_PAGES, ge=1, le=50),
    _: User = Depends(require_permission("payments.read")),
) -> ApiResponse[dict[str, Any]]:
    start, end = _range(date_from, date_to)
    return ApiResponse(
        data=await LiveCashfreeService().transactions(start, end, max_pages=max_pages)
    )


@router.get("/refunds", response_model=ApiResponse[dict])
async def list_live_refunds(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    max_pages: int = Query(default=MAX_PAGES, ge=1, le=50),
    _: User = Depends(require_permission("payments.read")),
) -> ApiResponse[dict[str, Any]]:
    start, end = _range(date_from, date_to)
    return ApiResponse(data=await LiveCashfreeService().refunds(start, end, max_pages=max_pages))


@router.get("/settlements", response_model=ApiResponse[dict])
async def list_live_settlements(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    max_pages: int = Query(default=MAX_PAGES, ge=1, le=50),
    _: User = Depends(require_permission("payments.read")),
) -> ApiResponse[dict[str, Any]]:
    start, end = _range(date_from, date_to)
    return ApiResponse(
        data=await LiveCashfreeService().settlements(start, end, max_pages=max_pages)
    )


@router.get("/summary", response_model=ApiResponse[dict])
async def get_live_payment_summary(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    max_pages: int = Query(default=MAX_PAGES, ge=1, le=50),
    _: User = Depends(require_permission("payments.read")),
) -> ApiResponse[dict[str, Any]]:
    start, end = _range(date_from, date_to)
    return ApiResponse(
        data=await LiveCashfreeService().payment_received_summary(start, end, max_pages=max_pages)
    )


@router.post("/orders/{order_id}/checkout", response_model=ApiResponse[dict])
async def create_live_checkout(
    order_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("payments.create")),
) -> ApiResponse[dict[str, Any]]:
    """Creates (or reuses) a Cashfree checkout without storing any payment
    record. The `payment_session_id` is returned to the caller only."""
    order = await OrderRepository(session).get_by_id_with_items_and_customer(order_id)
    if order is None:
        raise NotFoundError("Order not found.")
    config = CashfreeConfig.from_settings()
    return_url = (
        config.return_url.replace("{order_id}", str(order.id))
        if config and config.return_url
        else None
    )
    result = await LiveCashfreeService().create_checkout(order, return_url=return_url)
    mode = config.environment if config is not None else "sandbox"
    return ApiResponse(
        data={**result, "mode": mode},
        message="Cashfree checkout session ready."
        if result["created"]
        else "Existing session reused.",
    )
