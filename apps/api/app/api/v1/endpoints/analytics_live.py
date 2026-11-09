"""Live (API-first) analytics: Shopify-derived metrics combined with
internal workflow state. Available in both modes; reads Shopify directly
and stores nothing."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.dependencies.auth import get_current_user, require_permission
from app.models.auth import User
from app.schemas.response import ApiResponse
from app.services.live_analytics_service import LiveAnalyticsSummary, build_summary
from app.services.live_orders import get_reader

router = APIRouter()


@router.get("/summary", response_model=ApiResponse[LiveAnalyticsSummary])
async def get_live_summary(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    max_pages: int = Query(default=20, ge=1, le=50, description="Shopify pages (100 orders each)"),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[LiveAnalyticsSummary]:
    summary = await build_summary(
        session, get_reader(), date_from=date_from, date_to=date_to, max_pages=max_pages
    )
    message = "Success" if summary.complete else "Incomplete: " + " ".join(summary.warnings)
    return ApiResponse(data=summary, message=message)


@router.get("/status", response_model=ApiResponse[dict])
async def get_live_mode_status(
    _: User = Depends(get_current_user),
) -> ApiResponse[dict[str, bool]]:
    """Whether the deployment serves provider data live (no local copy).
    Any signed-in user may read it; the UI uses it to pick live screens."""
    return ApiResponse(data={"live_mode": bool(settings.SHOPIFY_LIVE_MODE)})


# Operations the live architecture does NOT support, with the honest reason.
# The UI shows these instead of simulating success. Anything listed here has
# no verified provider API call in this integration.
UNSUPPORTED_LIVE_OPERATIONS: list[dict[str, str]] = [
    {
        "operation": "shopify_refund_creation",
        "reason": "Refund creation is not offered: Shopify's refundCreate input shape is not "
        "fully verified and its idempotency-key requirement changes by API version "
        "(optional in 2026-01, required from 2026-04). Refunds are readable per order.",
    },
    {
        "operation": "shiprocket_aggregates",
        "reason": "Shipment totals, courier performance and a standalone RTO list need a bulk "
        "Shiprocket status API that this integration does not have.",
    },
    {
        "operation": "cashfree_per_order_refunds",
        "reason": "No per-order Cashfree refund endpoint has been verified.",
    },
]


@router.get("/capabilities", response_model=ApiResponse[list[dict]])
async def get_live_capabilities(
    _: User = Depends(get_current_user),
) -> ApiResponse[list[dict[str, str]]]:
    """Operations that are intentionally unavailable in live-data mode."""
    return ApiResponse(data=UNSUPPORTED_LIVE_OPERATIONS)
